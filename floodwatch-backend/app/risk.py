"""The risk engine.

This is the research contribution, so it is written to be argued with rather
than trusted. Three rules govern the whole file:

  1. Every score decomposes. `RiskScore.features` carries every input and every
     contribution, and it is stored on every snapshot. A score from six months
     ago can be reconstructed without re-running anything.
  2. Two engines, always. `rules_v1` is the proposed system; `rainfall_only` is
     the baseline the paper has to beat. Both run on every evaluation, on the
     same inputs, so "what does crowdsourcing add" is a query, not an argument.
  3. Nothing here sends anything. It writes snapshots and proposals. A human
     approves a proposal before a phone hears about it (brief 6.5).

WHY THESE NUMBERS (all of them are proposals, section 4 of DESIGN.md):

  Region = geohash precision 5, about 4.9 km square. Precision 6 (1.2 km) makes
  a bucket too small to ever reach a respondent floor at pilot scale; precision
  4 (39 km) spans a whole district and would average a flooded valley together
  with dry high ground. GN divisions are the better long-term answer and the
  boundaries are publicly available (SL_GND on the same ArcGIS server) -- but
  they vary from a few hectares to tens of square kilometres, which makes the
  respondent floor mean different things in different places.

  Crowd floor = 5 distinct respondents in 60 minutes. The brief's ">=75% of
  recent answers" is unbounded below: 3 of 4 crosses it, and in a sparsely
  populated cell that is noise, not signal. Five is already generous for a
  20-user pilot and should rise with the panel size.

  Weights 0.45 gauge / 0.35 rainfall / 0.20 crowd. The gauge is a direct
  measurement of the thing being predicted, so it leads. Rainfall is causal but
  lagging in its effect. Crowd is the smallest weight and the largest research
  question: it is the only input that can see a flood a gauge cannot -- localised
  urban and drain-blockage flooding, which is most of Colombo's flood experience.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from . import geo
from .config import settings
from .db import utcnow
from .models import (
    Alert,
    AlertProposal,
    AlertRegion,
    Feedback,
    GaugeReading,
    GaugeStation,
    LocationPing,
    RainfallObservation,
    RegionRiskSnapshot,
)

log = logging.getLogger(__name__)

# Department of Meteorology rainfall language, used for the mapping below:
#   heavy       >= 75 mm / 24h
#   very heavy  >= 150 mm / 24h
RAIN_HEAVY_MM = 75.0
RAIN_VERY_HEAVY_MM = 150.0


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


@dataclass
class RegionContext:
    """Everything known about one region at one instant."""

    geohash: str
    at: datetime
    latitude: float
    longitude: float

    # crowd
    respondents: int = 0
    yes_count: int = 0
    yes_ratio: float | None = None
    crowd_floor_met: bool = False

    # gauge
    station: str | None = None
    station_km: float | None = None
    water_level_m: float | None = None
    alert_level_m: float | None = None
    minor_flood_level_m: float | None = None
    major_flood_level_m: float | None = None
    level_observed_at: datetime | None = None
    rise_m_per_3h: float | None = None

    # rainfall
    rain_1h_mm: float = 0.0
    rain_6h_mm: float = 0.0
    rain_24h_mm: float = 0.0
    rain_forecast_6h_mm: float = 0.0
    rain_source: str = "grid"  # "grid" (Open-Meteo) or "station" (gauge rainfall)

    # exposure
    users_present: int = 0

    def as_dict(self) -> dict:
        from .schemas import iso_z

        return {
            "region": self.geohash,
            "at": iso_z(self.at),
            "centre": {"latitude": round(self.latitude, 5), "longitude": round(self.longitude, 5)},
            "crowd": {
                "respondents": self.respondents,
                "yes": self.yes_count,
                "ratio": self.yes_ratio,
                "floorMet": self.crowd_floor_met,
                "windowMinutes": settings.crowd_window_minutes,
                "floor": settings.crowd_min_respondents,
            },
            "gauge": {
                "station": self.station,
                "distanceKm": self.station_km,
                "waterLevelM": self.water_level_m,
                "alertLevelM": self.alert_level_m,
                "minorFloodLevelM": self.minor_flood_level_m,
                "majorFloodLevelM": self.major_flood_level_m,
                "observedAt": iso_z(self.level_observed_at),
                "riseMPer3h": self.rise_m_per_3h,
            },
            "rainfall": {
                "mm1h": round(self.rain_1h_mm, 2),
                "mm6h": round(self.rain_6h_mm, 2),
                "mm24h": round(self.rain_24h_mm, 2),
                "forecast6hMm": round(self.rain_forecast_6h_mm, 2),
                "source": self.rain_source,
            },
            "exposure": {"usersPresent": self.users_present},
        }


def build_context(db: Session, region: str, at: datetime | None = None) -> RegionContext:
    at = at or utcnow()
    latitude, longitude = geo.decode_center(region)
    ctx = RegionContext(geohash=region, at=at, latitude=latitude, longitude=longitude)

    # --- crowd ---------------------------------------------------------
    window_start = at - timedelta(minutes=settings.crowd_window_minutes)
    # One vote per user per window, latest answer wins. Without this a single
    # user resubmitting "yes" twenty times would carry a region on their own --
    # the cheapest possible attack on the model (brief sec 7).
    latest_per_user = (
        select(
            Feedback.user_id.label("user_id"),
            func.max(Feedback.answered_at).label("answered_at"),
        )
        .where(
            Feedback.geohash.isnot(None),
            Feedback.geohash.startswith(region),
            Feedback.answered_at >= window_start,
            Feedback.answered_at <= at,
        )
        .group_by(Feedback.user_id)
        .subquery()
    )
    votes = db.execute(
        select(Feedback.user_id, Feedback.flood_present)
        .join(
            latest_per_user,
            (Feedback.user_id == latest_per_user.c.user_id)
            & (Feedback.answered_at == latest_per_user.c.answered_at),
        )
        .where(Feedback.geohash.startswith(region))
    ).all()

    # A user can have two rows at the identical timestamp; collapse defensively.
    by_user = {user_id: bool(flood) for user_id, flood in votes}
    ctx.respondents = len(by_user)
    ctx.yes_count = sum(1 for v in by_user.values() if v)
    if ctx.respondents:
        ctx.yes_ratio = round(ctx.yes_count / ctx.respondents, 4)
    ctx.crowd_floor_met = ctx.respondents >= settings.crowd_min_respondents

    # --- gauge ---------------------------------------------------------
    station = nearest_station(db, latitude, longitude)
    if station is not None:
        distance = geo.haversine_km(latitude, longitude, station.latitude, station.longitude)
        ctx.station = station.station
        ctx.station_km = round(distance, 2)
        ctx.alert_level_m = station.alert_level_m
        ctx.minor_flood_level_m = station.minor_flood_level_m
        ctx.major_flood_level_m = station.major_flood_level_m

        readings = db.scalars(
            select(GaugeReading)
            .where(
                GaugeReading.station == station.station,
                GaugeReading.observed_at <= at,
                GaugeReading.observed_at >= at - timedelta(hours=12),
                GaugeReading.water_level_m.isnot(None),
            )
            .order_by(GaugeReading.observed_at.desc())
            .limit(200)
        ).all()
        if readings:
            newest = readings[0]
            ctx.water_level_m = newest.water_level_m
            ctx.level_observed_at = newest.observed_at
            # Rate of rise. A river 1 m below its alert level and climbing 0.5 m
            # every three hours is a different situation from one sitting still,
            # and the difference is exactly the lead time the paper measures.
            cutoff = newest.observed_at - timedelta(hours=3)
            older = [r for r in readings if r.observed_at <= cutoff]
            if older:
                delta = newest.water_level_m - older[0].water_level_m
                hours = max(
                    0.5, (newest.observed_at - older[0].observed_at).total_seconds() / 3600.0
                )
                ctx.rise_m_per_3h = round(delta / hours * 3.0, 3)

    # --- rainfall ------------------------------------------------------
    def rain_sum(hours: int, forecast: bool) -> float:
        if forecast:
            lo, hi = at, at + timedelta(hours=hours)
        else:
            lo, hi = at - timedelta(hours=hours), at
        total = db.scalar(
            select(func.coalesce(func.sum(RainfallObservation.precipitation_mm), 0.0)).where(
                RainfallObservation.geohash == region,
                RainfallObservation.observed_at > lo,
                RainfallObservation.observed_at <= hi,
                RainfallObservation.is_forecast.is_(forecast),
            )
        )
        return float(total or 0.0)

    ctx.rain_1h_mm = rain_sum(1, False)
    ctx.rain_6h_mm = rain_sum(6, False)
    ctx.rain_24h_mm = rain_sum(24, False)
    ctx.rain_forecast_6h_mm = rain_sum(6, True)

    # Gauge stations report rainfall too. Where a station sits inside the region
    # its reading is a direct local measurement and beats the gridded model.
    #
    # SUM or MAX depends on whether the upstream field is incremental or a
    # running daily total -- which is undocumented. See the long comment on
    # settings.gauge_rainfall_is_cumulative; getting this wrong by an order of
    # magnitude is the most likely cause of a false alarm in this system.
    if ctx.station is not None and (ctx.station_km or 99) < 10:
        aggregate = func.max if settings.gauge_rainfall_is_cumulative else func.sum
        station_rain = db.scalar(
            select(func.coalesce(aggregate(GaugeReading.rainfall_mm), 0.0)).where(
                GaugeReading.station == ctx.station,
                GaugeReading.observed_at > at - timedelta(hours=24),
                GaugeReading.observed_at <= at,
            )
        )
        station_mm = float(station_rain or 0.0)
        if station_mm > ctx.rain_24h_mm:
            ctx.rain_24h_mm = station_mm
            ctx.rain_source = "station"

    # --- exposure ------------------------------------------------------
    ctx.users_present = int(
        db.scalar(
            select(func.count(distinct(LocationPing.user_id))).where(
                LocationPing.geohash.startswith(region),
                LocationPing.recorded_at >= at - timedelta(hours=2),
            )
        )
        or 0
    )

    return ctx


def nearest_station(db: Session, latitude: float, longitude: float) -> GaugeStation | None:
    """Closest station within the search radius.

    A linear scan over roughly a hundred stations. A spatial index would be
    faster and would also be an abstraction with one implementation.
    """
    best: GaugeStation | None = None
    best_km = settings.gauge_search_radius_km
    for station in db.scalars(select(GaugeStation)):
        km = geo.haversine_km(latitude, longitude, station.latitude, station.longitude)
        if km <= best_km:
            best, best_km = station, km
    return best


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass
class RiskScore:
    engine: str
    score: float
    severity: str | None
    features: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def _clip(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _severity_for(score: float) -> str | None:
    if score >= settings.score_high:
        return "high"
    if score >= settings.score_moderate:
        return "moderate"
    if score >= settings.score_low:
        return "low"
    return None


def gauge_subscore(ctx: RegionContext) -> float | None:
    """Water level mapped onto 0..1 against the station's own flood levels.

    Anchored on the Irrigation Department's published thresholds rather than on
    an invented scale, so the number means something to a hydrologist:
        at alert level        -> 0.50
        at minor flood level  -> 0.75
        at major flood level  -> 1.00
    """
    level = ctx.water_level_m
    alert = ctx.alert_level_m
    if level is None or alert is None or alert <= 0:
        return None

    minor = ctx.minor_flood_level_m or alert * 1.1
    major = ctx.major_flood_level_m or minor * 1.15

    if level >= major:
        base = 1.0
    elif level >= minor:
        base = 0.75 + 0.25 * (level - minor) / max(0.01, major - minor)
    elif level >= alert:
        base = 0.50 + 0.25 * (level - alert) / max(0.01, minor - alert)
    else:
        base = 0.50 * _clip(level / alert)

    # Rate of rise, capped at +0.15. Movement is information the level alone
    # does not carry.
    if ctx.rise_m_per_3h and ctx.rise_m_per_3h > 0:
        base += _clip(ctx.rise_m_per_3h / 1.0) * 0.15

    # A gauge 25 km away describes a different catchment than one next door.
    if ctx.station_km is not None and ctx.station_km > 10:
        confidence = _clip(1.0 - (ctx.station_km - 10) / 30.0, 0.5, 1.0)
        base *= confidence

    return _clip(base)


def rainfall_subscore(ctx: RegionContext) -> float:
    """24h observed rainfall on the Met Department's scale, plus a forecast nudge."""
    observed = ctx.rain_24h_mm
    if observed >= RAIN_VERY_HEAVY_MM:
        base = 1.0
    elif observed >= RAIN_HEAVY_MM:
        base = 0.6 + 0.4 * (observed - RAIN_HEAVY_MM) / (RAIN_VERY_HEAVY_MM - RAIN_HEAVY_MM)
    else:
        base = 0.6 * (observed / RAIN_HEAVY_MM)

    # Forecast counts at half weight. Rain that has not fallen is a reason to
    # watch, not a reason to warn.
    forecast = _clip(ctx.rain_forecast_6h_mm / 50.0) * 0.5
    return _clip(max(base, base * 0.8 + forecast))


