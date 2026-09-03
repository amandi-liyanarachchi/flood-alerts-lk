"""Auth, NIC normalisation, and the validation rules from brief section 4."""

from __future__ import annotations

import pytest

from tests.conftest import register


def test_register_returns_contract_shape(client):
    response = register(client)
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"token", "user"}
    assert set(body["user"]) == {"id", "nic", "firstName", "lastName", "phone"}
    assert body["user"]["id"].startswith("u_")
    assert body["token"]


def test_login_returns_same_shape(client):
    register(client)
    response = client.post("/api/v1/auth/login", json={"nic": "912345678V", "password": "secret123"})
    assert response.status_code == 200
    assert set(response.json()) == {"token", "user"}


def test_bad_login_is_401_invalid_credentials(client):
    """Brief 5.2: the client shows this rather than tearing down the session,
    but only because the code is INVALID_CREDENTIALS on an /auth/* path."""
    register(client)
    response = client.post("/api/v1/auth/login", json={"nic": "912345678V", "password": "wrongwrong"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_unknown_nic_is_indistinguishable_from_wrong_password(client):
    """No NIC enumeration -- the identifier is a national ID number."""
    register(client)
    unknown = client.post("/api/v1/auth/login", json={"nic": "999999999V", "password": "secret123"})
    wrong = client.post("/api/v1/auth/login", json={"nic": "912345678V", "password": "nope12345"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


@pytest.mark.parametrize("variant", ["912345678v", " 912345678V ", "91 234 5678 V", "912345678V"])
def test_nic_normalisation_never_forks_an_account(client, variant):
    """Brief 5.1. Every spelling must reach the same account."""
    register(client)
    duplicate = register(client, nic=variant)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "NIC_ALREADY_REGISTERED"

    login = client.post("/api/v1/auth/login", json={"nic": variant, "password": "secret123"})
    assert login.status_code == 200


def test_new_format_nic_accepted(client):
    assert register(client, nic="199112345678").status_code == 201


@pytest.mark.parametrize(
    "nic",
    ["91234567V", "9123456789V", "912345678X", "912345678", "19911234567", "1991123456789", "abcdefghiV"],
)
def test_invalid_nic_rejected(client, nic):
    response = register(client, nic=nic)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


@pytest.mark.parametrize("phone", ["0812345678", "071234567", "07123456789", "+94712345678", "712345678"])
def test_invalid_phone_rejected(client, phone):
    assert register(client, phone=phone).status_code == 400


def test_password_minimum_is_eight_and_that_is_the_only_rule(client):
    assert register(client, password="1234567").status_code == 400
    assert register(client, nic="199112345678", password="12345678").status_code == 201


@pytest.mark.parametrize("name", ["", "N1mal", "Nimal!", "N" * 51])
def test_invalid_names_rejected(client, name):
    assert register(client, firstName=name).status_code == 400


@pytest.mark.parametrize("name", ["Nimal", "Mary-Anne", "O'Brien", "Anne Marie"])
def test_valid_names_accepted(client, name):
    assert register(client, nic="199112345678", firstName=name).status_code == 201


def test_error_message_is_safe_to_show_a_member_of_the_public(client):
    message = register(client, nic="nonsense").json()["error"]["message"]
    for leak in ("Traceback", "sqlalchemy", "SELECT", "app/", "pydantic", "u_"):
        assert leak not in message
    assert message.endswith(".")
