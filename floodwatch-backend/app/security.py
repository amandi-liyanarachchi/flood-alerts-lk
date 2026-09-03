"""Password hashing and JWT issuing.

The client holds a 30-day access token with no refresh and no rotation (brief
5.6). That is a deliberate simplification of the mobile app, and it means the
only revocation lever the server has is `users.tokens_valid_from`: bump it and
every token issued before that instant stops working.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import settings
from .db import utcnow


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # A malformed hash is a data problem, not a valid login.
        return False


def new_user_id() -> str:
    return "u_" + secrets.token_hex(8)


def new_alert_id() -> str:
    return "a_" + secrets.token_hex(6)


def create_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.jwt_ttl_days)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    """Return the claims, or None if the token is invalid or expired.

    Returning None rather than raising keeps the caller honest: the only place
    that turns "no claims" into a 401 is deps.current_user, so a 401 can never
    escape from somewhere unexpected and wipe a user's queued pings.
    """
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None


def token_issued_before(claims: dict, cutoff: datetime) -> bool:
    """True if this token predates the user's tokens_valid_from cutoff."""
    issued = claims.get("iat")
    if issued is None:
        return True
    issued_dt = datetime.fromtimestamp(issued, tz=timezone.utc).replace(tzinfo=None)
    # One second of slack: iat is a whole second, tokens_valid_from is not.
    return issued_dt < cutoff.replace(microsecond=0) - timedelta(seconds=1)


def constant_time_equals(a: str, b: str) -> bool:
    return secrets.compare_digest(a or "", b or "")


__all__ = [
    "hash_password",
    "verify_password",
    "new_user_id",
    "new_alert_id",
    "create_token",
    "decode_token",
    "token_issued_before",
    "constant_time_equals",
    "utcnow",
]
