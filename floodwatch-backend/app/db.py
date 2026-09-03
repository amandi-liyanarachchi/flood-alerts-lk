"""Database engine, session factory and the declarative base.

All datetimes are stored as *naive UTC*. Storing naive-UTC keeps SQLite (tests)
and PostgreSQL (everything else) behaving identically, and the API layer appends
the "Z" on the way out, so the contract's "all timestamps are UTC ISO-8601"
holds. Nothing in this codebase may store a local time.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Current time as a naive UTC datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(value: datetime) -> datetime:
    """Normalise any datetime to naive UTC.

    The client sends "2026-08-28T09:15:00Z", which pydantic parses as
    tz-aware. A malformed client could send a local offset; convert rather than
    reject, because rejecting means a 4xx and a 4xx destroys the ping.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def init_db() -> None:
    """Create tables. Adequate for a prototype; swap for Alembic if the schema
    starts changing under a deployed pilot."""
    from . import models  # noqa: F401  (registers the mappers)

    Base.metadata.create_all(bind=engine)