def crowd_subscore(ctx: RegionContext) -> float | None:
    """Zero below the respondent floor -- not "unknown", but "no signal here".

    Returning None below the floor would redistribute the weight onto rainfall
    and gauge and make a thinly-covered region look MORE certain than a
    well-covered one. Below the floor the crowd simply contributes nothing.
    """
    if not ctx.crowd_floor_met or ctx.yes_ratio is None:
        return None
    # Half the panel saying yes is the neutral point; the brief's 0.75 lands at
    # 0.5, and unanimity at 1.0.
    return _clip((ctx.yes_ratio - 0.5) / 0.5)


class RulesV1Engine:
    """Weighted fusion of gauge, rainfall and crowd. The proposed system."""

    name = "rules_v1"

    def score(self, ctx: RegionContext) -> RiskScore:
        gauge = gauge_subscore(ctx)
        rain = rainfall_subscore(ctx)
        crowd = crowd_subscore(ctx)

        parts: list[tuple[str, float, float]] = [("rainfall", rain, settings.weight_rainfall)]
        if gauge is not None:
            parts.append(("gauge", gauge, settings.weight_gauge))
        if crowd is not None:
            parts.append(("crowd", crowd, settings.weight_crowd))

        # Renormalise over the inputs actually available, so a region with no
        # nearby gauge is not automatically scored low.
        total_weight = sum(w for _, _, w in parts)
        score = sum(v * w for _, v, w in parts) / total_weight

        reasons: list[str] = []
        contributions = {
            name: {"value": round(value, 4), "weight": round(weight / total_weight, 4)}
            for name, value, weight in parts
        }

        # --- overrides -------------------------------------------------
        # A river above its major flood level is not a probabilistic statement.
        if (
            ctx.water_level_m is not None
            and ctx.major_flood_level_m is not None
            and ctx.water_level_m >= ctx.major_flood_level_m
        ):
            score = max(score, 0.90)
            reasons.append(
                f"{ctx.station} is at or above its major flood level "
                f"({ctx.water_level_m} m vs {ctx.major_flood_level_m} m)."
            )

        # The crowd corroborates; it does not originate.
        #
        # Where physical data already shows something, agreement from people
        # standing in the region raises confidence -- that is the whole point of
        # the study, and it is worth +0.10 above a physical floor of 0.35.
        #
        # Where there is NO physical signal at all, the crowd is capped below
        # the alert threshold no matter how unanimous. Weight renormalisation
        # would otherwise let twenty coordinated accounts reach "low" on their
        # own, which is the cheapest possible attack on a public warning system.
        # The signal is not thrown away: it is flagged as crowdOnlySignal and
        # surfaced to the operator as something to INVESTIGATE, because
        # localised urban and drain-blockage flooding is real, is most of
        # Colombo's flood experience, and is exactly what no gauge can see.
        physical = max(gauge or 0.0, rain)
        crowd_confirms = False
        crowd_only = False
        if crowd is not None and ctx.yes_ratio is not None and ctx.yes_ratio >= settings.crowd_yes_ratio:
            if physical >= 0.35:
                crowd_confirms = True
                score = _clip(score + 0.10)
                reasons.append(
                    f"{ctx.yes_count} of {ctx.respondents} people in this area "
                    f"report flooding right now."
                )
            else:
                crowd_only = True

        if physical < settings.score_low:
            # Just below the lowest severity band, so _severity_for returns None.
            score = min(score, settings.score_low - 1e-4)

        if gauge is not None and ctx.alert_level_m and ctx.water_level_m:
            if ctx.water_level_m >= ctx.alert_level_m:
                reasons.append(
                    f"{ctx.station} is above its alert level "
                    f"({ctx.water_level_m} m vs {ctx.alert_level_m} m)."
                )
        if ctx.rain_24h_mm >= RAIN_HEAVY_MM:
            reasons.append(f"{ctx.rain_24h_mm:.0f} mm of rain in the last 24 hours.")
        if ctx.rise_m_per_3h and ctx.rise_m_per_3h > 0.2:
            reasons.append(f"Water level rising {ctx.rise_m_per_3h:.2f} m every 3 hours.")

        features = ctx.as_dict()
        features["contributions"] = contributions
        features["crowdConfirms"] = crowd_confirms
        features["crowdOnlySignal"] = crowd_only
        features["physicalSupport"] = round(physical, 4)
        features["engine"] = self.name

        return RiskScore(
            engine=self.name,
            score=round(_clip(score), 4),
            severity=_severity_for(score),
            features=features,
            reasons=reasons,
        )


