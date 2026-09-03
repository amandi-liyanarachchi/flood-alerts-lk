"""Gridded rainfall, via Open-Meteo.

Why Open-Meteo for the running system: free, no key, no registration, hourly
resolution, and one call returns both recent past and short forecast. For a
prototype that must keep working unattended, that matters more than provenance.

Why NOT IMERG or CHIRPS here: both are excellent and both are what the paper
should cite for retrospective analysis, but they need Earthdata credentials,
they publish with hours to days of latency, and IMERG Early is still ~4 hours
behind. Neither can drive a real-time warning. The intended split is:

    live warning path   -> Open-Meteo (this file) + Irrigation Dept gauges
    paper's evaluation  -> IMERG Final / CHIRPS, pulled offline into flood_events
                           and used to re-score history with the same engine

Rainfall is fetched per REGION CELL, not per user, so cost is bounded by the
number of populated cells rather than by the number of participants.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import geo
from ..config import settings
from ..db import utcnow
from ..models import RainfallObservation
from .irrigation import IngestResult

log = logging.getLogger(__name__)


def _parse_hour(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except (TypeError, ValueError):
        return None


def fetch_for_regions(db: Session, geohashes: list[str]) -> IngestResult:
    """Fetch past 2 days and next 2 days of hourly precipitation for each cell."""
    if not geohashes:
        return IngestResult(ok=True, detail="no populated regions")

    now = utcnow()
    fetched = stored = 0
    failures: list[str] = []

    with httpx.Client() as client:
        for region in geohashes:
            latitude, longitude = geo.decode_center(region)
            try:
                response = client.get(
                    settings.open_meteo_base,
                    params={
                        "latitude": round(latitude, 4),
                        "longitude": round(longitude, 4),
                        "hourly": "precipitation",
                        "past_days": 2,
                        "forecast_days": 2,
                        "timezone": "UTC",
                    },
                    timeout=settings.upstream_timeout_seconds,
                )
                response.raise_for_status()
                body = response.json()
            except Exception as exc:  # noqa: BLE001
                log.warning("rainfall fetch failed for %s: %s", region, exc)
                failures.append(region)
                continue

            hourly = body.get("hourly") or {}
            times = hourly.get("time") or []
            values = hourly.get("precipitation") or []
            fetched += len(times)

            for time_str, value in zip(times, values):
                observed_at = _parse_hour(time_str)
                if observed_at is None:
                    continue
                row = RainfallObservation(
                    geohash=region,
                    latitude=latitude,
                    longitude=longitude,
                    observed_at=observed_at,
                    precipitation_mm=float(value or 0.0),
                    # Anything ahead of now is a forecast. The risk engine
                    # weights observed and forecast rainfall differently -- a
                    # forecast is a reason to watch, not a reason to warn.
                    is_forecast=observed_at > now,
                )
                db.add(row)
                try:
                    db.commit()
                    stored += 1
                except IntegrityError:
                    db.rollback()
                    # Already have this hour for this cell. Overwrite only when
                    # a forecast has since become an observation.
                    existing = (
                        db.query(RainfallObservation)
                        .filter(
                            RainfallObservation.geohash == region,
                            RainfallObservation.observed_at == observed_at,
                        )
                        .one_or_none()
                    )
                    if existing is not None and existing.is_forecast and not row.is_forecast:
                        existing.precipitation_mm = row.precipitation_mm
                        existing.is_forecast = False
                        existing.ingested_at = utcnow()
                        db.commit()

    return IngestResult(
        ok=not failures or len(failures) < len(geohashes),
        fetched=fetched,
        stored=stored,
        detail=f"failed cells: {failures}" if failures else "",
    )
