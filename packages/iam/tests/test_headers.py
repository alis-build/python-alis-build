"""Reading and writing the identity HTTP headers."""

from __future__ import annotations

import pytest

from alis.build.iam import (
    Identity,
    IdentityDecodeError,
    IdentityNotFoundError,
    IdentityType,
    SystemConfig,
    dumps,
    unsigned_jwt,
)
from alis.build.iam.headers import (
    HEADER_NAME,
    LEGACY_HEADER_NAME,
    identity_from_headers,
    identity_from_headers_or_none,
    identity_headers,
    is_secure_request,
)


def test_header_names_are_stable() -> None:
    assert HEADER_NAME == "x-alis-identity"
    assert LEGACY_HEADER_NAME == "x-alis-forwarded-authorization"


def test_round_trip(user: Identity) -> None:
    assert identity_from_headers(identity_headers(user)) == user


def test_value_is_raw_json_not_base64(user: Identity) -> None:
    assert identity_headers(user)[HEADER_NAME] == dumps(user).decode()


def test_legacy_header_is_opt_in(user: Identity) -> None:
    assert LEGACY_HEADER_NAME not in identity_headers(user)
    legacy = identity_headers(user, legacy=True)
    assert legacy[LEGACY_HEADER_NAME] == unsigned_jwt(user)
    assert legacy[HEADER_NAME] == dumps(user).decode()


@pytest.mark.parametrize(
    "name",
    ["x-alis-identity", "X-Alis-Identity", "X-ALIS-IDENTITY", "x-Alis-IDENTITY"],
)
def test_lookup_is_case_insensitive(user: Identity, name: str) -> None:
    assert identity_from_headers({name: dumps(user).decode()}) == user


def test_missing_header_raises() -> None:
    with pytest.raises(IdentityNotFoundError, match="x-alis-identity"):
        identity_from_headers({})
    with pytest.raises(IdentityNotFoundError):
        identity_from_headers({HEADER_NAME: ""})


def test_missing_header_is_none_for_the_optional_accessor() -> None:
    assert identity_from_headers_or_none({}) is None
    assert identity_from_headers_or_none({HEADER_NAME: ""}) is None


def test_malformed_header_always_raises() -> None:
    """An unreadable identity is a problem, not an anonymous request."""
    with pytest.raises(IdentityDecodeError):
        identity_from_headers({HEADER_NAME: "not json"})
    with pytest.raises(IdentityDecodeError):
        identity_from_headers_or_none({HEADER_NAME: "not json"})


def test_system_promotion_applies(system_config: SystemConfig) -> None:
    system_config.add_system_email("svc@example.com")
    built = Identity(type=IdentityType.USER, id="s1", email="svc@example.com")
    assert identity_from_headers(identity_headers(built)).is_system()


@pytest.mark.parametrize(
    ("scheme", "headers", "expected"),
    [
        pytest.param("https", {}, True, id="direct_tls"),
        pytest.param("wss", {}, True, id="secure_websocket"),
        pytest.param("http", {}, False, id="plain"),
        pytest.param("http", {"x-forwarded-proto": "https"}, True, id="proxied_tls"),
        pytest.param("http", {"X-Forwarded-Proto": "HTTPS"}, True, id="case_insensitive"),
        pytest.param("http", {"x-forwarded-proto": "https,http"}, True, id="proxy_chain"),
        pytest.param("http", {"x-forwarded-proto": "http,https"}, False, id="chain_started_plain"),
        pytest.param("http", {"x-forwarded-proto": ""}, False, id="empty_header"),
    ],
)
def test_is_secure_request(scheme: str, headers: dict[str, str], expected: bool) -> None:
    assert is_secure_request(scheme, headers) is expected
