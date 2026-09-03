"""Firebase Cloud Messaging.

The server sends; the app only receives. Guarded exactly like the client's
Firebase.initializeApp(): a missing service account disables push and logs it,
rather than blocking startup. Alerts still reach users by polling
GET /alerts/active in the meantime.

NEVER commit the service account JSON. Point FIREBASE_CREDENTIALS_FILE at a path
outside the repo (docker-compose mounts ./secrets, which is gitignored).
"""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import utcnow
from .models import Alert, AlertRegion, Device, LocationPing

log = logging.getLogger(__name__)

_app = None
_messaging = None
_init_attempted = False


def _init() -> bool:
    """Lazily initialise the Admin SDK. Returns False when push is disabled."""
    global _app, _messaging, _init_attempted
    if _init_attempted:
        return _messaging is not None
    _init_attempted = True

    if not settings.firebase_credentials_file:
        log.info("Push disabled: FIREBASE_CREDENTIALS_FILE is not set.")
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials, messaging

        cred = credentials.Certificate(settings.firebase_credentials_file)
        _app = firebase_admin.initialize_app(cred)
        _messaging = messaging
        log.info("Push enabled via Firebase Admin SDK.")
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("Push disabled: could not initialise Firebase Admin SDK: %s", exc)
        return False


def tokens_for_alert(db: Session, alert: Alert, lookback_hours: int = 6) -> list[Device]:
    """Devices belonging to users seen in any of the alert's regions.

    "Seen recently" rather than "registered", because the point is to warn people
    who are actually there. A user who left the region three days ago does not
    need to be woken at 3am.
    """
    prefixes = [r.geohash for r in alert.regions]
    if not prefixes:
        return []

    since = utcnow().replace(microsecond=0)
    from datetime import timedelta

    since = since - timedelta(hours=lookback_hours)

    conditions = [LocationPing.geohash.startswith(p) for p in prefixes]
    from sqlalchemy import or_

    user_ids = {
        row[0]
        for row in db.execute(
            select(LocationPing.user_id).where(
                or_(*conditions), LocationPing.recorded_at >= since
            ).distinct()
        )
    }
    if not user_ids:
        return []

    return list(db.scalars(select(Device).where(Device.user_id.in_(user_ids))))


def send_alert(db: Session, alert: Alert) -> dict:
    """Fan out one alert. Always returns a result dict; never raises.

    The push is a trigger, not the payload. The app navigates Home and re-fetches
    GET /alerts/active on receipt, so the alert row must already be queryable --
    alerts_service.publish() commits it before calling this.
    """
    devices = tokens_for_alert(db, alert)
    if not devices:
        return {"enabled": _init(), "targeted": 0, "sent": 0, "failed": 0, "note": "no devices in region"}

    if not _init():
        return {
            "enabled": False,
            "targeted": len(devices),
            "sent": 0,
            "failed": 0,
            "note": "push disabled; users will receive this on next /alerts/active poll",
        }

    message = _messaging.MulticastMessage(
        tokens=[d.fcm_token for d in devices],
        notification=_messaging.Notification(title=alert.title, body=alert.message),
        data={
            # Exactly the payload the app expects (brief sec 8). All values must
            # be strings -- FCM rejects a data map with non-string values.
            "type": "flood_alert",
            "alertId": alert.id,
            "severity": alert.severity,
        },
        android=_messaging.AndroidConfig(priority="high"),
        apns=_messaging.APNSConfig(
            headers={"apns-priority": "10"},
            payload=_messaging.APNSPayload(aps=_messaging.Aps(sound="default")),
        ),
    )

    try:
        response = _messaging.send_each_for_multicast(message)
    except Exception as exc:  # noqa: BLE001
        log.error("FCM send failed for alert %s: %s", alert.id, exc)
        return {"enabled": True, "targeted": len(devices), "sent": 0, "failed": len(devices), "error": str(exc)}

    stale = _prune_stale_tokens(db, devices, response.responses)
    return {
        "enabled": True,
        "targeted": len(devices),
        "sent": response.success_count,
        "failed": response.failure_count,
        "prunedTokens": stale,
    }


def _prune_stale_tokens(db: Session, devices: list[Device], responses: Iterable) -> int:
    """Delete tokens FCM reports as unregistered (brief sec 8)."""
    removed = 0
    for device, result in zip(devices, responses):
        if getattr(result, "success", False):
            continue
        exception = getattr(result, "exception", None)
        name = type(exception).__name__ if exception else ""
        if name in {"UnregisteredError", "SenderIdMismatchError"}:
            db.delete(device)
            removed += 1
    if removed:
        db.commit()
    return removed
