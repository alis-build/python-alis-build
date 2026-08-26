"""The authentication protocol, with no I/O in it.

Every decision about what to send, what a response means, and whether a token
holds up lives here as a plain function. The clients are thin shells that do
one network call each and hand the result back to this module, so the async and
blocking flavours cannot drift apart and every edge case is testable without a
server.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final
from urllib.parse import urlencode

from .._errors import (
    InvalidAudienceError,
    InvalidTokenError,
    TokenEndpointError,
    TokenExpiredError,
)
from .._wire import b64url_decode
from ._models import Claims, Tokens

DEFAULT_LEEWAY: Final = timedelta(seconds=30)
"""Clock skew tolerated when checking a token's expiry.

Servers do not agree on the time to the second, and a token that expires
during a request should not fail for a fraction of a second's drift.
"""

_JSON_HEADERS: Final = {"content-type": "application/json"}


class GrantType(StrEnum):
    """How a token is being requested."""

    AUTHORIZATION_CODE = "authorization_code"
    REFRESH_TOKEN = "refresh_token"


@dataclass(frozen=True, slots=True)
class TokenRequest:
    """A request to the token endpoint, ready to send."""

    url: str
    headers: Mapping[str, str]
    content: bytes


@dataclass(frozen=True, slots=True)
class JwtParts:
    """A compact token split into its segments."""

    header: str
    payload: str
    signature: str

    @property
    def signing_input(self) -> bytes:
        """The bytes a signature covers: the header and payload as sent."""
        return f"{self.header}.{self.payload}".encode("ascii")


def build_authorize_url(
    authorize_url: str,
    redirect_uri: str,
    state: str,
    /,
    *,
    client_id: str = "",
    nonce: str = "",
    extra_params: Mapping[str, str] | None = None,
) -> str:
    """Builds the URL to send a user to in order to sign in.

    Args:
        authorize_url: The authorization server's authorize endpoint.
        redirect_uri: Where the server should send the user back to.
        state: An unguessable value echoed back on the callback, used to tie
            the callback to the request that started it.
        client_id: The registered application's id, if it has one.
        nonce: An unguessable value the identity token should echo back.
        extra_params: Any further query parameters to include.

    Returns:
        The URL, with parameters in a stable order.
    """
    params: dict[str, str] = {"redirect_uri": redirect_uri, "state": state}
    if client_id:
        params["client_id"] = client_id
    if nonce:
        params["nonce"] = nonce
    if extra_params:
        params.update(extra_params)
    # Sorted so the same inputs always produce the same URL.
    return f"{authorize_url}?{urlencode(sorted(params.items()))}"


def build_token_request(
    token_url: str,
    grant_type: GrantType,
    /,
    *,
    client_id: str = "",
    client_secret: str = "",
    code: str = "",
    redirect_uri: str = "",
    refresh_token: str = "",
) -> TokenRequest:
    """Builds a request to the token endpoint.

    The body is JSON rather than form-encoded, which is what the platform's
    authorization server expects, and the client credentials travel in the body
    rather than in an authorization header.
    """
    body: dict[str, str] = {
        "grant_type": str(grant_type),
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    }
    if grant_type is GrantType.AUTHORIZATION_CODE:
        body["code"] = code
    else:
        body["refresh_token"] = refresh_token

    return TokenRequest(
        url=token_url,
        headers=_JSON_HEADERS,
        content=json.dumps(body, separators=(",", ":")).encode(),
    )


def parse_token_response(status: int, body: bytes, /) -> Tokens:
    """Reads the tokens out of a token endpoint response.

    Raises:
        TokenEndpointError: If the server did not return ``200``, or returned
            something that is not a JSON object.
    """
    if status != 200:
        raise TokenEndpointError(status, body.decode(errors="replace")[:2048])
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TokenEndpointError(status, f"response was not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise TokenEndpointError(status, "response was not a JSON object")

    return Tokens(
        access_token=_optional_str(payload, "access_token"),
        refresh_token=_optional_str(payload, "refresh_token"),
        id_token=_optional_str(payload, "id_token"),
    )


def _optional_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def split_token(token: str, /) -> JwtParts:
    """Splits a compact token into its three segments.

    Raises:
        InvalidTokenError: If the token is not three dot-separated segments.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidTokenError("invalid token format, expected header.payload.signature")
    return JwtParts(header=parts[0], payload=parts[1], signature=parts[2])


def _decode_segment(segment: str, what: str) -> dict[str, Any]:
    try:
        decoded = json.loads(b64url_decode(segment))
    except Exception as exc:
        raise InvalidTokenError(f"could not decode the token {what}: {exc}") from exc
    if not isinstance(decoded, dict):
        raise InvalidTokenError(f"the token {what} is not a JSON object")
    return decoded


def decode_header(parts: JwtParts, /) -> dict[str, Any]:
    """Decodes a token's header.

    Raises:
        InvalidTokenError: If the header is not a JSON object.
    """
    return _decode_segment(parts.header, "header")


def decode_claims(parts: JwtParts, /) -> Claims:
    """Decodes a token's claims **without checking anything about them**.

    Raises:
        InvalidTokenError: If the payload is not a JSON object.
    """
    return Claims(raw=_decode_segment(parts.payload, "payload"))


def validate_claims(
    claims: Claims,
    /,
    *,
    now: datetime | None = None,
    audience: str | Sequence[str] | None = None,
    issuer: str = "",
    leeway: timedelta = DEFAULT_LEEWAY,
    require_expiry: bool = True,
) -> None:
    """Checks that a token's claims hold up.

    Args:
        claims: The claims to check.
        now: The moment to check against. Defaults to the current time.
        audience: Who the token must be for. A token that names no audience is
            accepted, since not every issuer sets one; a token that names a
            different audience is refused.
        issuer: Who the token must be from. Unchecked when empty.
        leeway: Clock skew to tolerate on the expiry.
        require_expiry: Refuse a token that carries no expiry at all.

    Raises:
        TokenExpiredError: If the token has expired.
        InvalidAudienceError: If the token names a different audience.
        InvalidTokenError: If a required claim is missing or malformed.
    """
    moment = now or datetime.now(tz=UTC)

    expires_at = claims.expires_at
    if expires_at is None:
        if require_expiry:
            raise InvalidTokenError("the token carries no expiry")
    elif expires_at + leeway < moment:
        raise TokenExpiredError(f"the token expired at {expires_at.isoformat()}")

    if audience is not None:
        wanted = (audience,) if isinstance(audience, str) else tuple(audience)
        carried = claims.audience
        if carried and not set(carried) & set(wanted):
            raise InvalidAudienceError(f"the token is for {list(carried)}, not {list(wanted)}")

    if issuer and claims.issuer != issuer:
        raise InvalidTokenError(f"the token is from {claims.issuer!r}, not {issuer!r}")
