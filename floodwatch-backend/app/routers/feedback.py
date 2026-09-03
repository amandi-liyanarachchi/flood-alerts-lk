"""POST /feedback -- the crowdsourced signal.

Poisoning this endpoint is an attack on the model, not just on the server
(brief sec 7). Two defences live here; the third and most important one lives in
risk.py, where aggregation counts one vote per user per window.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from .. import errors, geo, ratelimit
from ..config import settings
from ..db import to_naive_utc, utcnow
from ..deps import CurrentUser, DbSession
from ..models import Feedback, LocationPing
from ..schemas import AcceptedResponse, FeedbackRequest

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=AcceptedResponse)
def submit_feedback(payload: FeedbackRequest, user: CurrentUser, db: DbSession) -> AcceptedResponse:
    # 503, not 429 -- same reasoning as /locations. Feedback is not queued by the
    # client, but a 4xx would still show the user a failure for what is really
    # our problem.
    if not ratelimit.allow(f"fb:{user.id}", settings.feedback_max_per_hour):
        raise errors.service_busy(retry_after=300)

    latitude, longitude = payload.latitude, payload.longitude
    inferred = False

    if latitude is None or longitude is None:
        # No fix was available on the device. Fall back to the user's last known
        # ping (brief sec 3). Flagged, because a fallback location is weaker
        # evidence and the analysis should be able to exclude it.
        last = db.scalar(
            select(LocationPing)
            .where(LocationPing.user_id == user.id)
            .order_by(LocationPing.recorded_at.desc())
            .limit(1)
        )
        if last is not None:
            latitude, longitude, inferred = last.latitude, last.longitude, True

    answered_at = to_naive_utc(payload.answeredAt)
    entry = Feedback(
        user_id=user.id,
        flood_present=payload.floodPresent,
        latitude=latitude,
        longitude=longitude,
        answered_at=answered_at,
        geohash=geo.encode(latitude, longitude, precision=8) if latitude is not None else None,
        received_at=utcnow(),
        location_inferred=inferred,
    )
    db.add(entry)

    try:
        db.commit()
    except OperationalError:
        db.rollback()
        raise errors.service_busy(retry_after=60)

    # Answers with no location at all are still stored: they are evidence that a
    # user engaged, and discarding them would bias the response-rate statistics.
    # They are simply excluded from region aggregation, which needs a location.
    return AcceptedResponse(accepted=True)
