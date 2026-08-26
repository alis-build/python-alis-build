"""Carrying an identity on HTTP headers.

These are plain functions over a header mapping, so they work with whatever
request object your framework hands you -- ``flask.request.headers``,
``django.request.headers``, ``httpx.Headers``, or a bare ``dict``. For ASGI
applications, :mod:`alis.build.iam.asgi` wires them up for you.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from ._errors import IdentityNotFoundError
from ._identity import Identity
from ._registry import SystemConfig
from ._wire import dumps, loads, unsigned_jwt

HEADER_NAME: Final = "x-alis-identity"
"""Carries the identity as JSON. Also the gRPC metadata key."""

LEGACY_HEADER_NAME: Final = "x-alis-forwarded-authorization"
"""Carries the same identity as an unsigned token, for older services."""

_FORWARDED_PROTO: Final = "x-forwarded-proto"


def _lookup(headers: Mapping[str, str], name: str) -> str | None:
    """Reads a header case-insensitively.

    Most framework header mappings already fold case, but a plain ``dict`` does
    not, so fall back to a scan rather than quietly missing the header.
    """
    value = headers.get(name)
    if value is not None:
        return value
    for key, candidate in headers.items():
        if key.lower() == name:
            return candidate
    return None


def identity_from_headers(
    headers: Mapping[str, str], /, config: SystemConfig | None = None
) -> Identity:
    """Reads the caller identity from request headers.

    Args:
        headers: The request's headers. Looked up case-insensitively.
        config: System email registry to consult. Defaults to the process-wide one.

    Returns:
        The identity the request carries.

    Raises:
        IdentityNotFoundError: If the request carries no identity header.
        IdentityDecodeError: If the header is present but cannot be decoded.
    """
    value = _lookup(headers, HEADER_NAME)
    if not value:
        raise IdentityNotFoundError(f"no {HEADER_NAME} header on the request")
    return loads(value, config)


def identity_from_headers_or_none(
    headers: Mapping[str, str], /, config: SystemConfig | None = None
) -> Identity | None:
    """Reads the caller identity, returning ``None`` when the header is absent.

    A malformed header still raises: an unreadable identity is a real problem,
    and treating it as an anonymous request would hide it.

    Raises:
        IdentityDecodeError: If the header is present but cannot be decoded.
    """
    value = _lookup(headers, HEADER_NAME)
    if not value:
        return None
    return loads(value, config)


def identity_headers(identity: Identity, /, *, legacy: bool = False) -> dict[str, str]:
    """Builds the headers that carry an identity to another service.

    Args:
        identity: The identity to forward.
        legacy: Also emit the unsigned-token header some older services read.

    Returns:
        A mapping to merge into an outgoing request's headers.
    """
    headers = {HEADER_NAME: dumps(identity).decode()}
    if legacy:
        headers[LEGACY_HEADER_NAME] = unsigned_jwt(identity)
    return headers


def is_secure_request(scheme: str, headers: Mapping[str, str], /) -> bool:
    """Reports whether the request reached the server over TLS.

    Behind a load balancer the connection to the application is plain HTTP, so
    the forwarded-protocol header is the only signal available. That header is
    client-supplied and must not be trusted unless a proxy you control
    overwrites it.
    """
    if scheme in ("https", "wss"):
        return True
    forwarded = _lookup(headers, _FORWARDED_PROTO)
    if not forwarded:
        return False
    # A chain of proxies appends, so the first entry is the original scheme.
    return forwarded.split(",")[0].strip().lower() == "https"
