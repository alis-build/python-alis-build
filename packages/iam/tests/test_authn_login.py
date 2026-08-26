"""The login flow: starting one, and finishing it from the callback."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from alis.build.iam.authn import (
    LOGIN_COOKIE_NAME,
    LOGIN_TRANSACTION_TTL,
    LoginError,
    LoginExpiredError,
    LoginStateMismatchError,
    LoginTransaction,
    NonceMismatchError,
    SyncAuthnClient,
    generate_nonce,
    generate_state,
)
from alis.build.iam.authn._login import (
    check_transaction,
    decode_transaction,
    encode_transaction,
)

BASE = "https://auth.example.com"
REDIRECT = "https://app.example.com/callback"
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def token_server(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"access_token": "at", "refresh_token": "rt", "id_token": "it"})


@pytest.fixture
def client() -> Any:
    value = SyncAuthnClient.from_base_url(
        BASE, client_id="cid", http_client=httpx.Client(transport=httpx.MockTransport(token_server))
    )
    yield value
    value.close()


# -- unguessable values ----------------------------------------------------


def test_state_and_nonce_are_unguessable() -> None:
    values = {generate_state() for _ in range(100)} | {generate_nonce() for _ in range(100)}
    assert len(values) == 200
    assert all(len(value) >= 40 for value in values)


# -- starting --------------------------------------------------------------


def test_start_login_builds_the_url_and_the_cookie(client: Any) -> None:
    start = client.start_login(REDIRECT, return_to="/dashboard")

    query = parse_qs(urlparse(start.url).query)
    assert query["redirect_uri"] == [REDIRECT]
    assert query["state"] == [start.state]
    assert query["nonce"] == [start.nonce]
    assert query["client_id"] == ["cid"]
    assert start.return_to == "/dashboard"


def test_the_cookie_carries_the_transaction(client: Any) -> None:
    start = client.start_login(REDIRECT, return_to="/dashboard")
    transaction = decode_transaction(start.cookie.value)

    assert transaction.state == start.state
    assert transaction.nonce == start.nonce
    assert transaction.return_to == "/dashboard"
    assert transaction.expires_at > datetime.now(tz=UTC)


def test_the_set_cookie_header(client: Any) -> None:
    header = client.start_login(REDIRECT).cookie.to_set_cookie()

    assert header.startswith(f"{LOGIN_COOKIE_NAME}=")
    assert "; Path=/" in header
    assert f"; Max-Age={int(LOGIN_TRANSACTION_TTL.total_seconds())}" in header
    assert "; HttpOnly" in header
    assert "; Secure" in header
    assert header.endswith("; SameSite=Lax")
    assert "Domain" not in header


def test_an_insecure_cookie_for_local_http(client: Any) -> None:
    header = client.start_login(REDIRECT, secure=False).cookie.to_set_cookie()
    assert "Secure" not in header
    assert "; HttpOnly; SameSite=Lax" in header


def test_the_clearing_cookie_expires_immediately(client: Any) -> None:
    header = client.clear_login_cookie().to_set_cookie()
    assert header.startswith(f"{LOGIN_COOKIE_NAME}=;")
    assert "; Max-Age=0" in header


# -- finishing -------------------------------------------------------------


def complete(client: Any, start: Any, **query: str) -> Any:
    params = {"code": "the-code", "state": start.state}
    params.update(query)
    return client.complete_login(params, {LOGIN_COOKIE_NAME: start.cookie.value}, REDIRECT)


def test_a_good_callback_completes(client: Any) -> None:
    start = client.start_login(REDIRECT, return_to="/dashboard")
    result = complete(client, start)

    assert result.tokens.access_token == "at"
    assert result.return_to == "/dashboard"
    assert result.nonce == start.nonce
    assert result.clear_cookie.max_age == 0


def test_a_mismatched_state_is_refused(client: Any) -> None:
    """This is the cross-site request forgery check."""
    start = client.start_login(REDIRECT)
    with pytest.raises(LoginStateMismatchError):
        complete(client, start, state="not-the-state")


@pytest.mark.parametrize(
    ("query", "match"),
    [
        pytest.param({"state": "s"}, "no authorization code", id="no_code"),
        pytest.param({"code": "c"}, "no state", id="no_state"),
        pytest.param(
            {"error": "access_denied", "error_description": "user said no"},
            "user said no",
            id="server_refused",
        ),
        pytest.param({"error": "server_error"}, "server_error", id="server_refused_tersely"),
    ],
)
def test_a_bad_callback_is_refused(client: Any, query: dict[str, str], match: str) -> None:
    with pytest.raises(LoginError, match=match):
        client.complete_login(query, {}, REDIRECT)


def test_a_missing_cookie_is_refused(client: Any) -> None:
    start = client.start_login(REDIRECT)
    with pytest.raises(LoginError, match="missing"):
        client.complete_login({"code": "c", "state": start.state}, {}, REDIRECT)


@pytest.mark.parametrize(
    "value",
    [
        "not-base64!!",
        "",
        base64.urlsafe_b64encode(b"[]").decode().rstrip("="),
        base64.urlsafe_b64encode(b'{"nonce":"n"}').decode().rstrip("="),
    ],
)
def test_an_unusable_cookie_is_refused(client: Any, value: str) -> None:
    with pytest.raises(LoginError):
        client.complete_login({"code": "c", "state": "s"}, {LOGIN_COOKIE_NAME: value}, REDIRECT)


# -- the transaction itself ------------------------------------------------


def test_transaction_round_trip() -> None:
    transaction = LoginTransaction(
        state="st", nonce="no", return_to="/here", expires_at=NOW + timedelta(minutes=10)
    )
    assert decode_transaction(encode_transaction(transaction)) == transaction


def test_the_cookie_payload_shape() -> None:
    transaction = LoginTransaction(state="st", nonce="no", return_to="/here", expires_at=NOW)
    encoded = encode_transaction(transaction)
    padded = encoded + "=" * (-len(encoded) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))

    assert payload == {
        "state": "st",
        "nonce": "no",
        "return_to": "/here",
        "expires": int(NOW.timestamp()),
    }


def test_an_expired_transaction_is_refused() -> None:
    transaction = LoginTransaction(state="st", nonce="", return_to="", expires_at=NOW)
    with pytest.raises(LoginExpiredError):
        check_transaction(transaction, "st", now=NOW + timedelta(seconds=1))


def test_a_live_transaction_passes() -> None:
    transaction = LoginTransaction(
        state="st", nonce="", return_to="", expires_at=NOW + timedelta(minutes=5)
    )
    check_transaction(transaction, "st", now=NOW)


# -- the identity token's nonce --------------------------------------------


def id_token_with(**claims: Any) -> str:
    def segment(payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{segment({'alg': 'none'})}.{segment(dict(claims))}.###"


def test_a_matching_nonce_passes(client: Any) -> None:
    client.validate_id_token_nonce(id_token_with(nonce="n1"), "n1")


@pytest.mark.parametrize(
    ("token", "nonce", "match"),
    [
        pytest.param("", "n1", "no identity token", id="no_token"),
        pytest.param(id_token_with(nonce="n1"), "", "no nonce was recorded", id="no_nonce"),
        pytest.param(id_token_with(sub="x"), "n1", "carries no nonce", id="no_claim"),
        pytest.param(id_token_with(nonce="other"), "n1", "does not match", id="mismatch"),
    ],
)
def test_a_bad_nonce_is_refused(client: Any, token: str, nonce: str, match: str) -> None:
    with pytest.raises(NonceMismatchError, match=match):
        client.validate_id_token_nonce(token, nonce)


def test_the_full_round_trip(client: Any) -> None:
    """Start a login, come back on the callback, then check the nonce."""
    start = client.start_login(REDIRECT, return_to="/dashboard")
    result = complete(client, start)
    client.validate_id_token_nonce(id_token_with(nonce=result.nonce), result.nonce)
