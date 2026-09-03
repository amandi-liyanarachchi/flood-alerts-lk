"""Test fixtures.

The database is a file-backed SQLite instance per test session, and the
scheduler is off -- no test should ever reach the Irrigation Department's
servers.
"""

from __future__ import annotations

import os
import tempfile

# Must be set before app.config is imported anywhere.
_tmp = tempfile.mkdtemp(prefix="floodwatch-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["INGEST_ENABLED"] = "false"
os.environ["JWT_SECRET"] = "test-secret"
os.environ["ADMIN_TOKEN"] = "test-admin-token"
os.environ["ENVIRONMENT"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import ratelimit  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    ratelimit.reset()
    yield


@pytest.fixture
def client():
    # raise_server_exceptions=False so the 500 handler's response is asserted
    # rather than the exception propagating into the test.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def admin_headers():
    return {"X-Admin-Token": "test-admin-token"}


def register(client, nic="912345678V", password="secret123", **overrides):
    payload = {
        "nic": nic,
        "firstName": "Nimal",
        "lastName": "Perera",
        "phone": "0712345678",
        "password": password,
    }
    payload.update(overrides)
    return client.post("/api/v1/auth/register", json=payload)


@pytest.fixture
def auth(client):
    """A registered user's Authorization header."""
    response = register(client)
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}
