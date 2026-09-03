"""POST /auth/register and POST /auth/login -- both public."""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import errors, security
from ..config import settings
from ..db import utcnow
from ..deps import DbSession
from ..models import ConsentRecord, User
from ..schemas import AuthResponse, LoginRequest, RegisterRequest, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _auth_response(user: User) -> AuthResponse:
    return AuthResponse(
        token=security.create_token(user.id),
        user=UserOut(
            id=user.id,
            nic=user.nic,
            firstName=user.first_name,
            lastName=user.last_name,
            phone=user.phone,
        ),
    )


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=AuthResponse)
def register(payload: RegisterRequest, db: DbSession) -> AuthResponse:
    # payload.nic is already whitespace-stripped and uppercased by the schema
    # validator, matching what the client sends (brief 5.1).
    existing = db.scalar(select(User).where(User.nic == payload.nic))
    if existing is not None:
        raise errors.ApiError(
            409,
            errors.NIC_ALREADY_REGISTERED,
            "An account already exists for this NIC. Please sign in instead.",
        )

    user = User(
        id=security.new_user_id(),
        nic=payload.nic,
        first_name=payload.firstName,
        last_name=payload.lastName,
        phone=payload.phone,
        password_hash=security.hash_password(payload.password),
    )
    db.add(user)

    # The client has no consent screen yet (brief 5, "What the app does NOT
    # do"), so registration cannot itself be evidence of informed consent. We
    # record a placeholder marking consent as OUTSTANDING; POST /consent
    # supersedes it once the screen ships. Nothing here pretends consent was
    # given.
    db.add(
        ConsentRecord(
            user_id=user.id,
            notice_version="not-collected",
            granted_at=utcnow(),
            withdrawn_at=utcnow(),  # withdrawn == not currently in force
        )
    )

    try:
        db.commit()
    except IntegrityError:
        # Two registrations for the same NIC raced. The unique index is the
        # real guarantee; the SELECT above is just a friendlier path to it.
        db.rollback()
        raise errors.ApiError(
            409,
            errors.NIC_ALREADY_REGISTERED,
            "An account already exists for this NIC. Please sign in instead.",
        )

    db.refresh(user)
    return _auth_response(user)


@router.post("/login", response_model=AuthResponse)
def login(payload: LoginRequest, db: DbSession) -> AuthResponse:
    user = db.scalar(select(User).where(User.nic == payload.nic))

    # 401 + INVALID_CREDENTIALS is the contract. On /auth/* the client treats a
    # 401 as "wrong credentials" and simply shows error.message; it does NOT
    # tear down the session (brief 5.2).
    if user is None or not security.verify_password(payload.password, user.password_hash):
        # Identical response either way, so the endpoint cannot be used to
        # enumerate which NICs are registered -- a real concern when the
        # identifier is a national ID number.
        raise errors.invalid_credentials()

    return _auth_response(user)
