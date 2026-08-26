"""Carrying an identity on gRPC metadata."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Final

from .._errors import IdentityNotFoundError
from .._identity import Identity
from .._registry import SystemConfig
from .._wire import dumps, loads, unsigned_jwt
from ..headers import HEADER_NAME, LEGACY_HEADER_NAME

METADATA_KEY: Final = HEADER_NAME
"""Carries the identity as JSON. Same key as the HTTP header."""

LEGACY_METADATA_KEY: Final = LEGACY_HEADER_NAME
"""Carries the same identity as an unsigned token, for older services."""

Metadata = Iterable[tuple[str, str | bytes]]


def identity_metadata(identity: Identity, /, *, legacy: bool = False) -> list[tuple[str, str]]:
    """Builds the metadata that carries an identity to another service.

    Note that gRPC requires a non-``-bin`` metadata value to be ASCII, so an
    identity carrying a non-ASCII email cannot be forwarded this way. That is a
    constraint of the transport, not of the identity itself.

    Args:
        identity: The identity to forward.
        legacy: Also emit the unsigned-token entry some older services read.

    Returns:
        Key/value pairs to append to an outgoing call's metadata.
    """
    pairs = [(METADATA_KEY, dumps(identity).decode())]
    if legacy:
        pairs.append((LEGACY_METADATA_KEY, unsigned_jwt(identity)))
    return pairs


def _last_value(metadata: Metadata, key: str) -> str | None:
    """Returns the last value for key, which is the one a caller appended most recently."""
    found: str | None = None
    for name, value in metadata:
        if name.lower() != key:
            continue
        found = value.decode() if isinstance(value, bytes) else value
    return found


def identity_from_metadata(metadata: Metadata, /, config: SystemConfig | None = None) -> Identity:
    """Reads the caller identity from incoming call metadata.

    Args:
        metadata: The call's invocation metadata as key/value pairs.
        config: System email registry to consult. Defaults to the process-wide one.

    Returns:
        The identity the call carries.

    Raises:
        IdentityNotFoundError: If the call carries no identity entry.
        IdentityDecodeError: If the entry is present but cannot be decoded.
    """
    value = _last_value(metadata, METADATA_KEY)
    if not value:
        raise IdentityNotFoundError(f"no {METADATA_KEY} entry in the call metadata")
    return loads(value, config)


def identity_from_metadata_or_none(
    metadata: Metadata, /, config: SystemConfig | None = None
) -> Identity | None:
    """Reads the caller identity, returning ``None`` when no entry is present.

    A malformed entry still raises.

    Raises:
        IdentityDecodeError: If the entry is present but cannot be decoded.
    """
    value = _last_value(metadata, METADATA_KEY)
    if not value:
        return None
    return loads(value, config)


def merge_metadata(
    metadata: Sequence[tuple[str, str | bytes]] | None, identity: Identity, *, legacy: bool = False
) -> list[tuple[str, str | bytes]]:
    """Appends the identity entries to a call's existing metadata."""
    merged: list[tuple[str, str | bytes]] = list(metadata or ())
    merged.extend(identity_metadata(identity, legacy=legacy))
    return merged
