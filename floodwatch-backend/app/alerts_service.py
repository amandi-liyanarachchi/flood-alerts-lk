"""Publishing and retracting alerts.

The one invariant this module exists to protect: an alert is COMMITTED and
QUERYABLE at GET /alerts/active before any push is sent. The app treats the push
as a trigger and immediately re-fetches that endpoint (brief sec 8); sending
first would race the user to an empty response and show them a notification about
an alert that does not exist yet.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from . import push, security
from .db import utcnow
from .models import Alert, AlertProposal, AlertRegion

log = logging.getLogger(__name__)

# How long an approved alert stays live without being renewed. Chosen so that a
# forgotten alert expires on its own rather than warning people about a flood
# that drained yesterday.
DEFAULT_TTL_HOURS = {"low": 6, "moderate": 6, "high": 12}


def publish(
    db: Session,
    proposal: AlertProposal,
    operator: str,
    ttl_hours: int | None = None,
    send_push: bool = True,
) -> Alert:
    """Approve a proposal and make it live."""
    now = utcnow()
    hours = ttl_hours or DEFAULT_TTL_HOURS.get(proposal.severity, 6)

    alert = Alert(
        id=security.new_alert_id(),
        severity=proposal.severity,
        title=proposal.title,
        message=proposal.message,
        issued_at=now,
        expires_at=now + timedelta(hours=hours),
        issued_by=operator,
        proposal_id=proposal.id,
    )
    db.add(alert)
    db.flush()
    db.add(AlertRegion(alert_id=alert.id, geohash=proposal.geohash))

    proposal.status = "approved"
    proposal.decided_at = now
    proposal.decided_by = operator
    proposal.alert_id = alert.id

    # Commit BEFORE pushing. This ordering is the whole point of the module.
    db.commit()
    db.refresh(alert)

    if send_push:
        result = push.send_alert(db, alert)
        alert.push_sent_at = utcnow()
        alert.push_result = result
        db.commit()
        log.info("Alert %s published by %s: %s", alert.id, operator, result)

    return alert


def dismiss(db: Session, proposal: AlertProposal, operator: str, note: str | None = None) -> None:
    """Reject a proposal. The row stays: a dismissal is a labelled negative and
    is worth as much to the evaluation as an approval."""
    proposal.status = "dismissed"
    proposal.decided_at = utcnow()
    proposal.decided_by = operator
    proposal.decision_note = note
    db.commit()


def retract(db: Session, alert: Alert, operator: str) -> None:
    """Take a live alert down.

    No "all clear" push is sent. The app has no concept of one, and inventing a
    message type would be a contract change. Users see the alert disappear on
    their next poll or pull-to-refresh.
    """
    alert.retracted_at = utcnow()
    alert.issued_by = alert.issued_by or operator
    db.commit()
    log.info("Alert %s retracted by %s", alert.id, operator)