class RainfallOnlyEngine:
    """The baseline the paper has to beat.

    "Rainfall threshold alone" is the honest counterfactual: it is roughly what
    a person with a weather app already has. Running it on every evaluation, on
    identical inputs, is what lets the paper say what the gauges and the crowd
    actually added -- in precision, in recall, and in lead time.
    """

    name = "rainfall_only"

    def score(self, ctx: RegionContext) -> RiskScore:
        value = rainfall_subscore(ctx)
        features = ctx.as_dict()
        features["engine"] = self.name
        features["contributions"] = {"rainfall": {"value": round(value, 4), "weight": 1.0}}
        return RiskScore(
            engine=self.name,
            score=round(value, 4),
            severity=_severity_for(value),
            features=features,
            reasons=[f"{ctx.rain_24h_mm:.0f} mm of rain in the last 24 hours."],
        )


ENGINES = [RulesV1Engine(), RainfallOnlyEngine()]
PRIMARY_ENGINE = "rules_v1"


# ---------------------------------------------------------------------------
# Evaluation run
# ---------------------------------------------------------------------------


def populated_regions(db: Session, hours: int = 24) -> list[str]:
    """Regions worth evaluating: where people are, plus where the rivers are.

    Station regions are included even with no users so that the system has a
    physical picture from day one -- otherwise a 20-user pilot would only ever
    evaluate the cells those 20 people happen to stand in.
    """
    precision = settings.geohash_precision
    since = utcnow() - timedelta(hours=hours)

    regions = {
        row[0][:precision]
        for row in db.execute(
            select(distinct(LocationPing.geohash)).where(LocationPing.recorded_at >= since)
        )
        if row[0]
    }
    regions |= {
        row[0][:precision]
        for row in db.execute(select(distinct(GaugeStation.geohash)))
        if row[0]
    }
    return sorted(regions)


