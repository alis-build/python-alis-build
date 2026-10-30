"""Configuration and pure logic shared by the two client flavours."""

from __future__ import annotations

import hmac
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Self

from .._errors import (
    InvalidTokenError,
    LoginError,
    NonceMismatchError,
    UnknownSigningKeyError,
)
from .._identity import Identity
from .._registry import SystemConfig
from .._wire import from_unverified_jwt
from ._keys import (
    DEFAULT_KEY_TTL,
    DEFAULT_MIN_REFRESH_INTERVAL,
    KeyStore,
    SigningKey,
    require_algorithm,
    require_key_id,
    verify_signature,
)
from ._login import (
    LoginTransaction,
    check_transaction,
    decode_transaction,
    encode_transaction,
    generate_nonce,
    generate_state,
)
from ._models import (
    LOGIN_TRANSACTION_TTL,
    Claims,
    LoginCookie,
    LoginStart,
    Tokens,
)
from ._protocol import (
    DEFAULT_LEEWAY,
    GrantType,
    JwtParts,
    TokenRequest,
    build_authorize_url,
    build_token_request,
    decode_claims,
    decode_header,
    split_token,
    validate_claims,
)

DEFAULT_TIMEOUT: Final = 10.0
"""Seconds allowed for a call to the authorization server.

Set explicitly, because a request to an unresponsive identity provider that
never times out holds a request handler open indefinitely.
"""


