"""POST /locations -- the highest-volume endpoint in the system.

Read brief section 5.3 before changing any status code in this file. The status
code returned here decides whether a location ping survives:

    2xx  -> client accepts and moves on
    5xx  -> client queues the ping and retries on the next tick
    4xx  -> client DROPS THE PING PERMANENTLY
    401  -> client drops the ping, wipes the whole queue, logs the user out
"""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from .. import errors, geo, ratelimit
from ..config import settings
from ..db import to_naive_utc, utcnow
from ..deps import CurrentUser, DbSession
from ..models import LocationPing
from ..schemas import AcceptedResponse, LocationRequest

router = APIRouter(prefix="/locations", tags=["locations"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=AcceptedResponse)
def ingest_location(payload: LocationRequest, user: CurrentUser, db: DbSession) -> AcceptedResponse:
    # Backpressure is 503, never 429. 429 is a 4xx and would make the client
    # delete the ping forever. The generous limit (120/hour vs an expected 6)
    # exists to stop a runaway client, not to shape normal traffic.
    if not ratelimit.allow(f"loc:{user.id}", settings.locations_max_per_hour):
        raise errors.service_busy(retry_after=300)

    recorded_at = to_naive_utc(payload.recordedAt)
    now = utcnow()

    ping = LocationPing(
        user_id=user.id,
        latitude=payload.latitude,
        longitude=payload.longitude,
        accuracy=payload.accuracy,
        recorded_at=recorded_at,
        source=payload.source,
        geohash=geo.encode(payload.latitude, payload.longitude, precision=8),
        received_at=now,
        # recorded_at is authoritative, not arrival time (brief 5.4). We record
        # the gap so the paper can report the real lag distribution -- the brief
        # predicts up to ~50 minutes after an outage.
        # Signed on purpose. A NEGATIVE lag means the device clock is ahead of
        # ours, and recordedAt comes from the device, so a wrong phone clock
        # silently corrupts the time series. Storing the sign makes that
        # visible in the data instead of invisible.
        lag_seconds=int((now - recorded_at).total_seconds()),
        in_country=geo.is_plausible_lk_coordinate(payload.latitude, payload.longitude),
    )
    db.add(ping)

    try:
        db.commit()
    except IntegrityError:
        # A replayed ping: our 201 was lost after we committed, so the client
        # retried. (user_id, recorded_at, source) is the natural key. Idempotent
        # success -- anything else would make the client either give up on a
        # ping we already have (4xx) or retry forever (5xx).
        db.rollback()
        return AcceptedResponse(accepted=True)
    except OperationalError:
        # Deadlock, connection drop, database restarting mid-deploy. Transient.
        # A 4xx here would silently destroy location data during exactly the
        # conditions this system exists for.
        db.rollback()
        raise errors.service_busy(retry_after=60)

    return AcceptedResponse(accepted=True)


@router.get("/me/latest", tags=["locations"])
def my_latest_location(user: CurrentUser, db: DbSession) -> dict:
    """Convenience for debugging a device in the field. Not used by the app."""
    ping = db.scalar(
        select(LocationPing)
        .where(LocationPing.user_id == user.id)
        .order_by(LocationPing.recorded_at.desc())
        .limit(1)
    )
    if ping is None:
        return {"location": None}
    from ..schemas import iso_z

    return {
        "location": {
            "latitude": ping.latitude,
            "longitude": ping.longitude,
            "accuracy": ping.accuracy,
            "recordedAt": iso_z(ping.recorded_at),
            "source": ping.source,
            "region": ping.geohash[: settings.geohash_precision],
        }
    }
