"""Admin API, behind an X-Admin-Token header.

Everything an operator needs to run the pilot and everything the paper needs to
report: what is arriving, what the model proposes and why, and one Approve or
Dismiss decision per proposal.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Body, Query
from sqlalchemy import Integer, distinct, func, select

from .. import alerts_service, errors, evaluation, geo, retention, risk
from ..config import settings
from ..db import utcnow
from ..deps import AdminGuard, DbSession
from ..ingest import irrigation, rainfall
from ..models import (
    Alert,
    AlertProposal,
    Device,
    Feedback,
    GaugeReading,
    GaugeStation,
    LocationPing,
    RegionRiskSnapshot,
    User,
)
from ..schemas import iso_z

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


@router.get("/summary")
def summary(_: AdminGuard, db: DbSession) -> dict:
    now = utcnow()
    day_ago = now - timedelta(hours=24)

    return {
        "at": iso_z(now),
        "users": db.scalar(select(func.count(User.id))) or 0,
        "devices": db.scalar(select(func.count(Device.id))) or 0,
        "pings24h": db.scalar(
            select(func.count(LocationPing.id)).where(LocationPing.recorded_at >= day_ago)
        )
        or 0,
        "activeUsers24h": db.scalar(
            select(func.count(distinct(LocationPing.user_id))).where(
                LocationPing.recorded_at >= day_ago
            )
        )
        or 0,
        "feedback24h": db.scalar(
            select(func.count(Feedback.id)).where(Feedback.answered_at >= day_ago)
        )
        or 0,
        "yes24h": db.scalar(
            select(func.count(Feedback.id)).where(
                Feedback.answered_at >= day_ago, Feedback.flood_present.is_(True)
            )
        )
        or 0,
        "stations": db.scalar(select(func.count(GaugeStation.id))) or 0,
        "readings24h": db.scalar(
            select(func.count(GaugeReading.id)).where(GaugeReading.observed_at >= day_ago)
        )
        or 0,
        "pendingProposals": db.scalar(
            select(func.count(AlertProposal.id)).where(AlertProposal.status == "proposed")
        )
        or 0,
        "liveAlerts": db.scalar(
            select(func.count(Alert.id)).where(
                Alert.retracted_at.is_(None), Alert.expires_at > now
            )
        )
        or 0,
        "config": {
            "regionPrecision": settings.geohash_precision,
            "crowdWindowMinutes": settings.crowd_window_minutes,
            "crowdMinRespondents": settings.crowd_min_respondents,
            "crowdYesRatio": settings.crowd_yes_ratio,
        },
    }


@router.get("/regions")
def region_density(
    _: AdminGuard,
    db: DbSession,
    hours: int = Query(default=24, ge=1, le=720),
) -> dict:
    """Ping and feedback density per region -- the map on the dashboard."""
    since = utcnow() - timedelta(hours=hours)
    precision = settings.geohash_precision
    region_expr = func.substr(LocationPing.geohash, 1, precision)

    pings = {
        row[0]: {"pings": row[1], "users": row[2]}
        for row in db.execute(
            select(region_expr, func.count(LocationPing.id), func.count(distinct(LocationPing.user_id)))
            .where(LocationPing.recorded_at >= since)
            .group_by(region_expr)
        )
    }

    fb_expr = func.substr(Feedback.geohash, 1, precision)
    feedback = {
        row[0]: {"answers": row[1], "yes": row[2] or 0}
        for row in db.execute(
            select(
                fb_expr,
                func.count(Feedback.id),
                func.sum(func.cast(Feedback.flood_present, Integer)),
            )
            .where(Feedback.answered_at >= since, Feedback.geohash.isnot(None))
            .group_by(fb_expr)
        )
    }

    latest = {}
    for row in db.execute(
        select(
            RegionRiskSnapshot.geohash,
            func.max(RegionRiskSnapshot.computed_at),
        )
        .where(RegionRiskSnapshot.engine == risk.PRIMARY_ENGINE)
        .group_by(RegionRiskSnapshot.geohash)
    ):
        snap = db.scalar(
            select(RegionRiskSnapshot).where(
                RegionRiskSnapshot.geohash == row[0],
                RegionRiskSnapshot.computed_at == row[1],
                RegionRiskSnapshot.engine == risk.PRIMARY_ENGINE,
            )
        )
        if snap:
            latest[row[0]] = {"score": snap.score, "severity": snap.severity}

    regions = sorted(set(pings) | set(feedback) | set(latest))
    out = []
    for region in regions:
        latitude, longitude = geo.decode_center(region)
        out.append(
            {
                "region": region,
                "latitude": round(latitude, 5),
                "longitude": round(longitude, 5),
                **pings.get(region, {"pings": 0, "users": 0}),
                **feedback.get(region, {"answers": 0, "yes": 0}),
                **latest.get(region, {"score": None, "severity": None}),
            }
        )
    return {"hours": hours, "regions": out}


@router.get("/community-signals")
def community_signals(_: AdminGuard, db: DbSession, hours: int = Query(default=6, ge=1, le=72)) -> dict:
    """Regions where people report flooding but no instrument agrees.

    These are NOT alert proposals and they never become alerts on their own --
    the crowd cannot originate a public warning (see risk.py). They are here
    because localised urban flooding is real, no gauge can see it, and an
    operator who can phone a Grama Niladhari is a better sensor than either.

    Every row is also a research artefact: the ones that turn out to be real
    floods are the case for crowdsourcing, and the ones that do not are the
    false-positive rate the paper has to report honestly.
    """
    since = utcnow() - timedelta(hours=hours)
    rows = db.scalars(
        select(RegionRiskSnapshot)
        .where(
            RegionRiskSnapshot.engine == risk.PRIMARY_ENGINE,
            RegionRiskSnapshot.computed_at >= since,
        )
        .order_by(RegionRiskSnapshot.computed_at.desc())
    ).all()

    seen: set[str] = set()
    signals = []
    for snap in rows:
        if snap.geohash in seen or not (snap.features or {}).get("crowdOnlySignal"):
            continue
        seen.add(snap.geohash)
        crowd = (snap.features or {}).get("crowd", {})
        latitude, longitude = geo.decode_center(snap.geohash)
        signals.append(
            {
                "region": snap.geohash,
                "latitude": round(latitude, 5),
                "longitude": round(longitude, 5),
                "respondents": crowd.get("respondents"),
                "yes": crowd.get("yes"),
                "ratio": crowd.get("ratio"),
                "physicalSupport": (snap.features or {}).get("physicalSupport"),
                "computedAt": iso_z(snap.computed_at),
            }
        )
    return {"hours": hours, "signals": signals}


@router.get("/regions/{region}")
def region_detail(region: str, _: AdminGuard, db: DbSession) -> dict:
    """Everything behind one region's current score."""
    ctx = risk.build_context(db, region)
    results = {engine.name: engine.score(ctx) for engine in risk.ENGINES}
    return {
        "region": region,
        "context": ctx.as_dict(),
        "engines": {
            name: {"score": r.score, "severity": r.severity, "reasons": r.reasons}
            for name, r in results.items()
        },
    }