class BaseAuthnClient:
    """Everything a client does that is not a network call.

    Args:
        authorize_url: Where to send a user to sign in.
        token_url: Where to exchange and refresh tokens.
        jwks_url: Where the issuer publishes its signing keys.
        client_id: The registered application's id.
        client_secret: The registered application's secret.
        verify_signatures: Whether to check token signatures. Turning this off
            leaves the expiry as the only check, so a forged token with a
            future expiry would pass. Only do it for tokens your own service
            issued and stored, never for tokens a caller supplies.
        issuer: If set, tokens must say they came from here.
        audience: If set, tokens must be for this audience.
        leeway: Clock skew tolerated when checking expiry.
        timeout: Seconds allowed for a call to the authorization server.
        key_ttl: How long a fetched key set is trusted.
        min_key_refresh_interval: The shortest gap between two key fetches.
    """

    __slots__ = (
        "_audience",
        "_authorize_url",
        "_client_id",
        "_client_secret",
        "_issuer",
        "_jwks_url",
        "_keys",
        "_leeway",
        "_timeout",
        "_token_url",
        "_verify_signatures",
    )

    # Keyword-only configuration, nine of twelve defaulted. Nothing is
    # positional, so every call site names what it passes; the count measures
    # how much an OIDC provider has to be told, not the width of the interface.
    def __init__(  # noqa: PLR0913
        self,
        *,
        authorize_url: str,
        token_url: str,
        jwks_url: str,
        client_id: str = "",
        client_secret: str = "",
        verify_signatures: bool = True,
        issuer: str = "",
        audience: str | Sequence[str] | None = None,
        leeway: timedelta = DEFAULT_LEEWAY,
        timeout: float = DEFAULT_TIMEOUT,
        key_ttl: timedelta = DEFAULT_KEY_TTL,
        min_key_refresh_interval: timedelta = DEFAULT_MIN_REFRESH_INTERVAL,
    ) -> None:
        self._authorize_url = authorize_url
        self._token_url = token_url
        self._jwks_url = jwks_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._verify_signatures = verify_signatures
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway
        self._timeout = timeout
        self._keys = KeyStore(ttl=key_ttl, min_refresh_interval=min_key_refresh_interval)

    @classmethod
    def from_base_url(cls, base_url: str, /, **options: Any) -> Self:
        """Builds a client from an authorization server's base URL.

        The three endpoints follow the conventional layout: ``/authorize``,
        ``/token`` and ``/.well-known/jwks.json``. Pass them individually if
        your server puts them elsewhere.
        """
        root = base_url.rstrip("/")
        return cls(
            authorize_url=f"{root}/authorize",
            token_url=f"{root}/token",
            jwks_url=f"{root}/.well-known/jwks.json",
            **options,
        )

    @property
    def keys(self) -> KeyStore:
        """The cache of the issuer's published signing keys."""
        return self._keys

    @property
    def timeout(self) -> float:
        """Seconds allowed for a call to the authorization server."""
        return self._timeout

    @property
    def jwks_url(self) -> str:
        """Where the issuer publishes its signing keys."""
        return self._jwks_url

    # -- no I/O ------------------------------------------------------------

    def authorize_url(  # noqa: PLR0913 - keyword-only, one per query parameter
        self,
        redirect_uri: str,
        state: str,
        /,
        *,
        nonce: str = "",
        extra_params: Mapping[str, str] | None = None,
        prompt: str | Sequence[str] | None = None,
        max_age: timedelta | None = None,
        login_hint: str = "",
    ) -> str:
        """Builds the URL to send a user to in order to sign in.

        The sign-in options are described on :func:`build_authorize_url`.
        """
        return build_authorize_url(
            self._authorize_url,
            redirect_uri,
            state,
            client_id=self._client_id,
            nonce=nonce,
            extra_params=extra_params,
            prompt=prompt,
            max_age=max_age,
            login_hint=login_hint,
        )

    def start_login(  # noqa: PLR0913 - keyword-only, one per query parameter
        self,
        redirect_uri: str,
        /,
        *,
        return_to: str = "",
        secure: bool = True,
        prompt: str | Sequence[str] | None = None,
        max_age: timedelta | None = None,
        login_hint: str = "",
        extra_params: Mapping[str, str] | None = None,
    ) -> LoginStart:
        """Begins a login.

        Send the user to the returned URL and set the returned cookie on that
        same response; the callback needs it to prove the login is the one this
        service started.

        Args:
            redirect_uri: Where the authorization server should send the user back.
            return_to: Where to send the user once the login finishes.
            secure: Whether the cookie should be restricted to HTTPS. Leave it
                on unless you are serving plain HTTP locally.
            prompt: ``"login"``, ``"select_account"`` or ``"consent"``, or
                several of them. See :func:`build_authorize_url`.
            max_age: Re-authenticate the user if they last signed in longer
                ago than this.
            login_hint: The email the identity provider should pre-fill.
            extra_params: Any further query parameters for the authorize URL.

        Raises:
            ValueError: If ``max_age`` is negative, or a sign-in option is also
                given in ``extra_params``.
        """
        state = generate_state()
        nonce = generate_nonce()
        transaction = LoginTransaction(
            state=state,
            nonce=nonce,
            return_to=return_to,
            expires_at=datetime.now(tz=UTC) + LOGIN_TRANSACTION_TTL,
        )
        return LoginStart(
            url=self.authorize_url(
                redirect_uri,
                state,
                nonce=nonce,
                extra_params=extra_params,
                prompt=prompt,
                max_age=max_age,
                login_hint=login_hint,
            ),
            state=state,
            nonce=nonce,
            return_to=return_to,
            cookie=LoginCookie(
                value=encode_transaction(transaction),
                max_age=int(LOGIN_TRANSACTION_TTL.total_seconds()),
                secure=secure,
            ),
        )

    def clear_login_cookie(self, *, secure: bool = True) -> LoginCookie:
        """Returns a cookie that discards a finished login transaction."""
        return LoginCookie(value="", max_age=0, secure=secure)

    def validate_id_token_nonce(self, id_token: str, nonce: str, /) -> None:
        """Checks that an identity token echoes the nonce the login sent.

        This checks the nonce and nothing else: the identity token's signature
        and expiry are not verified here, so its claims are not evidence on
        their own.

        Raises:
            NonceMismatchError: If the nonce is missing or does not match.
            InvalidTokenError: If the token cannot be decoded.
        """
        if not id_token:
            raise NonceMismatchError("no identity token was returned")
        if not nonce:
            raise NonceMismatchError("no nonce was recorded for this login")

        claims = decode_claims(split_token(id_token))
        if not claims.nonce:
            raise NonceMismatchError("the identity token carries no nonce")
        if not hmac.compare_digest(claims.nonce, nonce):
            raise NonceMismatchError("the identity token's nonce does not match this login")

    # -- request building and response handling ----------------------------

    def _code_request(self, redirect_uri: str, code: str) -> TokenRequest:
        return build_token_request(
            self._token_url,
            GrantType.AUTHORIZATION_CODE,
            client_id=self._client_id,
            client_secret=self._client_secret,
            code=code,
            redirect_uri=redirect_uri,
        )

    def _refresh_request(self, refresh_token: str) -> TokenRequest:
        if not refresh_token:
            raise InvalidTokenError("there is no refresh token to renew with")
        return build_token_request(
            self._token_url,
            GrantType.REFRESH_TOKEN,
            client_id=self._client_id,
            client_secret=self._client_secret,
            refresh_token=refresh_token,
        )

    def _read_transaction(self, cookies: Mapping[str, str], state: str) -> LoginTransaction:
        from ._models import LOGIN_COOKIE_NAME

        transaction = decode_transaction(cookies.get(LOGIN_COOKIE_NAME, ""))
        check_transaction(transaction, state)
        return transaction

    @staticmethod
    def _callback_code(query: Mapping[str, str]) -> tuple[str, str]:
        """Pulls the code and state out of a callback's query parameters.

        Raises:
            LoginError: If the server reported an error, or the callback is
                missing what it needs.
        """
        failure = query.get("error")
        if failure:
            description = query.get("error_description", "")
            detail = f"{failure}: {description}" if description else failure
            raise LoginError(f"the authorization server refused the login: {detail}")
        code = query.get("code", "")
        state = query.get("state", "")
        if not code:
            raise LoginError("the callback carries no authorization code")
        if not state:
            raise LoginError("the callback carries no state")
        return code, state

    # -- verification ------------------------------------------------------

    def _prepare(self, token: str) -> tuple[JwtParts, str]:
        """Splits a token and returns it with the key id it needs.

        Returns an empty key id when signature checking is switched off.
        """
        parts = split_token(token)
        if not self._verify_signatures:
            return parts, ""
        header = decode_header(parts)
        require_algorithm(header)
        return parts, require_key_id(header)

    def _verify(self, parts: JwtParts, key: SigningKey | None) -> None:
        if not self._verify_signatures:
            return
        if key is None:
            raise UnknownSigningKeyError("the issuer publishes no key matching this token")
        verify_signature(parts, key)

    def _check_claims(
        self,
        parts: JwtParts,
        /,
        *,
        now: datetime | None,
        audience: str | Sequence[str] | None,
    ) -> Claims:
        claims = decode_claims(parts)
        validate_claims(
            claims,
            now=now,
            audience=self._audience if audience is None else audience,
            issuer=self._issuer,
            leeway=self._leeway,
        )
        return claims

    def _identity_from(self, token: str, config: SystemConfig | None = None, /) -> Identity:
        """Builds an identity from a token whose claims have already been checked."""
        return from_unverified_jwt(token, config)


__all__ = ["DEFAULT_TIMEOUT", "BaseAuthnClient", "Tokens"]
