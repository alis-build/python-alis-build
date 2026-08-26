"""Signing keys: reading a published set, caching it, and verifying against it."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from alis.build.iam.authn import (
    InvalidTokenError,
    KeyStore,
    SignatureVerificationError,
    SigningKey,
    parse_jwks,
)
from alis.build.iam.authn._keys import require_algorithm, require_key_id, verify_signature
from alis.build.iam.authn._protocol import split_token

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


# -- reading a published key set -------------------------------------------


def test_standard_field_names(jwks: Any) -> None:
    keys = parse_jwks(json.dumps(jwks("key-1")).encode())
    assert set(keys) == {"key-1"}


def test_legacy_field_names_still_work(jwks: Any) -> None:
    """Issuers are not all on the same version of the key format."""
    keys = parse_jwks(json.dumps(jwks("key-1", legacy_names=True)).encode())
    assert set(keys) == {"key-1"}


def test_non_rsa_keys_are_skipped(jwks: Any) -> None:
    keys = parse_jwks(json.dumps(jwks("key-1", kty="EC")).encode())
    assert keys == {}


def test_a_key_with_no_type_is_assumed_rsa(jwks: Any) -> None:
    keys = parse_jwks(json.dumps(jwks("key-1", kty=None)).encode())
    assert set(keys) == {"key-1"}


def test_one_bad_key_does_not_lose_the_good_ones(jwks: Any) -> None:
    """A single malformed entry must not lock everyone out."""
    document = jwks("good")
    document["keys"].extend(
        [
            {"kid": "no-material", "kty": "RSA"},
            {"kid": "bad-base64", "kty": "RSA", "n": "!!!", "e": "AQAB"},
            {"no-kid": True},
            "not even an object",
        ]
    )
    keys = parse_jwks(json.dumps(document).encode())
    assert set(keys) == {"good"}


@pytest.mark.parametrize("body", [b"not json", b"{}", b'{"keys":"nope"}', b"[]"])
def test_an_unusable_document_is_an_error(body: bytes) -> None:
    with pytest.raises(ValueError, match="key set"):
        parse_jwks(body)


# -- header checks ---------------------------------------------------------


def test_supported_algorithm_passes() -> None:
    assert require_algorithm({"alg": "RS256"}) == "RS256"


@pytest.mark.parametrize("algorithm", ["none", "HS256", "RS512", "ES256"])
def test_other_algorithms_are_refused(algorithm: str) -> None:
    """An issuer switching algorithm should fail loudly, not go unchecked."""
    with pytest.raises(InvalidTokenError, match="only"):
        require_algorithm({"alg": algorithm})


@pytest.mark.parametrize("header", [{}, {"alg": ""}, {"alg": 5}])
def test_a_missing_algorithm_is_refused(header: dict[str, Any]) -> None:
    with pytest.raises(InvalidTokenError, match="no signing algorithm"):
        require_algorithm(header)


@pytest.mark.parametrize("header", [{}, {"kid": ""}, {"kid": 7}])
def test_a_missing_key_id_is_refused(header: dict[str, Any]) -> None:
    with pytest.raises(InvalidTokenError, match="no key id"):
        require_key_id(header)


# -- verification ----------------------------------------------------------


def test_a_genuine_signature_verifies(jwks: Any, make_token: Any) -> None:
    keys = parse_jwks(json.dumps(jwks("key-1")).encode())
    verify_signature(split_token(make_token(kid="key-1")), keys["key-1"])


def test_a_forged_signature_does_not(jwks: Any, make_token: Any) -> None:
    keys = parse_jwks(json.dumps(jwks("key-1")).encode())
    with pytest.raises(SignatureVerificationError, match="does not verify"):
        verify_signature(split_token(make_token(kid="key-1", sign=False)), keys["key-1"])


def test_a_tampered_payload_does_not(jwks: Any, make_token: Any) -> None:
    keys = parse_jwks(json.dumps(jwks("key-1")).encode())
    head, _, signature = make_token(kid="key-1", sub="alice").split(".")
    tampered = f"{head}.eyJzdWIiOiJyb290In0.{signature}"
    with pytest.raises(SignatureVerificationError):
        verify_signature(split_token(tampered), keys["key-1"])


def test_an_empty_signature_is_refused(jwks: Any, make_token: Any) -> None:
    keys = parse_jwks(json.dumps(jwks("key-1")).encode())
    head, payload, _ = make_token(kid="key-1").split(".")
    with pytest.raises(SignatureVerificationError, match="no signature"):
        verify_signature(split_token(f"{head}.{payload}."), keys["key-1"])


# -- the cache -------------------------------------------------------------


@pytest.fixture
def key(signing_key: Any) -> Any:
    """Builds a signing key for the cache tests, reusing the session key pair."""

    def build(kid: str) -> SigningKey:
        return SigningKey(kid=kid, public_key=signing_key.public_key())

    return build


def test_an_empty_store_wants_a_fetch() -> None:
    store = KeyStore()
    assert store.get("key-1", now=NOW) is None
    assert store.should_fetch("key-1", now=NOW) is True


def test_a_fresh_store_serves_without_fetching(key: Any) -> None:
    store = KeyStore()
    store.replace({"key-1": key("key-1")}, now=NOW)
    assert store.get("key-1", now=NOW) is not None
    assert store.should_fetch("key-1", now=NOW) is False


def test_an_aged_out_store_wants_a_fetch(key: Any) -> None:
    store = KeyStore(ttl=timedelta(hours=1), min_refresh_interval=timedelta(seconds=0))
    store.replace({"key-1": key("key-1")}, now=NOW)
    later = NOW + timedelta(hours=2)
    assert store.get("key-1", now=later) is None
    assert store.should_fetch("key-1", now=later) is True


def test_an_unknown_key_wants_a_fetch(key: Any) -> None:
    store = KeyStore(min_refresh_interval=timedelta(seconds=0))
    store.replace({"key-1": key("key-1")}, now=NOW)
    assert store.should_fetch("key-2", now=NOW) is True


def test_repeated_fetches_for_an_unknown_key_are_rate_limited(key: Any) -> None:
    """Otherwise a token naming a key that was never published turns every
    request into a request to the issuer."""
    store = KeyStore(min_refresh_interval=timedelta(seconds=30))
    store.replace({"key-1": key("key-1")}, now=NOW)

    assert store.should_fetch("missing", now=NOW + timedelta(seconds=1)) is False
    assert store.should_fetch("missing", now=NOW + timedelta(seconds=31)) is True


def test_a_failed_attempt_still_counts_against_the_interval() -> None:
    store = KeyStore(min_refresh_interval=timedelta(seconds=30))
    store.record_attempt(now=NOW)
    assert store.should_fetch("key-1", now=NOW + timedelta(seconds=5)) is False


def test_the_cache_cannot_wedge_permanently(key: Any) -> None:
    """A key id the store has never seen must eventually be fetched for,
    however the issuer happens to name its keys."""
    store = KeyStore(min_refresh_interval=timedelta(seconds=30))
    store.replace({"2026-01-01": key("2026-01-01")}, now=NOW)
    assert store.should_fetch("an-unrelated-kid", now=NOW + timedelta(minutes=5)) is True


def test_clearing_forces_a_fetch(key: Any) -> None:
    store = KeyStore()
    store.replace({"key-1": key("key-1")}, now=NOW)
    store.clear()
    assert store.get("key-1", now=NOW) is None
    assert store.key_ids == frozenset()
