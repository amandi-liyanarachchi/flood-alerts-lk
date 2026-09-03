"""Data retention (brief sec 7).

The study collects continuous location traces keyed to a national identity
number. Keeping them indefinitely is neither necessary nor defensible, so the
policy is written as code that actually runs, not as a paragraph in an ethics
form.

    location_pings          90 days   raw traces. The research question needs
                                      "were people in this region", not "where
                                      was this person in March".
    feedback               730 days   the crowdsourced answers ARE the dataset;
                                      they carry a coarse location and one bit.
    gauge_readings         365 days   public data, re-fetchable, kept for
                                      retrospective scoring.
    rainfall_observations  365 days   same.
    region_risk_snapshots   forever   aggregate over users, name nobody, and are
                                      the evidence base for the paper.
    alerts / proposals      forever   public safety record and audit trail.

Before pings are deleted they are rolled up into a per-region-hour count, so the
exposure denominator ("how many people were in this cell") survives without the
traces that produced it.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import utcnow
from .models import Feedback, GaugeReading, LocationPing, RainfallObservation, RegionRiskSnapshot

log = logging.getLogger(__name__)


def rollup_and_purge(db: Session) -> dict:
    now = utcnow()
    ping_cutoff = now - timedelta(days=settings.retention_locations_days)

    # Roll up before deleting: keep the count, drop the trace.
    rows = db.execute(
        select(
            func.substr(LocationPing.geohash, 1, settings.geohash_precision).label("region"),
            func.count(func.distinct(LocationPing.user_id)).label("users"),
            func.count(LocationPing.id).label("pings"),
        )
        .where(LocationPing.recorded_at < ping_cutoff)
        .group_by("region")
    ).all()

    for region, users, pings in rows:
        db.add(
            RegionRiskSnapshot(
                geohash=region,
                computed_at=ping_cutoff,
                engine="retention_rollup",
                score=0.0,
                severity=None,
                features={
                    "kind": "exposure_rollup",
                    "distinctUsers": int(users),
                    "pings": int(pings),
                    "purgedBefore": ping_cutoff.isoformat() + "Z",
                },
            )
        )

    purged = {
        "locationPings": db.query(LocationPing)
        .filter(LocationPing.recorded_at < ping_cutoff)
        .delete(synchronize_session=False),
        "feedback": db.query(Feedback)
        .filter(Feedback.answered_at < now - timedelta(days=settings.retention_feedback_days))
        .delete(synchronize_session=False),
        "gaugeReadings": db.query(GaugeReading)
        .filter(GaugeReading.observed_at < now - timedelta(days=settings.retention_readings_days))
        .delete(synchronize_session=False),
        "rainfall": db.query(RainfallObservation)
        .filter(
            RainfallObservation.observed_at < now - timedelta(days=settings.retention_readings_days)
        )
        .delete(synchronize_session=False),
    }
    db.commit()

    log.info("Retention pass: rolled up %d regions, purged %s", len(rows), purged)
    return {"rolledUpRegions": len(rows), "purged": purged, "at": now.isoformat() + "Z"}
