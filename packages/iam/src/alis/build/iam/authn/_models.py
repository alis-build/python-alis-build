"""Values exchanged with the authorization server."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Self

LOGIN_COOKIE_NAME: Final = "alis_authn_login"
"""Cookie holding the in-progress login, until the callback arrives."""

LOGIN_TRANSACTION_TTL: Final = timedelta(minutes=10)
"""How long a login has to complete before its transaction expires."""


@dataclass(frozen=True, slots=True)
class Tokens:
    """The tokens an authorization server issues.

    Immutable: refreshing returns a new value rather than editing this one in
    place, so a token set shared between tasks cannot change underneath them.
    """

    access_token: str = ""
    refresh_token: str = ""
    id_token: str = ""

    def merged_with(self, issued: Self, /) -> Self:
        """Returns these tokens updated with a freshly issued set.

        A server that does not rotate refresh tokens omits the field on a
        refresh, so an empty value means "unchanged" and must not overwrite a
        working refresh token. The identity token is treated the same way.
        """
        return replace(
            self,
            access_token=issued.access_token or self.access_token,
            refresh_token=issued.refresh_token or self.refresh_token,
            id_token=issued.id_token or self.id_token,
        )


@dataclass(frozen=True, slots=True)
class Claims:
    """The claims carried by a verified token.

    Only the claims this package acts on are named; everything the token
    carried is in :attr:`raw`.
    """

    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def expires_at(self) -> datetime | None:
        """When the token expires, if it says."""
        exp = self.raw.get("exp")
        if not isinstance(exp, (int, float)) or isinstance(exp, bool):
            return None
        return datetime.fromtimestamp(exp, tz=UTC)

    @property
    def audience(self) -> tuple[str, ...]:
        """Who the token was issued for. Empty if it does not say."""
        aud = self.raw.get("aud")
        if isinstance(aud, str):
            return (aud,)
        if isinstance(aud, list):
            return tuple(item for item in aud if isinstance(item, str))
        return ()

    @property
    def issuer(self) -> str:
        """Who issued the token, or ``""`` if it does not say."""
        issuer = self.raw.get("iss")
        return issuer if isinstance(issuer, str) else ""

    @property
    def subject(self) -> str:
        """Who the token is about, or ``""`` if it does not say."""
        subject = self.raw.get("sub")
        return subject if isinstance(subject, str) else ""

    @property
    def nonce(self) -> str:
        """The nonce the login sent, or ``""`` if the token carries none."""
        nonce = self.raw.get("nonce")
        return nonce if isinstance(nonce, str) else ""


@dataclass(frozen=True, slots=True)
class AuthenticationResult:
    """The outcome of checking, and possibly renewing, a token set."""

    tokens: Tokens
    refreshed: bool
    """Whether the access token was renewed. Persist the tokens if so."""


@dataclass(frozen=True, slots=True)
class LoginCookie:
    """A cookie the caller should set on its response.

    Rendered rather than set directly so this package stays independent of any
    web framework.
    """

    value: str
    max_age: int
    secure: bool

    def to_set_cookie(self) -> str:
        """Renders the ``Set-Cookie`` header value."""
        parts = [
            f"{LOGIN_COOKIE_NAME}={self.value}",
            "Path=/",
            f"Max-Age={self.max_age}",
            "HttpOnly",
        ]
        if self.secure:
            parts.append("Secure")
        parts.append("SameSite=Lax")
        return "; ".join(parts)


@dataclass(frozen=True, slots=True)
class LoginStart:
    """Everything needed to begin a login.

    Send the user to :attr:`url` and set :attr:`cookie` on that response.
    """

    url: str
    state: str
    nonce: str
    return_to: str
    cookie: LoginCookie


@dataclass(frozen=True, slots=True)
class LoginResult:
    """The outcome of a completed login."""

    tokens: Tokens
    return_to: str
    """Where the user was heading before the login began."""

    nonce: str
    """The nonce this login sent.

    Pass it to ``validate_id_token_nonce`` along with the identity token to
    confirm the token belongs to this login rather than a replayed one.
    """

    clear_cookie: LoginCookie
    """Set this on the response to discard the finished transaction."""