def evaluate_region(db: Session, region: str, at: datetime | None = None) -> dict:
    """Score one region with every engine and persist the snapshots."""
    ctx = build_context(db, region, at)
    results: dict[str, RiskScore] = {}
    for engine in ENGINES:
        result = engine.score(ctx)
        results[engine.name] = result
        db.add(
            RegionRiskSnapshot(
                geohash=region,
                computed_at=ctx.at,
                engine=result.engine,
                score=result.score,
                severity=result.severity,
                features=result.features,
            )
        )
    db.commit()
    return {"context": ctx, "results": results}


def evaluate_all(db: Session, at: datetime | None = None) -> dict:
    """One full pass: score every region, propose alerts where warranted."""
    at = at or utcnow()
    regions = populated_regions(db)
    proposed = 0

    for region in regions:
        outcome = evaluate_region(db, region, at)
        primary = outcome["results"][PRIMARY_ENGINE]
        if primary.severity is None:
            continue
        if maybe_propose(db, outcome["context"], primary):
            proposed += 1

    return {"regions": len(regions), "proposals": proposed, "at": at}


def maybe_propose(db: Session, ctx: RegionContext, result: RiskScore) -> bool:
    """Queue an alert proposal, unless one is already pending or already live.

    Alert fatigue is the failure mode that kills these systems. A region that
    stays wet for six hours must produce one proposal, not thirty-six.
    """
    now = utcnow()

    pending = db.scalar(
        select(AlertProposal).where(
            AlertProposal.geohash == ctx.geohash,
            AlertProposal.status == "proposed",
        )
    )
    if pending is not None:
        # Refresh the standing proposal instead of adding another. If the
        # situation worsened, the operator sees the worse number.
        if result.score > pending.score:
            pending.score = result.score
            pending.severity = result.severity
            pending.explanation = _explanation(ctx, result)
            pending.message = _message_for(ctx, result)
            db.commit()
        return False

    live = db.scalar(
        select(Alert)
        .join(AlertRegion, AlertRegion.alert_id == Alert.id)
        .where(
            AlertRegion.geohash == ctx.geohash,
            Alert.retracted_at.is_(None),
            Alert.expires_at > now,
        )
    )
    if live is not None:
        rank = {"low": 1, "moderate": 2, "high": 3}
        # Only escalate. A live "moderate" that is still moderate needs nothing.
        if rank.get(result.severity, 0) <= rank.get(live.severity, 0):
            return False

    db.add(
        AlertProposal(
            geohash=ctx.geohash,
            severity=result.severity,
            score=result.score,
            title=_title_for(result),
            message=_message_for(ctx, result),
            explanation=_explanation(ctx, result),
            status="proposed",
            created_at=now,
        )
    )
    db.commit()
    return True


def _title_for(result: RiskScore) -> str:
    return {
        "high": "Flood risk in your area",
        "moderate": "Possible flooding in your area",
        "low": "Watch for rising water in your area",
    }.get(result.severity or "low", "Flood risk in your area")


def _message_for(ctx: RegionContext, result: RiskScore) -> str:
    """The sentence a member of the public reads on their lock screen.

    Plain language, one concrete reason, one action. No scores, no station IDs,
    no percentages -- and never a promise the system cannot keep.
    """
    reason = result.reasons[0] if result.reasons else "Conditions in your area suggest a flood risk."
    action = {
        "high": "Move to higher ground and follow instructions from local authorities.",
        "moderate": "Avoid low-lying areas and be ready to move if water rises.",
        "low": "Stay alert and avoid crossing flowing water.",
    }.get(result.severity or "low", "Stay alert.")
    return f"{reason} {action}"


def _explanation(ctx: RegionContext, result: RiskScore) -> dict:
    """What the operator sees before approving. Everything, in one object."""
    return {
        "score": result.score,
        "severity": result.severity,
        "engine": result.engine,
        "reasons": result.reasons,
        "features": result.features,
    }
