"""FastAPI dependencies: database session, authenticated user, admin guard."""

from __future__ import annotations

from typing import Annotated, Iterator

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from . import errors, security
from .config import settings
from .db import SessionLocal
from .models import User


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DbSession = Annotated[Session, Depends(get_db)]


def current_user(
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Resolve `Authorization: Bearer <token>` to a user.

    Every 401 in the whole application comes from here. That is on purpose: a
    401 outside /auth/* makes the client drop its session AND discard up to 50
    queued location pings (brief 5.2), so it must only ever mean "this token is
    genuinely not valid".
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise errors.unauthorized("Please sign in to continue.")

    claims = security.decode_token(authorization.split(" ", 1)[1].strip())
    if not claims or not claims.get("sub"):
        raise errors.unauthorized()

    user = db.get(User, claims["sub"])
    if user is None:
        # Account deleted while the token was still in date.
        raise errors.unauthorized("This account is no longer active. Please sign in again.")

    if security.token_issued_before(claims, user.tokens_valid_from):
        raise errors.unauthorized()

    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_admin(x_admin_token: Annotated[str | None, Header()] = None) -> str:
    """Guard for /admin/*.

    A single shared token rather than a second user system. Three researchers do
    not need role-based access control, and the alternative would be an
    abstraction with one implementation. Every admin decision is nevertheless
    attributed -- see the `operator` field on the decision endpoints.
    """
    if not x_admin_token or not security.constant_time_equals(x_admin_token, settings.admin_token):
        raise errors.ApiError(403, errors.FORBIDDEN, "Administrator access is required.")
    return x_admin_token


AdminGuard = Annotated[str, Depends(require_admin)]
