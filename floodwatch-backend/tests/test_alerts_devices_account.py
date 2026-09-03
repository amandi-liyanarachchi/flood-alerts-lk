"""Alerts, devices, consent and deletion."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app import geo
from app.config import settings
from app.db import utcnow
from app.models import Alert, AlertRegion, ConsentRecord, Device, LocationPing, User
from tests.conftest import register

COLOMBO = (6.9271, 79.8612)
KANDY = (7.2906, 80.6337)


def issue_alert(client, admin_headers, latitude, longitude, severity="high"):
    region = geo.encode(latitude, longitude, settings.geohash_precision)
    response = client.post(
        "/api/v1/admin/alerts/manual",
        headers=admin_headers,
        json={
            "region": region,
            "severity": severity,
            "title": "Flood risk in your area",
            "message": "Rising water levels reported nearby. Move to higher ground.",
            "operator": "tester",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["alertId"]


# --- GET /alerts/active ----------------------------------------------------


def test_no_alert_returns_null(client, auth):
    response = client.get(
        "/api/v1/alerts/active", params={"latitude": 6.9271, "longitude": 79.8612}, headers=auth
    )
    assert response.status_code == 200
    assert response.json() == {"alert": None}


def test_active_alert_matches_the_contract_shape(client, auth, admin_headers):
    issue_alert(client, admin_headers, *COLOMBO)
    body = client.get(
        "/api/v1/alerts/active",
        params={"latitude": COLOMBO[0], "longitude": COLOMBO[1]},
        headers=auth,
    ).json()

    alert = body["alert"]
    assert set(alert) == {"id", "severity", "title", "message", "issuedAt"}
    assert alert["id"].startswith("a_")
    assert alert["severity"] in {"low", "moderate", "high"}
    assert alert["issuedAt"].endswith("Z")


def test_alert_does_not_leak_into_another_region(client, auth, admin_headers):
    issue_alert(client, admin_headers, *COLOMBO)
    body = client.get(
        "/api/v1/alerts/active",
        params={"latitude": KANDY[0], "longitude": KANDY[1]},
        headers=auth,
    ).json()
    assert body["alert"] is None


def test_without_coordinates_falls_back_to_the_last_ping(client, auth, admin_headers):
    """The client omits the query params when it has no fix."""
    client.post(
        "/api/v1/locations",
        headers=auth,
        json={
            "latitude": COLOMBO[0],
            "longitude": COLOMBO[1],
            "accuracy": 10,
            "recordedAt": "2026-08-28T09:00:00Z",
            "source": "auto",
        },
    )
    issue_alert(client, admin_headers, *COLOMBO)

    assert client.get("/api/v1/alerts/active", headers=auth).json()["alert"] is not None


def test_most_severe_alert_wins(client, auth, admin_headers):
    issue_alert(client, admin_headers, *COLOMBO, severity="low")
    issue_alert(client, admin_headers, *COLOMBO, severity="high")
    alert = client.get(
        "/api/v1/alerts/active",
        params={"latitude": COLOMBO[0], "longitude": COLOMBO[1]},
        headers=auth,
    ).json()["alert"]
    assert alert["severity"] == "high"


def test_retracted_alert_disappears(client, auth, admin_headers):
    alert_id = issue_alert(client, admin_headers, *COLOMBO)
    client.post(
        f"/api/v1/admin/alerts/{alert_id}/retract",
        headers=admin_headers,
        json={"operator": "tester"},
    )
    assert (
        client.get(
            "/api/v1/alerts/active",
            params={"latitude": COLOMBO[0], "longitude": COLOMBO[1]},
            headers=auth,
        ).json()["alert"]
        is None
    )


def test_expired_alert_disappears(client, auth, admin_headers, db):
    issue_alert(client, admin_headers, *COLOMBO)
    alert = db.scalars(select(Alert)).first()
    alert.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()

    assert (
        client.get(
            "/api/v1/alerts/active",
            params={"latitude": COLOMBO[0], "longitude": COLOMBO[1]},
            headers=auth,
        ).json()["alert"]
        is None
    )


def test_alert_is_queryable_before_any_push_is_sent(client, admin_headers, db, auth):
    """Brief 8: the push is a trigger, not the source of truth."""
    sent_when_queryable = {}

    import app.push as push_module

    original = push_module.send_alert

    def spy(session, alert):
        from app.routers.alerts import find_active_alert_for

        sent_when_queryable["queryable"] = (
            find_active_alert_for(session, *COLOMBO) is not None
        )
        return original(session, alert)

    push_module.send_alert = spy
    try:
        issue_alert(client, admin_headers, *COLOMBO)
    finally:
        push_module.send_alert = original

    assert sent_when_queryable["queryable"] is True


# --- devices ---------------------------------------------------------------


def test_register_and_delete_fcm_token(client, auth, db):
    assert (
        client.post(
            "/api/v1/devices/fcm-token",
            headers=auth,
            json={"fcmToken": "fZ8abc", "platform": "android"},
        ).json()
        == {"accepted": True}
    )
    assert db.scalar(select(func.count(Device.id))) == 1

    assert (
        client.request(
            "DELETE", "/api/v1/devices/fcm-token", headers=auth, json={"fcmToken": "fZ8abc"}
        ).json()
        == {"accepted": True}
    )
    assert db.scalar(select(func.count(Device.id))) == 0


def test_deleting_an_unknown_token_still_succeeds(client, auth):
    """Logout must never fail because the server tidied up first."""
    response = client.request(
        "DELETE", "/api/v1/devices/fcm-token", headers=auth, json={"fcmToken": "never-seen"}
    )
    assert response.status_code == 200


def test_re_registering_the_same_token_does_not_duplicate(client, auth, db):
    for _ in range(3):
        client.post(
            "/api/v1/devices/fcm-token",
            headers=auth,
            json={"fcmToken": "fZ8abc", "platform": "android"},
        )
    assert db.scalar(select(func.count(Device.id))) == 1


def test_a_token_moving_to_another_user_is_reassigned_not_duplicated(client, db):
    """FCM reissues a token to whichever install holds it -- a shared handset."""
    first = register(client, nic="912345678V").json()["token"]
    second = register(client, nic="199112345678").json()["token"]

    for token in (first, second):
        client.post(
            "/api/v1/devices/fcm-token",
            headers={"Authorization": f"Bearer {token}"},
            json={"fcmToken": "shared-handset", "platform": "android"},
        )

    devices = db.scalars(select(Device)).all()
    assert len(devices) == 1
    import jwt

    from app.config import settings as s

    owner = jwt.decode(second, s.jwt_secret, algorithms=[s.jwt_algorithm])["sub"]
    assert devices[0].user_id == owner


# --- consent ---------------------------------------------------------------


def test_registration_does_not_pretend_consent_was_given(client, auth):
    status = client.get("/api/v1/consent", headers=auth).json()
    assert status["granted"] is False
    assert status["currentVersion"] == settings.consent_notice_version


def test_consent_can_be_granted_and_withdrawn(client, auth, db):
    version = settings.consent_notice_version
    client.post("/api/v1/consent", headers=auth, json={"noticeVersion": version, "granted": True})
    assert client.get("/api/v1/consent", headers=auth).json()["granted"] is True

    client.post("/api/v1/consent", headers=auth, json={"noticeVersion": version, "granted": False})
    assert client.get("/api/v1/consent", headers=auth).json()["granted"] is False


def test_consent_to_an_old_notice_version_is_refused(client, auth):
    response = client.post(
        "/api/v1/consent", headers=auth, json={"noticeVersion": "1999-01-v1", "granted": True}
    )
    assert response.status_code == 400


# --- account deletion ------------------------------------------------------


def test_delete_account_erases_everything_keyed_to_the_user(client, auth, db):
    client.post(
        "/api/v1/locations",
        headers=auth,
        json={
            "latitude": 6.9271,
            "longitude": 79.8612,
            "accuracy": 10,
            "recordedAt": "2026-08-28T09:00:00Z",
            "source": "auto",
        },
    )
    client.post(
        "/api/v1/feedback",
        headers=auth,
        json={
            "floodPresent": True,
            "latitude": 6.9271,
            "longitude": 79.8612,
            "answeredAt": "2026-08-28T09:05:00Z",
        },
    )
    client.post(
        "/api/v1/devices/fcm-token", headers=auth, json={"fcmToken": "abc", "platform": "ios"}
    )

    response = client.request(
        "DELETE", "/api/v1/account", headers=auth, json={"password": "secret123"}
    )
    assert response.status_code == 200

    for model in (User, LocationPing, Device, ConsentRecord):
        assert db.scalar(select(func.count()).select_from(model)) == 0


def test_wrong_password_on_delete_is_not_a_401(client, auth):
    """A 401 would tear down the session and hide the real reason."""
    response = client.request(
        "DELETE", "/api/v1/account", headers=auth, json={"password": "wrongwrong"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_token_stops_working_after_deletion(client, auth):
    client.request("DELETE", "/api/v1/account", headers=auth, json={"password": "secret123"})
    assert client.get("/api/v1/alerts/active", headers=auth).status_code == 401


def test_issued_alerts_survive_a_deletion(client, auth, admin_headers, db):
    """A public safety record holding no personal data."""
    issue_alert(client, admin_headers, *COLOMBO)
    client.request("DELETE", "/api/v1/account", headers=auth, json={"password": "secret123"})
    assert db.scalar(select(func.count(Alert.id))) == 1
    assert db.scalar(select(func.count(AlertRegion.id))) == 1


# --- admin guard -----------------------------------------------------------


def test_admin_requires_the_token(client):
    assert client.get("/api/v1/admin/summary").status_code == 403
    assert client.get("/api/v1/admin/summary", headers={"X-Admin-Token": "wrong"}).status_code == 403


def test_a_user_token_is_not_an_admin_token(client, auth):
    assert client.get("/api/v1/admin/summary", headers=auth).status_code == 403
