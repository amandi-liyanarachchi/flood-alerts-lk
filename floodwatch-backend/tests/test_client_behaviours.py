"""Section 5 of the brief, one test per behaviour.

These are the tests that matter. Everything here encodes a consequence of how the
Flutter client is implemented, and each one, if broken, causes silent data loss
rather than a visible failure.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import func, select

from app import ratelimit
from app.db import utcnow
from app.models import Feedback, LocationPing
from tests.conftest import register

PING = {
    "latitude": 6.9271,
    "longitude": 79.8612,
    "accuracy": 12.5,
    "recordedAt": "2026-08-28T09:15:00Z",
    "source": "auto",
}


# --- 5.3 the status code decides whether data survives ---------------------


def test_accepted_ping_returns_201_accepted_true(client, auth):
    response = client.post("/api/v1/locations", json=PING, headers=auth)
    assert response.status_code == 201
    assert response.json() == {"accepted": True}


def test_transient_database_failure_returns_503_not_4xx(client, auth):
    """A 4xx here makes the client delete the ping forever. It must be a 5xx."""
    from sqlalchemy.exc import OperationalError

    with patch(
        "sqlalchemy.orm.Session.commit",
        side_effect=OperationalError("deadlock detected", None, Exception()),
    ):
        response = client.post("/api/v1/locations", json=PING, headers=auth)

    assert response.status_code == 503, "transient failure must be retryable"
    assert 500 <= response.status_code < 600
    assert "Retry-After" in response.headers


def test_backpressure_is_503_never_429(client, auth):
    """429 is a 4xx. The client would drop the ping permanently (brief 5.3)."""
    ratelimit.reset()
    seen = set()
    for minute in range(130):
        body = dict(PING, recordedAt=f"2026-08-28T09:{minute % 60:02d}:{minute // 60:02d}Z")
        seen.add(client.post("/api/v1/locations", json=body, headers=auth).status_code)

    assert 429 not in seen, "429 would destroy location data during a flood"
    assert 503 in seen, "the limit should have been reached"


def test_unhandled_error_stays_a_5xx(client, auth):
    """An internal bug must not become a 4xx just because it looks tidier."""
    with patch("app.routers.locations.geo.encode", side_effect=RuntimeError("boom")):
        response = client.post("/api/v1/locations", json=PING, headers=auth)
    assert response.status_code >= 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "boom" not in response.text


def test_no_authenticated_endpoint_401s_for_a_transient_problem(client, auth):
    """A 401 outside /auth/* wipes the queue AND logs the user out (brief 5.2)."""
    from sqlalchemy.exc import OperationalError

    with patch(
        "sqlalchemy.orm.Session.commit",
        side_effect=OperationalError("connection reset", None, Exception()),
    ):
        assert client.post("/api/v1/locations", json=PING, headers=auth).status_code != 401


def test_missing_token_is_401(client):
    response = client.post("/api/v1/locations", json=PING)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_garbage_token_is_401(client):
    response = client.post(
        "/api/v1/locations", json=PING, headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert response.status_code == 401


# --- 5.4 pings arrive late, out of order and duplicated --------------------


def test_replayed_ping_is_idempotent_not_a_duplicate_row(client, auth, db):
    """The client retries when our 201 is lost after we committed."""
    for _ in range(3):
        assert client.post("/api/v1/locations", json=PING, headers=auth).status_code == 201

    assert db.scalar(select(func.count(LocationPing.id))) == 1


def test_manual_send_does_not_collide_with_the_auto_ping_at_the_same_instant(client, auth, db):
    """(user, recordedAt, source) is the key -- source is part of it because a
    manual send can share a timestamp with an automatic one."""
    client.post("/api/v1/locations", json=PING, headers=auth)
    client.post("/api/v1/locations", json=dict(PING, source="manual"), headers=auth)
    assert db.scalar(select(func.count(LocationPing.id))) == 2


def test_backlog_of_fifty_pings_all_survive(client, auth, db):
    """After an outage the client flushes up to 50 queued pings in a burst."""
    for minute in range(50):
        body = dict(PING, recordedAt=f"2026-08-28T08:{minute:02d}:00Z")
        assert client.post("/api/v1/locations", json=body, headers=auth).status_code == 201

    assert db.scalar(select(func.count(LocationPing.id))) == 50


def test_out_of_order_arrival_is_ordered_on_recorded_at(client, auth, db):
    """Arrival order is not chronological order -- a manual send overtakes the
    backlog. recordedAt is authoritative (brief 5.4)."""
    late = dict(PING, recordedAt="2026-08-28T09:00:00Z", source="auto")
    early = dict(PING, recordedAt="2026-08-28T08:00:00Z", source="manual")
    client.post("/api/v1/locations", json=late, headers=auth)
    client.post("/api/v1/locations", json=early, headers=auth)

    ordered = db.scalars(select(LocationPing).order_by(LocationPing.recorded_at)).all()
    assert [p.source for p in ordered] == ["manual", "auto"]
    assert ordered[0].recorded_at < ordered[1].recorded_at


def test_a_fifty_minute_old_ping_is_accepted_and_its_lag_recorded(client, auth, db):
    old = (utcnow() - timedelta(minutes=50)).replace(microsecond=0)
    body = dict(PING, recordedAt=old.isoformat() + "Z")
    assert client.post("/api/v1/locations", json=body, headers=auth).status_code == 201

    ping = db.scalars(select(LocationPing)).first()
    assert 2900 < ping.lag_seconds < 3100


# --- timestamps ------------------------------------------------------------


def test_timestamps_are_utc_iso8601_with_a_z(client, auth, db, admin_headers):
    """Brief 3: all timestamps are UTC ISO-8601 strings."""
    client.post(
        "/api/v1/admin/alerts/manual",
        headers=admin_headers,
        json={
            "region": "tc3f2",
            "severity": "high",
            "title": "Flood risk in your area",
            "message": "Move to higher ground.",
            "operator": "tester",
        },
    )
    alerts = client.get("/api/v1/admin/alerts", headers=admin_headers).json()["alerts"]
    assert alerts[0]["issuedAt"].endswith("Z")
    assert "+00:00" not in alerts[0]["issuedAt"]


def test_offset_timestamps_are_converted_not_rejected(client, auth, db):
    """A client sending +05:30 must not have its ping destroyed by a 4xx."""
    body = dict(PING, recordedAt="2026-08-28T14:45:00+05:30")
    assert client.post("/api/v1/locations", json=body, headers=auth).status_code == 201
    ping = db.scalars(select(LocationPing)).first()
    assert ping.recorded_at.hour == 9 and ping.recorded_at.minute == 15


# --- feedback --------------------------------------------------------------


def test_feedback_without_coordinates_falls_back_to_last_ping(client, auth, db):
    client.post("/api/v1/locations", json=PING, headers=auth)
    response = client.post(
        "/api/v1/feedback",
        headers=auth,
        json={
            "floodPresent": True,
            "latitude": None,
            "longitude": None,
            "answeredAt": "2026-08-28T09:20:00Z",
        },
    )
    assert response.status_code == 201

    entry = db.scalars(select(Feedback)).first()
    assert entry.latitude == PING["latitude"]
    assert entry.location_inferred is True


def test_feedback_with_no_ping_and_no_fix_is_still_accepted(client, auth, db):
    """Discarding it would bias the response-rate statistics."""
    response = client.post(
        "/api/v1/feedback",
        headers=auth,
        json={
            "floodPresent": False,
            "latitude": None,
            "longitude": None,
            "answeredAt": "2026-08-28T09:20:00Z",
        },
    )
    assert response.status_code == 201
    entry = db.scalars(select(Feedback)).first()
    assert entry.geohash is None


def test_users_may_resubmit_and_every_answer_is_kept(client, auth, db):
    for hour, answer in ((9, True), (10, False), (11, True)):
        client.post(
            "/api/v1/feedback",
            headers=auth,
            json={
                "floodPresent": answer,
                "latitude": 6.9271,
                "longitude": 79.8612,
                "answeredAt": f"2026-08-28T{hour:02d}:00:00Z",
            },
        )
    assert db.scalar(select(func.count(Feedback.id))) == 3


# --- 5.6 long-lived tokens -------------------------------------------------


def test_token_is_valid_for_thirty_days(client):
    import jwt

    from app.config import settings

    token = register(client, nic="199112345678").json()["token"]
    claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    days = (claims["exp"] - claims["iat"]) / 86400
    assert 29.9 < days < 30.1


# --- error envelope --------------------------------------------------------


def test_every_non_2xx_uses_the_envelope(client, auth):
    cases = [
        client.post("/api/v1/locations", json=PING),                       # 401
        client.post("/api/v1/auth/register", json={"nic": "x"}),           # 400
        client.get("/api/v1/admin/summary"),                               # 403
        client.get("/api/v1/nope"),                                        # 404
    ]
    for response in cases:
        body = response.json()
        assert set(body) == {"error"}, response.text
        assert set(body["error"]) == {"code", "message"}
        assert isinstance(body["error"]["message"], str) and body["error"]["message"]
