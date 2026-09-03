"""POST and DELETE /devices/fcm-token.

The app registers after login, after register, and on every FCM token refresh,
and deregisters on logout while the token is still valid.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db import utcnow
from ..deps import CurrentUser, DbSession
from ..models import Device
from ..schemas import AcceptedResponse, FcmTokenDeleteRequest, FcmTokenRequest

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("/fcm-token", response_model=AcceptedResponse)
def register_token(payload: FcmTokenRequest, user: CurrentUser, db: DbSession) -> AcceptedResponse:
    existing = db.scalar(select(Device).where(Device.fcm_token == payload.fcmToken))

    if existing is not None:
        # FCM reissues a token to whichever app install currently holds it, so
        # the same string can migrate between users on a shared handset. Move
        # it, do not duplicate it -- otherwise the previous user keeps getting
        # alerts for a phone they no longer use.
        existing.user_id = user.id
        existing.platform = payload.platform
        existing.last_seen_at = utcnow()
    else:
        db.add(
            Device(
                user_id=user.id,
                fcm_token=payload.fcmToken,
                platform=payload.platform,
                last_seen_at=utcnow(),
            )
        )

    try:
        db.commit()
    except IntegrityError:
        db.rollback()

    return AcceptedResponse(accepted=True)


@router.delete("/fcm-token", response_model=AcceptedResponse)
def unregister_token(payload: FcmTokenDeleteRequest, user: CurrentUser, db: DbSession) -> AcceptedResponse:
    device = db.scalar(
        select(Device).where(Device.fcm_token == payload.fcmToken, Device.user_id == user.id)
    )
    if device is not None:
        db.delete(device)
        db.commit()

    # Idempotent: deleting a token that is already gone is a success. A logout
    # must never fail because the server tidied up first.
    return AcceptedResponse(accepted=True)
