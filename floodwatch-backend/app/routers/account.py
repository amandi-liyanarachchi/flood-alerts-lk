"""Consent and account deletion.

Neither of these endpoints exists in the client yet (brief sec 7 lists both as
missing everywhere). They are implemented server-side so the client work is
purely UI, and so the study is not collecting continuous location against a
national identity number with no way to record consent or honour a deletion
request.

Legal basis: Sri Lanka's Personal Data Protection Act No. 9 of 2022 (right of
erasure), Google Play's Data deletion policy, and App Store Guideline 5.1.1(v).
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from .. import errors, security
from ..config import settings
from ..db import utcnow
from ..deps import CurrentUser, DbSession
from ..models import ConsentRecord, Device, Feedback, LocationPing, User
from ..schemas import (
    AcceptedResponse,
    ConsentRequest,
    ConsentStatusResponse,
    DeleteAccountRequest,
    iso_z,
)

router = APIRouter(tags=["account"])


@router.get("/consent", response_model=ConsentStatusResponse)
def consent_status(user: CurrentUser, db: DbSession) -> ConsentStatusResponse:
    record = db.scalar(
        select(ConsentRecord)
        .where(ConsentRecord.user_id == user.id, ConsentRecord.withdrawn_at.is_(None))
        .order_by(ConsentRecord.granted_at.desc())
        .limit(1)
    )
    current = settings.consent_notice_version
    # Consent to an older version of the notice does not carry forward. If the
    # notice changed, the participant has not agreed to what is now being done.
    granted = record is not None and record.notice_version == current
    return ConsentStatusResponse(
        currentVersion=current,
        granted=granted,
        grantedVersion=record.notice_version if record else None,
        grantedAt=iso_z(record.granted_at) if record else None,
    )


@router.post("/consent", response_model=AcceptedResponse)
def record_consent(payload: ConsentRequest, user: CurrentUser, db: DbSession) -> AcceptedResponse:
    now = utcnow()

    if not payload.granted:
        # Withdrawal. Every standing consent is closed off; the participant's
        # data stops being collected from the next tick. Withdrawing consent is
        # not the same as deleting the account, so nothing is erased here --
        # DELETE /account is the separate, explicit act.
        for record in db.scalars(
            select(ConsentRecord).where(
                ConsentRecord.user_id == user.id, ConsentRecord.withdrawn_at.is_(None)
            )
        ):
            record.withdrawn_at = now
        db.commit()
        return AcceptedResponse(accepted=True)

    if payload.noticeVersion != settings.consent_notice_version:
        raise errors.validation_failed(
            "This consent notice is out of date. Please reopen the app and read the current notice."
        )

    db.add(
        ConsentRecord(user_id=user.id, notice_version=payload.noticeVersion, granted_at=now)
    )
    db.commit()
    return AcceptedResponse(accepted=True)


@router.delete("/account", response_model=AcceptedResponse)
def delete_account(payload: DeleteAccountRequest, user: CurrentUser, db: DbSession) -> AcceptedResponse:
    """Irreversible erasure of the participant and everything keyed to them."""
    if not security.verify_password(payload.password, user.password_hash):
        # Deliberately NOT a 401: a 401 would tear the session down and hide the
        # real reason. The password was wrong, the session is fine.
        raise errors.validation_failed("That password is incorrect. Please try again.")

    user_id = user.id

    # Explicit deletes rather than relying on cascade, so the erasure is visible
    # in this file and auditable in a data-protection review.
    db.query(LocationPing).filter(LocationPing.user_id == user_id).delete(synchronize_session=False)
    db.query(Feedback).filter(Feedback.user_id == user_id).delete(synchronize_session=False)
    db.query(Device).filter(Device.user_id == user_id).delete(synchronize_session=False)
    db.query(ConsentRecord).filter(ConsentRecord.user_id == user_id).delete(synchronize_session=False)
    db.query(User).filter(User.id == user_id).delete(synchronize_session=False)
    db.commit()

    # Any alert already issued stays: it is a public safety record, and it holds
    # no personal data. Region risk snapshots likewise aggregate over users and
    # never name one.
    return AcceptedResponse(accepted=True)
