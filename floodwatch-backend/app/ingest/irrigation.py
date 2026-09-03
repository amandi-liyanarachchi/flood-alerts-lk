"""Irrigation Department river gauges, via ArcGIS Feature Services.

There is no documented public API for Sri Lankan river levels. There is,
however, a public one. The Irrigation Department's ArcGIS Online dashboards are
backed by anonymously queryable feature services, discovered by reading the
dashboards' own item definitions:

    https://slirrigation.maps.arcgis.com/sharing/rest/content/items/<dashboard>/data?f=json
      -> datasource itemIds
      -> .../items/<webmap>/data?f=json
      -> operationalLayers[].url

Two layers matter:

    hydrostations/FeatureServer/0   station master list + official flood levels
    gauges_2_view/FeatureServer/0   append-only readings (water level, rainfall)

Verified 2026-08-28: anonymous, no key, ~6,400 reading rows, maxRecordCount 1000,
supports where / outFields / orderByFields / resultOffset.

TWO REAL HAZARDS, both handled below:

1. UNITS ARE MIXED. hydrostations.Unit is "m" for most stations but "ft" for
   some (Nagalagam Street on the Kelani). Comparing a metre reading against a
   foot threshold would put central Colombo permanently in flood. Everything is
   converted to metres at ingest.

2. THE TWO LAYERS DISAGREE. gauges_2_view carries its own alertpull / minorpull
   / majorpull, which differ from hydrostations for some stations (Glencourse
   minor flood: 15.5 vs 16). hydrostations is treated as authoritative and the
   mismatch is logged rather than silently resolved.

This is an undocumented endpoint on someone else's infrastructure. It can change
or vanish without notice. Every failure is caught, logged and reported; the risk
engine degrades to rainfall-only rather than stopping.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import geo
from ..config import settings
from ..db import utcnow
from ..models import GaugeReading, GaugeStation

log = logging.getLogger(__name__)

FEET_TO_METRES = 0.3048


@dataclass
class IngestResult:
    ok: bool
    fetched: int = 0
    stored: int = 0
    detail: str = ""


def _epoch_ms_to_naive_utc(value) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _query(client: httpx.Client, service: str, params: dict) -> list[dict]:
    """Run one ArcGIS query and return its features."""
    url = f"{settings.arcgis_base}/{service}/FeatureServer/0/query"
    merged = {"f": "json", "outSR": 4326, **params}
    response = client.get(url, params=merged, timeout=settings.upstream_timeout_seconds)
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        # ArcGIS returns HTTP 200 with an error object in the body.
        raise RuntimeError(f"ArcGIS error on {service}: {body['error']}")
    return body.get("features", [])


def refresh_stations(db: Session) -> IngestResult:
    """Upsert the station master list. Cheap; run daily, or on demand."""
    try:
        with httpx.Client() as client:
            features = _query(
                client,
                "hydrostations",
                {"where": "1=1", "outFields": "*", "returnGeometry": "true"},
            )
    except Exception as exc:  # noqa: BLE001 -- upstream is out of our control
        log.warning("hydrostations fetch failed: %s", exc)
        return IngestResult(ok=False, detail=str(exc))

    stored = 0
    for feature in features:
        attrs = feature.get("attributes", {}) or {}
        geometry = feature.get("geometry", {}) or {}
        name = (attrs.get("station") or "").strip()
        if not name:
            continue

        latitude = attrs.get("latitude") or geometry.get("y")
        longitude = attrs.get("longitude") or geometry.get("x")
        if latitude is None or longitude is None:
            continue

        unit = (attrs.get("Unit") or "m").strip().lower()
        factor = FEET_TO_METRES if unit.startswith("ft") else 1.0

        def to_m(value):
            return None if value is None else round(float(value) * factor, 3)

        station = db.scalar(select(GaugeStation).where(GaugeStation.station == name))
        if station is None:
            station = GaugeStation(station=name)
            db.add(station)

        station.basin = attrs.get("basin")
        station.tributary = attrs.get("Tributory")  # upstream spelling
        station.latitude = float(latitude)
        station.longitude = float(longitude)
        station.alert_level_m = to_m(attrs.get("Alert_Level"))
        station.minor_flood_level_m = to_m(attrs.get("Minor_Flood_Level"))
        station.major_flood_level_m = to_m(attrs.get("Major_Flood_Level"))
        station.source_unit = "ft" if factor != 1.0 else "m"
        station.elevation_m = attrs.get("Elivation_m_MSL")  # upstream spelling
        station.geohash = geo.encode(float(latitude), float(longitude), precision=8)
        station.updated_at = utcnow()
        stored += 1

    db.commit()
    return IngestResult(ok=True, fetched=len(features), stored=stored)


def fetch_readings(db: Session, page_size: int = 1000, max_pages: int = 3) -> IngestResult:
    """Pull the most recent readings and store the ones we do not have.

    Ordered newest-first and paged, so a normal run touches one page. max_pages
    caps a cold start at 3,000 rows; run scripts/backfill_gauges.py for history.
    """
    features: list[dict] = []
    try:
        with httpx.Client() as client:
            for page in range(max_pages):
                batch = _query(
                    client,
                    "gauges_2_view",
                    {
                        "where": "1=1",
                        "outFields": "basin,gauge,water_level,rain_fall,CreationDate",
                        "orderByFields": "CreationDate DESC",
                        "resultRecordCount": page_size,
                        "resultOffset": page * page_size,
                        "returnGeometry": "true",
                    },
                )
                features.extend(batch)
                if len(batch) < page_size:
                    break
    except Exception as exc:  # noqa: BLE001
        log.warning("gauges_2_view fetch failed: %s", exc)
        return IngestResult(ok=False, fetched=len(features), detail=str(exc))

    stations = {s.station: s for s in db.scalars(select(GaugeStation))}
    stored = 0

    for feature in features:
        attrs = feature.get("attributes", {}) or {}
        geometry = feature.get("geometry", {}) or {}
        name = (attrs.get("gauge") or "").strip()
        observed_at = _epoch_ms_to_naive_utc(attrs.get("CreationDate"))
        if not name or observed_at is None:
            continue

        # CreationDate is when the reading was published to ArcGIS, which is the
        # closest thing to an observation time the service exposes. Recorded as
        # such; the paper should not claim it is the instant of measurement.

        station = stations.get(name)
        unit_factor = FEET_TO_METRES if station and station.source_unit == "ft" else 1.0
        raw_level = attrs.get("water_level")
        level_m = None if raw_level is None else round(float(raw_level) * unit_factor, 3)

        reading = GaugeReading(
            station=name,
            basin=attrs.get("basin"),
            water_level_m=level_m,
            rainfall_mm=attrs.get("rain_fall"),
            latitude=geometry.get("y"),
            longitude=geometry.get("x"),
            observed_at=observed_at,
        )
        db.add(reading)
        try:
            db.commit()
            stored += 1
        except IntegrityError:
            # Already have this (station, observed_at). Expected on every run --
            # the service is append-only and we re-read the newest page.
            db.rollback()

    return IngestResult(ok=True, fetched=len(features), stored=stored)


def check_threshold_agreement(db: Session) -> list[dict]:
    """Compare hydrostations thresholds against the ones embedded in gauges.

    Not used by the risk engine -- it exists so the disagreement is visible on
    the admin dashboard and quotable in the paper's data-quality section rather
    than being discovered by a false alarm.
    """
    mismatches: list[dict] = []
    try:
        with httpx.Client() as client:
            features = _query(
                client,
                "gauges_2_view",
                {
                    "where": "1=1",
                    "outFields": "gauge,alertpull,minorpull,majorpull",
                    "orderByFields": "CreationDate DESC",
                    "resultRecordCount": 1000,
                    "returnGeometry": "false",
                },
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("threshold comparison failed: %s", exc)
        return []

    seen: set[str] = set()
    stations = {s.station: s for s in db.scalars(select(GaugeStation))}

    for feature in features:
        attrs = feature.get("attributes", {}) or {}
        name = (attrs.get("gauge") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        station = stations.get(name)
        if station is None:
            mismatches.append({"station": name, "issue": "present in gauges but not in hydrostations"})
            continue

        factor = FEET_TO_METRES if station.source_unit == "ft" else 1.0
        pairs = [
            ("alert", station.alert_level_m, attrs.get("alertpull")),
            ("minor", station.minor_flood_level_m, attrs.get("minorpull")),
            ("major", station.major_flood_level_m, attrs.get("majorpull")),
        ]
        for label, authoritative, other in pairs:
            if authoritative is None or other is None:
                continue
            if abs(authoritative - float(other) * factor) > 0.05:
                mismatches.append(
                    {
                        "station": name,
                        "threshold": label,
                        "hydrostations_m": authoritative,
                        "gauges_view_m": round(float(other) * factor, 3),
                    }
                )

    return mismatches
