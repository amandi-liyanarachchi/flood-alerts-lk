"""Background jobs.

APScheduler in the same process as the API. For a pilot on one box this is the
right amount of machinery: one container, one command, nothing to keep in sync.
If this ever runs more than one replica, the jobs must move out -- two schedulers
would double-ingest. That is the single thing to remember about this file.

Every job catches its own exceptions. A failing upstream must never take the API
down: the app's location pings are more important than the gauge feed.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from . import retention, risk
from .config import settings
from .db import SessionLocal
from .ingest import irrigation, rainfall

log = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="UTC")


def _session_job(name: str, fn) -> None:
    db = SessionLocal()
    try:
        result = fn(db)
        log.info("job %s: %s", name, result)
    except Exception:  # noqa: BLE001
        log.exception("job %s failed", name)
        db.rollback()
    finally:
        db.close()


def job_stations() -> None:
    _session_job("stations", irrigation.refresh_stations)


def job_readings() -> None:
    _session_job("readings", lambda db: irrigation.fetch_readings(db, max_pages=1))


def job_rainfall() -> None:
    def run(db):
        regions = risk.populated_regions(db)
        return rainfall.fetch_for_regions(db, regions)

    _session_job("rainfall", run)


def job_evaluate() -> None:
    _session_job("evaluate", risk.evaluate_all)


def job_retention() -> None:
    _session_job("retention", retention.rollup_and_purge)


def start() -> None:
    if not settings.ingest_enabled:
        log.info("Scheduler disabled (INGEST_ENABLED=false).")
        return

    scheduler.add_job(job_stations, "interval", hours=24, id="stations", next_run_time=None)
    scheduler.add_job(
        job_readings, "interval", minutes=settings.ingest_gauges_minutes, id="readings"
    )
    scheduler.add_job(
        job_rainfall, "interval", minutes=settings.ingest_rainfall_minutes, id="rainfall"
    )
    # Evaluation runs AFTER ingestion on the same cadence as the client's ping
    # interval: there is no point scoring more often than the data changes.
    scheduler.add_job(
        job_evaluate, "interval", minutes=settings.risk_evaluate_minutes, id="evaluate"
    )
    scheduler.add_job(job_retention, "cron", hour=19, minute=30, id="retention")  # 01:00 Colombo
    scheduler.start()
    log.info("Scheduler started with %d jobs.", len(scheduler.get_jobs()))


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
