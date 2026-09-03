"""GET /alerts/active -- the source of truth for what the user is told.

A push notification is only a trigger; the app re-fetches this endpoint on
receiving one (brief sec 8). An alert must therefore be queryable here BEFORE
its push is sent. alerts_service.publish() enforces that ordering.
"""

from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from .. import geo
from ..config import settings
from ..db import utcnow
from ..deps import CurrentUser, DbSession
from ..models import Alert, AlertRegion, LocationPing
from ..schemas import ActiveAlertResponse, AlertOut

router = APIRouter(prefix="/alerts", tags=["alerts"])

SEVERITY_RANK = {"low": 1, "moderate": 2, "high": 3}


@router.get("/active", response_model=ActiveAlertResponse)
def active_alert(
    user: CurrentUser,
    db: DbSession,
    latitude: float | None = Query(default=None, ge=-90, le=90),
    longitude: float | None = Query(default=None, ge=-180, le=180),
) -> ActiveAlertResponse:
    if latitude is None or longitude is None:
        # The client omits the query params when it has no fix. Rather than
        # returning nothing, fall back to the user's last known ping -- the same
        # fallback /feedback uses, and the reason we collect pings at all.
        last = db.scalar(
            select(LocationPing)
            .where(LocationPing.user_id == user.id)
            .order_by(LocationPing.recorded_at.desc())
            .limit(1)
        )
        if last is None:
            return ActiveAlertResponse(alert=None)
        latitude, longitude = last.latitude, last.longitude

    alert = find_active_alert_for(db, latitude, longitude)
    if alert is None:
        return ActiveAlertResponse(alert=None)

    return ActiveAlertResponse(
        alert=AlertOut(
            id=alert.id,
            severity=alert.severity,
            title=alert.title,
            message=alert.message,
            issuedAt=alert.issued_at,
        )
    )


def find_active_alert_for(db, latitude: float, longitude: float) -> Alert | None:
    """The most severe live alert covering this point, most recent as tiebreak.

    Matching is by geohash prefix: an alert covers a set of precision-N cells,
    and a point is inside when its own geohash starts with one of them. That
    keeps the lookup a plain indexed string comparison, with no PostGIS.
    """
    now = utcnow()
    point_hash = geo.encode(latitude, longitude, precision=settings.geohash_precision)

    # Cells at coarser precision than the stored region also match, so an alert
    # can be issued at precision 4 ("this basin") or 5 ("this town") without any
    # change here.
    prefixes = [point_hash[:i] for i in range(1, len(point_hash) + 1)]

    rows = db.scalars(
        select(Alert)
        .join(AlertRegion, AlertRegion.alert_id == Alert.id)
        .where(
            Alert.retracted_at.is_(None),
            Alert.expires_at > now,
            Alert.issued_at <= now,
            AlertRegion.geohash.in_(prefixes),
        )
    ).unique().all()

    if not rows:
        return None

    rows.sort(key=lambda a: (SEVERITY_RANK.get(a.severity, 3), a.issued_at), reverse=True)
    return rows[0]
