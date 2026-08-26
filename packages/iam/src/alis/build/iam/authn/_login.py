"""The short-lived transaction that ties a login callback to its request."""

from __future__ import annotations

import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from .._errors import LoginError, LoginExpiredError, LoginStateMismatchError
from .._wire import b64url_decode, b64url_encode

_RANDOM_BYTES: Final = 32


@dataclass(frozen=True, slots=True)
class LoginTransaction:
    """What a login remembered about itself while the user was away."""

    state: str
    nonce: str
    return_to: str
    expires_at: datetime


def generate_state() -> str:
    """Returns an unguessable value to tie a callback to its request."""
    return secrets.token_urlsafe(_RANDOM_BYTES)


def generate_nonce() -> str:
    """Returns an unguessable value for the identity token to echo back."""
    return secrets.token_urlsafe(_RANDOM_BYTES)


def encode_transaction(transaction: LoginTransaction, /) -> str:
    """Encodes a transaction for storage in a cookie."""
    payload = {
        "state": transaction.state,
        "nonce": transaction.nonce,
        "return_to": transaction.return_to,
        "expires": int(transaction.expires_at.timestamp()),
    }
    return b64url_encode(json.dumps(payload, separators=(",", ":")).encode())


def decode_transaction(value: str, /) -> LoginTransaction:
    """Reads a transaction back out of a cookie.

    Raises:
        LoginError: If the cookie is missing, unreadable, or incomplete.
    """
    if not value:
        raise LoginError("the login transaction cookie is missing")
    try:
        payload: Any = json.loads(b64url_decode(value))
    except Exception as exc:
        raise LoginError(f"the login transaction cookie is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise LoginError("the login transaction cookie is not an object")

    state = payload.get("state")
    expires = payload.get("expires")
    if not isinstance(state, str) or not state:
        raise LoginError("the login transaction carries no state")
    if not isinstance(expires, (int, float)) or isinstance(expires, bool):
        raise LoginError("the login transaction carries no expiry")

    return LoginTransaction(
        state=state,
        nonce=payload.get("nonce", "") if isinstance(payload.get("nonce"), str) else "",
        return_to=payload.get("return_to", "") if isinstance(payload.get("return_to"), str) else "",
        expires_at=datetime.fromtimestamp(expires, tz=UTC),
    )


def check_transaction(
    transaction: LoginTransaction, returned_state: str, /, *, now: datetime | None = None
) -> None:
    """Checks a callback against the transaction that started it.

    Raises:
        LoginExpiredError: If the login took too long.
        LoginStateMismatchError: If the state does not match. This is the
            cross-site request forgery check, so a mismatch means the callback
            should be refused rather than retried.
    """
    moment = now or datetime.now(tz=UTC)
    if transaction.expires_at < moment:
        raise LoginExpiredError("the login took too long; start again")
    # Constant-time, so a mismatch reveals nothing about the expected value.
    if not hmac.compare_digest(returned_state, transaction.state):
        raise LoginStateMismatchError("the callback state does not match the login request")
