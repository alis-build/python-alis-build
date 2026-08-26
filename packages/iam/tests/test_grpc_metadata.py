"""Carrying an identity on gRPC metadata."""

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
from alis.build.iam.grpc import (
    LEGACY_METADATA_KEY,
    METADATA_KEY,
    identity_from_metadata,
    identity_from_metadata_or_none,
    identity_metadata,
    merge_metadata,
)


def test_metadata_key_matches_the_http_header() -> None:
    assert METADATA_KEY == "x-alis-identity"
    assert LEGACY_METADATA_KEY == "x-alis-forwarded-authorization"


def test_round_trip(user: Identity) -> None:
    assert identity_from_metadata(identity_metadata(user)) == user


def test_value_is_raw_json(user: Identity) -> None:
    assert identity_metadata(user) == [(METADATA_KEY, dumps(user).decode())]


def test_legacy_entry_is_opt_in(user: Identity) -> None:
    pairs = identity_metadata(user, legacy=True)
    assert pairs[1] == (LEGACY_METADATA_KEY, unsigned_jwt(user))


def test_last_value_wins(user: Identity) -> None:
    """A call that passes through several hops accumulates entries."""
    stale = Identity(id="stale")
    metadata = [
        (METADATA_KEY, dumps(stale).decode()),
        (METADATA_KEY, dumps(user).decode()),
    ]
    assert identity_from_metadata(metadata) == user


def test_bytes_values_are_decoded(user: Identity) -> None:
    assert identity_from_metadata([(METADATA_KEY, dumps(user))]) == user


def test_keys_are_matched_case_insensitively(user: Identity) -> None:
    assert identity_from_metadata([("X-Alis-Identity", dumps(user).decode())]) == user


def test_missing_entry() -> None:
    with pytest.raises(IdentityNotFoundError, match="x-alis-identity"):
        identity_from_metadata([])
    assert identity_from_metadata_or_none([]) is None


def test_malformed_entry_always_raises() -> None:
    with pytest.raises(IdentityDecodeError):
        identity_from_metadata([(METADATA_KEY, "not json")])
    with pytest.raises(IdentityDecodeError):
        identity_from_metadata_or_none([(METADATA_KEY, "not json")])


def test_merge_preserves_existing_metadata(user: Identity) -> None:
    merged = merge_metadata([("trace-id", "abc")], user)
    assert merged[0] == ("trace-id", "abc")
    assert merged[1] == (METADATA_KEY, dumps(user).decode())
    assert merge_metadata(None, user) == identity_metadata(user)


def test_system_promotion_applies(system_config: SystemConfig) -> None:
    system_config.add_system_email("svc@example.com")
    built = Identity(type=IdentityType.USER, id="s1", email="svc@example.com")
    assert identity_from_metadata(identity_metadata(built)).is_system()
