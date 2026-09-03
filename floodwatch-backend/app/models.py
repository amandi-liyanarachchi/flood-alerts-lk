"""Every table in one file.

The brief asks for one obvious file over three clever ones. The whole schema is
under 200 lines, so it lives here where it can be read top to bottom.

Groups, in order:
  1. people        users, consent, devices
  2. crowdsourced  location_pings, feedback
  3. physical      gauge_stations, gauge_readings, rainfall_observations
  4. model output  region_risk_snapshots, alert_proposals, alerts, alert_regions
  5. evaluation    flood_events (ground truth)
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow

# ---------------------------------------------------------------------------
# 1. People
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    # nic is stored ONLY in normalised form: whitespace stripped, uppercased.
    # The client normalises identically before sending (brief 5.1). Storing raw
    # input would let "912345678v" and "912345678V" fork into two accounts.
    nic: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    first_name: Mapped[str] = mapped_column(String(50), nullable=False)
    last_name: Mapped[str] = mapped_column(String(50), nullable=False)
    phone: Mapped[str] = mapped_column(String(15), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # Bumped on password change or deletion request; tokens issued before this
    # instant are rejected. The only revocation mechanism available given that
    # the client holds a 30-day token it cannot refresh (brief 5.6).
    tokens_valid_from: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    devices: Mapped[list["Device"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    consents: Mapped[list["ConsentRecord"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class ConsentRecord(Base):
    """Informed consent for a human-subjects study.

    Required by the ethics protocol and by Sri Lanka's PDPA (Act No. 9 of 2022).
    Consent is versioned: re-issuing the notice requires re-consent, and the
    stored version is what the participant actually saw.
    """

    __tablename__ = "consent_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    notice_version: Mapped[str] = mapped_column(String(40), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship(back_populates="consents")


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    fcm_token: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    platform: Mapped[str] = mapped_column(String(10), nullable=False)  # android | ios
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    user: Mapped[User] = relationship(back_populates="devices")


# ---------------------------------------------------------------------------
# 2. Crowdsourced input
# ---------------------------------------------------------------------------


class LocationPing(Base):
    """144 rows per user per day at steady state. Append-only time series.

    Ordered and bucketed on `recorded_at`, never on `received_at`: pings arrive
    late, out of order and duplicated after an outage (brief 5.4).
    """

    __tablename__ = "location_pings"
    __table_args__ = (
        # The natural key the brief suggests. Makes a replayed ping a no-op
        # rather than a duplicate row, without a contract change.
        UniqueConstraint("user_id", "recorded_at", "source", name="uq_ping_natural_key"),
        Index("ix_pings_region_time", "geohash", "recorded_at"),
        Index("ix_pings_user_time", "user_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # auto | manual
    geohash: Mapped[str] = mapped_column(String(12), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # Seconds between recording and arrival. Kept because the brief predicts up
    # to ~50 minutes of lag and the paper should be able to report the real
    # distribution rather than the assumed one.
    lag_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    in_country: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Feedback(Base):
    """One Yes/No answer to "Is there flooding in your area right now?".

    Users may resubmit at any time; every submission is kept for the audit trail,
    but aggregation counts only the latest answer per user per window, so a
    single enthusiastic user cannot swing a region (brief 6.2).
    """

    __tablename__ = "feedback"
    __table_args__ = (
        Index("ix_feedback_region_time", "geohash", "answered_at"),
        Index("ix_feedback_user_time", "user_id", "answered_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    flood_present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    answered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    geohash: Mapped[str | None] = mapped_column(String(12), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # True when lat/lon were null on the wire and we substituted the user's last
    # known ping. Recorded because a fallback location is weaker evidence and the
    # paper should be able to exclude it.
    location_inferred: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# ---------------------------------------------------------------------------
# 3. Physical observations (Irrigation Department + rainfall)
# ---------------------------------------------------------------------------


class GaugeStation(Base):
    """Master list from hydrostations/FeatureServer/0.

    Treated as authoritative for flood thresholds. The gauges_2_view layer
    carries its own alertpull/minorpull/majorpull which disagree for some
    stations (Glencourse minor flood: 15.5 there vs 16 here); the ingester logs
    the mismatch rather than silently picking one.
    """

    __tablename__ = "gauge_stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    station: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    basin: Mapped[str | None] = mapped_column(String(80), nullable=True)
    tributary: Mapped[str | None] = mapped_column(String(80), nullable=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    # Always metres. Stations published in feet (e.g. Nagalagam Street) are
    # converted at ingest so nothing downstream has to know about units.
    alert_level_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    minor_flood_level_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    major_flood_level_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_unit: Mapped[str] = mapped_column(String(4), default="m", nullable=False)
    elevation_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    geohash: Mapped[str] = mapped_column(String(12), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class GaugeReading(Base):
    """A single observation from gauges_2_view/FeatureServer/0."""

    __tablename__ = "gauge_readings"
    __table_args__ = (
        UniqueConstraint("station", "observed_at", name="uq_reading_natural_key"),
        Index("ix_readings_station_time", "station", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    station: Mapped[str] = mapped_column(String(80), nullable=False)
    basin: Mapped[str | None] = mapped_column(String(80), nullable=True)
    water_level_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    rainfall_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class RainfallObservation(Base):
    """Gridded rainfall for one region cell, from Open-Meteo.

    Open-Meteo is the pragmatic choice for a prototype: free, no key, hourly, and
    it exposes both recent past and short forecast in one call. GPM IMERG and
    CHIRPS are the citable sources for the paper's retrospective analysis; see
    DESIGN.md sec 5.
    """

    __tablename__ = "rainfall_observations"
    __table_args__ = (
        UniqueConstraint("geohash", "observed_at", name="uq_rainfall_natural_key"),
        Index("ix_rainfall_region_time", "geohash", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    geohash: Mapped[str] = mapped_column(String(12), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    precipitation_mm: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    is_forecast: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# 4. Model output
# ---------------------------------------------------------------------------


class RegionRiskSnapshot(Base):
    """One row per region per evaluation run. This is the paper's raw material:
    every score the model ever produced, with the inputs that produced it."""

    __tablename__ = "region_risk_snapshots"
    __table_args__ = (Index("ix_snapshot_region_time", "geohash", "computed_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    geohash: Mapped[str] = mapped_column(String(12), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    engine: Mapped[str] = mapped_column(String(40), nullable=False)  # e.g. "rules_v1", "rainfall_only"
    score: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str | None] = mapped_column(String(10), nullable=True)  # low|moderate|high|None
    # Every feature and every contribution, so a score is reconstructable a year
    # later without re-running anything.
    features: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class AlertProposal(Base):
    """The model proposes; a human disposes.

    Official public flood warnings are the Disaster Management Centre's remit,
    and a false alarm trains people to ignore the app. Nothing reaches a phone
    without an operator approving this row (brief 6.5).
    """

    __tablename__ = "alert_proposals"
    __table_args__ = (Index("ix_proposal_status_time", "status", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    geohash: Mapped[str] = mapped_column(String(12), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="proposed", nullable=False)
    # proposed | approved | dismissed | superseded
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    alert_id: Mapped[str | None] = mapped_column(String(40), nullable=True)


class Alert(Base):
    """What GET /alerts/active returns. The push is only a trigger; this row is
    the source of truth, so it must exist and be queryable BEFORE any push is
    sent (brief sec 8)."""

    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_active", "retracted_at", "expires_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)  # low|moderate|high
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    retracted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    issued_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    proposal_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    push_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    push_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    regions: Mapped[list["AlertRegion"]] = relationship(
        back_populates="alert", cascade="all, delete-orphan", lazy="selectin"
    )


class AlertRegion(Base):
    """The region cells an alert covers. A user is inside the alert when their
    geohash starts with one of these prefixes."""

    __tablename__ = "alert_regions"
    __table_args__ = (
        UniqueConstraint("alert_id", "geohash", name="uq_alert_region"),
        Index("ix_alert_region_geohash", "geohash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False)
    geohash: Mapped[str] = mapped_column(String(12), nullable=False)

    alert: Mapped[Alert] = relationship(back_populates="regions")


# ---------------------------------------------------------------------------
# 5. Evaluation ground truth
# ---------------------------------------------------------------------------


class FloodEvent(Base):
    """A flood that actually happened, entered by hand from DMC situation
    reports or news archives. Without this table there is no precision, no
    recall, and no paper (brief 6.6)."""

    __tablename__ = "flood_events"
    __table_args__ = (Index("ix_events_region_time", "geohash", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    geohash: Mapped[str] = mapped_column(String(12), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    severity: Mapped[str | None] = mapped_column(String(10), nullable=True)
    source: Mapped[str] = mapped_column(String(120), nullable=False)  # citation
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
