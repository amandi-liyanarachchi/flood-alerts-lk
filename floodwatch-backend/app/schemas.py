"""Request and response shapes.

The request models mirror section 4 of the brief exactly. The client already
enforces these rules; we re-enforce them because a client is not a security
boundary, but the rules must MATCH or valid users get rejected.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

# Old format: 9 digits then V or W. Some older NICs end in X; per the research
# requirement only V/W are accepted.
NIC_OLD = re.compile(r"^\d{9}[VW]$")
# New format: exactly 12 digits.
NIC_NEW = re.compile(r"^\d{12}$")
PHONE = re.compile(r"^07\d{8}$")
NAME = re.compile(r"^[A-Za-zÀ-ɏ' \-]{1,50}$")

# bcrypt silently truncates beyond 72 bytes, which would make two different
# passwords equivalent. Reject rather than truncate.
MAX_PASSWORD_BYTES = 72


def iso_z(value: datetime | None) -> str | None:
    """Naive-UTC datetime -> "2026-08-28T09:15:00Z"."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(microsecond=0).isoformat() + "Z"


def normalise_nic(raw: str) -> str:
    """Strip ALL whitespace and uppercase -- identical to the client (brief 5.1).

    Uniqueness is enforced on this form. Storing raw input would let
    "912345678v" and "912345678V" become two accounts for the same person.
    """
    return re.sub(r"\s+", "", raw or "").upper()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class RegisterRequest(BaseModel):
    nic: str
    firstName: str
    lastName: str
    phone: str
    password: str

    @field_validator("nic")
    @classmethod
    def _nic(cls, v: str) -> str:
        v = normalise_nic(v)
        if not (NIC_OLD.match(v) or NIC_NEW.match(v)):
            raise ValueError(
                "Enter a valid NIC: either 9 digits followed by V or W, or 12 digits."
            )
        return v

    @field_validator("firstName", "lastName")
    @classmethod
    def _name(cls, v: str) -> str:
        v = (v or "").strip()
        if not NAME.match(v):
            raise ValueError("Names may only contain letters, spaces, hyphens and apostrophes.")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        v = re.sub(r"\s+", "", v or "")
        if not PHONE.match(v):
            raise ValueError("Enter a valid Sri Lankan mobile number, for example 0712345678.")
        return v

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        if len(v or "") < 8:
            raise ValueError("Your password must be at least 8 characters long.")
        if len(v.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError("Your password is too long. Please use 72 characters or fewer.")
        return v


class LoginRequest(BaseModel):
    nic: str
    password: str

    @field_validator("nic")
    @classmethod
    def _nic(cls, v: str) -> str:
        # Deliberately NOT format-validated. A badly formatted NIC at login is a
        # wrong credential, not a validation failure -- the client shows
        # error.message either way, and 401 INVALID_CREDENTIALS is the honest
        # answer that does not leak which NICs exist.
        return normalise_nic(v)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    nic: str
    firstName: str
    lastName: str
    phone: str


class AuthResponse(BaseModel):
    token: str
    user: UserOut


# ---------------------------------------------------------------------------
# Locations and feedback
# ---------------------------------------------------------------------------


class LocationRequest(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy: float | None = None
    recordedAt: datetime
    source: Literal["auto", "manual"] = "auto"


class FeedbackRequest(BaseModel):
    floodPresent: bool
    # May be null when the device had no fix; we fall back to the user's last
    # known ping (brief sec 3).
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    answeredAt: datetime


class AcceptedResponse(BaseModel):
    accepted: bool = True


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    severity: str
    title: str
    message: str
    issuedAt: datetime

    @field_serializer("issuedAt")
    def _issued(self, v: datetime) -> str:
        return iso_z(v)


class ActiveAlertResponse(BaseModel):
    alert: AlertOut | None = None


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------


class FcmTokenRequest(BaseModel):
    fcmToken: str = Field(min_length=1, max_length=512)
    platform: Literal["android", "ios"]


class FcmTokenDeleteRequest(BaseModel):
    fcmToken: str = Field(min_length=1, max_length=512)


# ---------------------------------------------------------------------------
# Consent and account (new endpoints -- the client does not call these yet)
# ---------------------------------------------------------------------------


class ConsentRequest(BaseModel):
    noticeVersion: str = Field(min_length=1, max_length=40)
    granted: bool = True


class ConsentStatusResponse(BaseModel):
    currentVersion: str
    granted: bool
    grantedVersion: str | None = None
    grantedAt: str | None = None


class DeleteAccountRequest(BaseModel):
    """Deletion is irreversible, so it is password-confirmed."""

    password: str
    reason: str | None = Field(default=None, max_length=500)
