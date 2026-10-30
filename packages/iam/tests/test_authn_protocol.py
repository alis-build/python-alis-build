"""The authentication protocol, tested without a network."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from alis.build.iam import IdentityDecodeError
from alis.build.iam.authn import (
    Claims,
    GrantType,
    InvalidAudienceError,
    InvalidTokenError,
    TokenEndpointError,
    TokenExpiredError,
    Tokens,
)
from alis.build.iam.authn._protocol import (
    build_authorize_url,
    build_token_request,
    decode_claims,
    decode_header,
    parse_token_response,
    split_token,
    validate_claims,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def claims_at(offset_seconds: int, **extra: object) -> Claims:
    raw: dict[str, object] = {"exp": int((NOW + timedelta(seconds=offset_seconds)).timestamp())}
    raw.update(extra)
    return Claims(raw=raw)


# -- authorize URL ---------------------------------------------------------


def test_authorize_url_is_stable() -> None:
    """Parameters come out in a fixed order, so the same inputs give the same URL."""
    url = build_authorize_url(
        "https://auth.example.com/authorize",
        "https://app/cb",
        "st8",
        client_id="cid",
        nonce="n1",
    )
    assert url == (
        "https://auth.example.com/authorize"
        "?client_id=cid&nonce=n1&redirect_uri=https%3A%2F%2Fapp%2Fcb&state=st8"
    )


def test_authorize_url_omits_empty_optionals() -> None:
    url = build_authorize_url("https://a/authorize", "https://app/cb", "st8")
    assert "client_id" not in url
    assert "nonce" not in url


def test_authorize_url_accepts_extra_params() -> None:
    url = build_authorize_url(
        "https://a/authorize", "https://app/cb", "st8", extra_params={"scope": "openid email"}
    )
    assert "scope=openid+email" in url


def test_authorize_url_sign_in_options() -> None:
    url = build_authorize_url(
        "https://a/authorize",
        "https://app/cb",
        "st8",
        prompt=["login", "consent"],
        max_age=timedelta(minutes=5),
        login_hint="jan@example.com",
    )
    assert url == (
        "https://a/authorize?login_hint=jan%40example.com&max_age=300"
        "&prompt=login+consent&redirect_uri=https%3A%2F%2Fapp%2Fcb&state=st8"
    )


def test_prompt_string_is_one_value() -> None:
    """A plain string must not be split into one prompt value per character."""
    url = build_authorize_url("https://a/authorize", "r", "s", prompt="select_account")
    assert url == "https://a/authorize?prompt=select_account&redirect_uri=r&state=s"


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"prompt": []}, id="empty_prompt_list"),
        pytest.param({"prompt": ""}, id="empty_prompt_string"),
        pytest.param({"login_hint": ""}, id="empty_login_hint"),
        pytest.param({"max_age": None}, id="no_max_age"),
    ],
)
def test_empty_options_are_omitted(kwargs: dict[str, Any]) -> None:
    url = build_authorize_url("https://a/authorize", "r", "s", **kwargs)
    assert url == "https://a/authorize?redirect_uri=r&state=s"


def test_max_age_truncates_to_whole_seconds() -> None:
    url = build_authorize_url("https://a/authorize", "r", "s", max_age=timedelta(seconds=1.9))
    assert url == "https://a/authorize?max_age=1&redirect_uri=r&state=s"


def test_max_age_zero_is_sent() -> None:
    """Zero asks for a fresh sign-in, so it is a value, not an absence."""
    url = build_authorize_url("https://a/authorize", "r", "s", max_age=timedelta(0))
    assert url == "https://a/authorize?max_age=0&redirect_uri=r&state=s"


def test_negative_max_age_rejected() -> None:
    with pytest.raises(ValueError, match="max_age"):
        build_authorize_url("https://a/authorize", "r", "s", max_age=timedelta(seconds=-1))


def test_option_clashing_with_extra_params_rejected() -> None:
    with pytest.raises(ValueError, match="prompt"):
        build_authorize_url(
            "https://a/authorize", "r", "s", prompt="login", extra_params={"prompt": "none"}
        )


# -- token requests --------------------------------------------------------


def test_code_request_body() -> None:
    request = build_token_request(
        "https://a/token",
        GrantType.AUTHORIZATION_CODE,
        client_id="cid",
        client_secret="sec",
        code="abc",
        redirect_uri="https://app/cb",
    )
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "grant_type": "authorization_code",
        "client_id": "cid",
        "client_secret": "sec",
        "redirect_uri": "https://app/cb",
        "code": "abc",
    }


def test_refresh_request_body() -> None:
    request = build_token_request(
        "https://a/token",
        GrantType.REFRESH_TOKEN,
        client_id="cid",
        client_secret="sec",
        refresh_token="rt",
    )
    body = json.loads(request.content)
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "rt"
    assert "code" not in body


def test_credentials_travel_in_the_body_not_a_header() -> None:
    request = build_token_request(
        "https://a/token", GrantType.REFRESH_TOKEN, client_id="cid", client_secret="sec"
    )
    assert "authorization" not in {key.lower() for key in request.headers}
    assert json.loads(request.content)["client_secret"] == "sec"


# -- token responses -------------------------------------------------------


def test_parse_successful_response() -> None:
    body = b'{"access_token":"at","refresh_token":"rt","id_token":"it","extra":1}'
    assert parse_token_response(200, body) == Tokens("at", "rt", "it")


def test_missing_fields_become_empty() -> None:
    assert parse_token_response(200, b"{}") == Tokens()


@pytest.mark.parametrize("status", [201, 204, 302, 400, 401, 500])
def test_only_200_is_success(status: int) -> None:
    with pytest.raises(TokenEndpointError) as excinfo:
        parse_token_response(status, b'{"error":"invalid_grant"}')
    assert excinfo.value.status == status
    assert "invalid_grant" in excinfo.value.body


@pytest.mark.parametrize("body", [b"not json", b"[]", b'"a string"'])
def test_unusable_success_body_is_an_error(body: bytes) -> None:
    with pytest.raises(TokenEndpointError):
        parse_token_response(200, body)


# -- token splitting -------------------------------------------------------


@pytest.mark.parametrize("token", ["", "a", "a.b", "a.b.c.d"])
def test_a_token_must_have_three_segments(token: str) -> None:
    with pytest.raises(InvalidTokenError, match=r"header\.payload\.signature"):
        split_token(token)


def test_signing_input_is_the_segments_as_sent() -> None:
    parts = split_token("aGVhZGVy.cGF5bG9hZA.c2ln")
    assert parts.signing_input == b"aGVhZGVy.cGF5bG9hZA"


def test_decoding_a_non_object_payload_fails() -> None:
    parts = split_token("e30.W10.sig")  # payload is []
    with pytest.raises(InvalidTokenError, match="not a JSON object"):
        decode_claims(parts)


def test_decoding_bad_base64_fails() -> None:
    parts = split_token("e30.!!!!.sig")
    with pytest.raises((InvalidTokenError, IdentityDecodeError)):
        decode_claims(parts)


def test_header_decoding(make_token) -> None:  # type: ignore[no-untyped-def]
    header = decode_header(split_token(make_token(kid="key-9")))
    assert header["alg"] == "RS256"
    assert header["kid"] == "key-9"


# -- claims ----------------------------------------------------------------


def test_a_live_token_passes() -> None:
    validate_claims(claims_at(3600), now=NOW)


def test_an_expired_token_is_refused() -> None:
    with pytest.raises(TokenExpiredError):
        validate_claims(claims_at(-3600), now=NOW)


def test_expiry_tolerates_clock_skew() -> None:
    """A token that lapsed a second ago must not fail on drift alone."""
    validate_claims(claims_at(-5), now=NOW, leeway=timedelta(seconds=30))
    with pytest.raises(TokenExpiredError):
        validate_claims(claims_at(-5), now=NOW, leeway=timedelta(seconds=0))


def test_a_token_with_no_expiry_is_refused_by_default() -> None:
    with pytest.raises(InvalidTokenError, match="no expiry"):
        validate_claims(Claims(raw={}), now=NOW)


def test_a_token_with_no_expiry_can_be_allowed() -> None:
    validate_claims(Claims(raw={}), now=NOW, require_expiry=False)


@pytest.mark.parametrize(
    ("carried", "wanted", "ok"),
    [
        pytest.param("api", "api", True, id="string_match"),
        pytest.param("other", "api", False, id="string_mismatch"),
        pytest.param(["api", "web"], "api", True, id="list_contains"),
        pytest.param(["web"], "api", False, id="list_missing"),
        pytest.param(["api"], ["api", "web"], True, id="either_wanted"),
        pytest.param(None, "api", True, id="no_audience_claim_is_accepted"),
    ],
)
def test_audience(carried: object, wanted: object, ok: bool) -> None:
    extra: dict[str, object] = {} if carried is None else {"aud": carried}
    claims = claims_at(3600, **extra)
    if ok:
        validate_claims(claims, now=NOW, audience=wanted)  # type: ignore[arg-type]
    else:
        with pytest.raises(InvalidAudienceError):
            validate_claims(claims, now=NOW, audience=wanted)  # type: ignore[arg-type]


def test_an_array_audience_does_not_crash_the_parser() -> None:
    """A list audience is legal and must not be read as a malformed token."""
    assert claims_at(3600, aud=["a", "b"]).audience == ("a", "b")


def test_issuer_is_checked_when_set() -> None:
    validate_claims(claims_at(3600, iss="https://auth"), now=NOW, issuer="https://auth")
    with pytest.raises(InvalidTokenError, match="not"):
        validate_claims(claims_at(3600, iss="https://evil"), now=NOW, issuer="https://auth")


def test_claim_accessors_tolerate_junk() -> None:
    claims = Claims(raw={"exp": "soon", "aud": 5, "iss": None, "sub": [], "nonce": 1})
    assert claims.expires_at is None
    assert claims.audience == ()
    assert claims.issuer == ""
    assert claims.subject == ""
    assert claims.nonce == ""


def test_tokens_merge_preserves_an_unrotated_refresh_token() -> None:
    """A server that does not rotate omits the field; that must not wipe it."""
    existing = Tokens(access_token="old", refresh_token="rt", id_token="it")
    assert existing.merged_with(Tokens(access_token="new")) == Tokens("new", "rt", "it")
    assert (
        existing.merged_with(Tokens(access_token="new", refresh_token="rt2")).refresh_token == "rt2"
    )


def test_max_age_truncation_is_exact_for_large_values() -> None:
    url = build_authorize_url("https://a/authorize", "r", "s", max_age=timedelta.max)
    assert "max_age=86399999999999&" in url
