"""Precision, recall, lead time and false-alarm rate (brief 6.6).

The comparison the paper needs is not "does the system work" but "what does
crowdsourcing add". So every metric here is computed per engine over identical
inputs, and `compare()` puts rules_v1 next to rainfall_only.

Definitions, stated explicitly because every flood-warning paper defines them
slightly differently and the reader deserves to know which one this is:

  A region-hour is the unit of analysis: one region cell, one clock hour.
  Positive prediction  region-hour where the engine's severity was not None.
  Positive truth       region-hour overlapping a FloodEvent for that region.
  TP / FP / FN / TN    the usual, over region-hours.
  Precision            TP / (TP + FP)   -- of the hours we warned, how many were floods
  Recall               TP / (TP + FN)   -- of the flood hours, how many did we catch
  False alarm ratio    FP / (TP + FP)   -- 1 - precision, reported because the
                       operational literature quotes FAR, not precision
  Lead time            for each event, minutes between the FIRST positive
                       prediction within the 24h before onset and the onset

Ground truth comes from the flood_events table, entered by hand from DMC
situation reports. Nothing here can be run until that table has rows -- which is
the honest state of the project, and better said than papered over.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import utcnow
from .models import FloodEvent, RegionRiskSnapshot
from .risk import ENGINES


def _hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def metrics_for_engine(
    db: Session,
    engine: str,
    since: datetime | None = None,
    until: datetime | None = None,
    min_severity: str = "low",
) -> dict:
    until = until or utcnow()
    since = since or (until - timedelta(days=30))
    rank = {"low": 1, "moderate": 2, "high": 3}
    floor = rank.get(min_severity, 1)

    snapshots = db.scalars(
        select(RegionRiskSnapshot).where(
            RegionRiskSnapshot.engine == engine,
            RegionRiskSnapshot.computed_at >= since,
            RegionRiskSnapshot.computed_at <= until,
        )
    ).all()
    events = db.scalars(
        select(FloodEvent).where(FloodEvent.started_at <= until)
    ).all()

    if not snapshots:
        return {"engine": engine, "error": "no snapshots in window", "regionHours": 0}

    # Collapse to region-hours, keeping the strongest prediction in each hour.
    predicted: dict[tuple[str, datetime], int] = {}
    for snap in snapshots:
        key = (snap.geohash, _hour(snap.computed_at))
        value = rank.get(snap.severity or "", 0)
        predicted[key] = max(predicted.get(key, 0), value)

    def is_flood(region: str, hour: datetime) -> bool:
        for event in events:
            if not region.startswith(event.geohash) and not event.geohash.startswith(region):
                continue
            end = event.ended_at or (event.started_at + timedelta(hours=24))
            if event.started_at <= hour < end:
                return True
        return False

    tp = fp = fn = tn = 0
    for (region, hour), value in predicted.items():
        warned = value >= floor
        flooded = is_flood(region, hour)
        if warned and flooded:
            tp += 1
        elif warned and not flooded:
            fp += 1
        elif not warned and flooded:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision and recall and (precision + recall)
        else None
    )

    # Lead time per event: first warning in the 24 hours before onset.
    lead_times: list[float] = []
    for event in events:
        if not (since <= event.started_at <= until):
            continue
        window_start = event.started_at - timedelta(hours=24)
        hits = [
            hour
            for (region, hour), value in predicted.items()
            if value >= floor
            and (region.startswith(event.geohash) or event.geohash.startswith(region))
            and window_start <= hour <= event.started_at
        ]
        if hits:
            lead_times.append((event.started_at - min(hits)).total_seconds() / 60.0)

    return {
        "engine": engine,
        "window": {"from": since.isoformat() + "Z", "to": until.isoformat() + "Z"},
        "minSeverity": min_severity,
        "regionHours": len(predicted),
        "groundTruthEvents": len(events),
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "falseAlarmRatio": round(fp / (tp + fp), 4) if (tp + fp) else None,
        "eventsDetected": len(lead_times),
        "medianLeadTimeMinutes": (
            round(sorted(lead_times)[len(lead_times) // 2], 1) if lead_times else None
        ),
        "meanLeadTimeMinutes": (
            round(sum(lead_times) / len(lead_times), 1) if lead_times else None
        ),
    }


def compare(db: Session, **kwargs) -> dict:
    """rules_v1 against rainfall_only, on identical inputs. The paper's table."""
    results = {engine.name: metrics_for_engine(db, engine.name, **kwargs) for engine in ENGINES}
    primary, baseline = results.get("rules_v1", {}), results.get("rainfall_only", {})

    def delta(field: str):
        a, b = primary.get(field), baseline.get(field)
        if a is None or b is None:
            return None
        return round(a - b, 4)

    return {
        "engines": results,
        "deltaVsBaseline": {
            "precision": delta("precision"),
            "recall": delta("recall"),
            "f1": delta("f1"),
            "falseAlarmRatio": delta("falseAlarmRatio"),
            "medianLeadTimeMinutes": delta("medianLeadTimeMinutes"),
        },
        "note": (
            "Positive deltas on precision/recall/f1 and lead time, and a negative "
            "delta on false-alarm ratio, are the result the study is looking for. "
            "With no rows in flood_events every figure here is null -- that is "
            "correct behaviour, not a bug."
        ),
    }