# ---------------------------------------------------------------------------
# Proposals and alerts
# ---------------------------------------------------------------------------


@router.get("/proposals")
def list_proposals(
    _: AdminGuard,
    db: DbSession,
    status: str = Query(default="proposed"),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    query = select(AlertProposal).order_by(AlertProposal.created_at.desc()).limit(limit)
    if status != "all":
        query = query.where(AlertProposal.status == status)

    return {
        "proposals": [
            {
                "id": p.id,
                "region": p.geohash,
                "latitude": round(geo.decode_center(p.geohash)[0], 5),
                "longitude": round(geo.decode_center(p.geohash)[1], 5),
                "severity": p.severity,
                "score": p.score,
                "title": p.title,
                "message": p.message,
                "status": p.status,
                "createdAt": iso_z(p.created_at),
                "decidedAt": iso_z(p.decided_at),
                "decidedBy": p.decided_by,
                "alertId": p.alert_id,
                "explanation": p.explanation,
            }
            for p in db.scalars(query)
        ]
    }


@router.post("/proposals/{proposal_id}/approve")
def approve_proposal(
    proposal_id: int,
    _: AdminGuard,
    db: DbSession,
    operator: str = Body(embed=True),
    ttlHours: int | None = Body(default=None, embed=True),
    message: str | None = Body(default=None, embed=True),
) -> dict:
    proposal = db.get(AlertProposal, proposal_id)
    if proposal is None:
        raise errors.ApiError(404, errors.NOT_FOUND, "That proposal no longer exists.")
    if proposal.status != "proposed":
        raise errors.ApiError(409, errors.VALIDATION_FAILED, f"This proposal is already {proposal.status}.")

    # The operator may rewrite the wording before it reaches the public. The
    # model is good at deciding whether to warn and poor at deciding how to
    # phrase it for a frightened person at 2am.
    if message:
        proposal.message = message.strip()[:500]

    alert = alerts_service.publish(db, proposal, operator=operator, ttl_hours=ttlHours)
    return {
        "alertId": alert.id,
        "severity": alert.severity,
        "issuedAt": iso_z(alert.issued_at),
        "expiresAt": iso_z(alert.expires_at),
        "push": alert.push_result,
    }


@router.post("/proposals/{proposal_id}/dismiss")
def dismiss_proposal(
    proposal_id: int,
    _: AdminGuard,
    db: DbSession,
    operator: str = Body(embed=True),
    note: str | None = Body(default=None, embed=True),
) -> dict:
    proposal = db.get(AlertProposal, proposal_id)
    if proposal is None:
        raise errors.ApiError(404, errors.NOT_FOUND, "That proposal no longer exists.")
    alerts_service.dismiss(db, proposal, operator=operator, note=note)
    return {"dismissed": proposal.id}


@router.get("/alerts")
def list_alerts(_: AdminGuard, db: DbSession, limit: int = Query(default=50, ge=1, le=500)) -> dict:
    now = utcnow()
    alerts = db.scalars(select(Alert).order_by(Alert.issued_at.desc()).limit(limit)).all()
    return {
        "alerts": [
            {
                "id": a.id,
                "severity": a.severity,
                "title": a.title,
                "message": a.message,
                "issuedAt": iso_z(a.issued_at),
                "expiresAt": iso_z(a.expires_at),
                "retractedAt": iso_z(a.retracted_at),
                "issuedBy": a.issued_by,
                "live": a.retracted_at is None and a.expires_at > now,
                "regions": [r.geohash for r in a.regions],
                "push": a.push_result,
            }
            for a in alerts
        ]
    }


@router.post("/alerts/{alert_id}/retract")
def retract_alert(
    alert_id: str,
    _: AdminGuard,
    db: DbSession,
    operator: str = Body(embed=True),
) -> dict:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise errors.ApiError(404, errors.NOT_FOUND, "That alert no longer exists.")
    alerts_service.retract(db, alert, operator=operator)
    return {"retracted": alert.id, "at": iso_z(alert.retracted_at)}


@router.post("/alerts/manual")
def manual_alert(
    _: AdminGuard,
    db: DbSession,
    region: str = Body(embed=True),
    severity: str = Body(embed=True),
    title: str = Body(embed=True),
    message: str = Body(embed=True),
    operator: str = Body(embed=True),
    ttlHours: int = Body(default=6, embed=True),
) -> dict:
    """Issue an alert the model did not propose.

    Needed for a live demo, and needed in the field when an operator knows
    something the sensors do not. Recorded as a proposal with engine "manual" so
    the audit trail has no special cases.
    """
    if severity not in {"low", "moderate", "high"}:
        raise errors.validation_failed("Severity must be low, moderate or high.")

    proposal = AlertProposal(
        geohash=region,
        severity=severity,
        score=1.0,
        title=title,
        message=message,
        explanation={"engine": "manual", "operator": operator, "reasons": ["Issued manually."]},
        status="proposed",
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)

    alert = alerts_service.publish(db, proposal, operator=operator, ttl_hours=ttlHours)
    return {"alertId": alert.id, "push": alert.push_result}


# ---------------------------------------------------------------------------
# Data pipeline controls
# ---------------------------------------------------------------------------


@router.get("/gauges")
def gauges(_: AdminGuard, db: DbSession, basin: str | None = None) -> dict:
    """Latest reading per station, with how it sits against its flood levels."""
    stations = db.scalars(
        select(GaugeStation).where(GaugeStation.basin == basin) if basin else select(GaugeStation)
    ).all()

    out = []
    for station in stations:
        reading = db.scalar(
            select(GaugeReading)
            .where(GaugeReading.station == station.station)
            .order_by(GaugeReading.observed_at.desc())
            .limit(1)
        )
        state = "unknown"
        if reading and reading.water_level_m is not None:
            level = reading.water_level_m
            if station.major_flood_level_m and level >= station.major_flood_level_m:
                state = "major"
            elif station.minor_flood_level_m and level >= station.minor_flood_level_m:
                state = "minor"
            elif station.alert_level_m and level >= station.alert_level_m:
                state = "alert"
            else:
                state = "normal"
        out.append(
            {
                "station": station.station,
                "basin": station.basin,
                "tributary": station.tributary,
                "latitude": station.latitude,
                "longitude": station.longitude,
                "sourceUnit": station.source_unit,
                "alertLevelM": station.alert_level_m,
                "minorFloodLevelM": station.minor_flood_level_m,
                "majorFloodLevelM": station.major_flood_level_m,
                "waterLevelM": reading.water_level_m if reading else None,
                "rainfallMm": reading.rainfall_mm if reading else None,
                "observedAt": iso_z(reading.observed_at) if reading else None,
                "state": state,
            }
        )
    out.sort(key=lambda r: (r["basin"] or "", r["station"]))
    return {"stations": out}


@router.post("/ingest/stations")
def run_station_refresh(_: AdminGuard, db: DbSession) -> dict:
    result = irrigation.refresh_stations(db)
    return {"ok": result.ok, "fetched": result.fetched, "stored": result.stored, "detail": result.detail}


@router.post("/ingest/readings")
def run_reading_ingest(_: AdminGuard, db: DbSession, pages: int = Body(default=1, embed=True)) -> dict:
    result = irrigation.fetch_readings(db, max_pages=pages)
    return {"ok": result.ok, "fetched": result.fetched, "stored": result.stored, "detail": result.detail}


@router.post("/ingest/rainfall")
def run_rainfall_ingest(_: AdminGuard, db: DbSession) -> dict:
    regions = risk.populated_regions(db)
    result = rainfall.fetch_for_regions(db, regions)
    return {"ok": result.ok, "regions": len(regions), "stored": result.stored, "detail": result.detail}


@router.get("/data-quality")
def data_quality(_: AdminGuard, db: DbSession) -> dict:
    """Known disagreements between the two upstream layers.

    Surfaced rather than resolved, because silently picking one would be exactly
    the kind of hidden decision that makes a false alarm impossible to explain.
    """
    return {"thresholdMismatches": irrigation.check_threshold_agreement(db)}


@router.post("/evaluate")
def run_evaluation(_: AdminGuard, db: DbSession) -> dict:
    result = risk.evaluate_all(db)
    return {"regions": result["regions"], "proposals": result["proposals"], "at": iso_z(result["at"])}


@router.get("/metrics")
def metrics(
    _: AdminGuard,
    db: DbSession,
    days: int = Query(default=30, ge=1, le=365),
    minSeverity: str = Query(default="low"),
) -> dict:
    since = utcnow() - timedelta(days=days)
    return evaluation.compare(db, since=since, min_severity=minSeverity)


@router.post("/retention/run")
def run_retention(_: AdminGuard, db: DbSession) -> dict:
    return retention.rollup_and_purge(db)
