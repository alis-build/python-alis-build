"""The blocking authentication client.

Identical in behaviour to :class:`~alis.build.iam.authn.AuthnClient`; only the
calls block. It never touches asyncio, so calling it from inside a running
event loop is merely a blocking call rather than an error.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from datetime import datetime
from types import TracebackType
from typing import Any, Self

import httpx

from .._errors import AuthenticationError, TokenExpiredError
from .._identity import Identity
from .._registry import SystemConfig
from ._base import BaseAuthnClient
from ._keys import parse_jwks
from ._models import AuthenticationResult, Claims, LoginCookie, LoginResult, Tokens
from ._protocol import TokenRequest, parse_token_response


class SyncAuthnClient(BaseAuthnClient):
    """Talks to an authorization server, blocking.

    Args:
        http_client: An existing client to send through. One is created if you
            do not pass one.
        **options: See :class:`~alis.build.iam.authn.BaseAuthnClient`.
    """

    __slots__ = ("_http", "_lock", "_owns_http")

    def __init__(self, *, http_client: httpx.Client | None = None, **options: Any) -> None:
        super().__init__(**options)
        self._http = http_client or httpx.Client(timeout=self.timeout)
        self._owns_http = http_client is None
        # Guards key fetches so a burst of requests triggers one, not many.
        self._lock = threading.Lock()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Closes the connection pool, if this client created it."""
        if self._owns_http:
            self._http.close()

    # -- token endpoint ----------------------------------------------------

    def _post_token(self, request: TokenRequest) -> Tokens:
        try:
            response = self._http.post(
                request.url, headers=dict(request.headers), content=request.content
            )
        except httpx.HTTPError as exc:
            raise AuthenticationError(f"the token endpoint could not be reached: {exc}") from exc
        return parse_token_response(response.status_code, response.content)

    def exchange_code(self, redirect_uri: str, code: str, /) -> Tokens:
        """Exchanges an authorization code for tokens."""
        return self._post_token(self._code_request(redirect_uri, code))

    def refresh(self, tokens: Tokens, /) -> Tokens:
        """Renews an access token, carrying across an unrotated refresh token."""
        issued = self._post_token(self._refresh_request(tokens.refresh_token))
        return tokens.merged_with(issued)

    # -- verification ------------------------------------------------------

    def sync_keys(self) -> None:
        """Fetches the issuer's published signing keys.

        Raises:
            AuthenticationError: If the key set cannot be fetched or read.
        """
        self._keys.record_attempt()
        try:
            response = self._http.get(self.jwks_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AuthenticationError(f"could not fetch the signing keys: {exc}") from exc
        try:
            self._keys.replace(parse_jwks(response.content))
        except ValueError as exc:
            raise AuthenticationError(f"could not read the signing keys: {exc}") from exc

    def validate_token(
        self,
        token: str,
        /,
        *,
        now: datetime | None = None,
        audience: str | Sequence[str] | None = None,
    ) -> Claims:
        """Checks a token's signature and claims."""
        parts, kid = self._prepare(token)
        key = self._keys.get(kid) if kid else None
        if kid and key is None and self._keys.should_fetch(kid):
            with self._lock:
                # Another thread may have fetched while this one waited.
                key = self._keys.get(kid)
                if key is None and self._keys.should_fetch(kid):
                    self.sync_keys()
                    key = self._keys.get(kid)
        self._verify(parts, key)
        return self._check_claims(parts, now=now, audience=audience)

    def verified_identity(
        self,
        token: str,
        /,
        *,
        now: datetime | None = None,
        audience: str | Sequence[str] | None = None,
        config: SystemConfig | None = None,
    ) -> Identity:
        """Checks a token and returns the identity it describes."""
        self.validate_token(token, now=now, audience=audience)
        return self._identity_from(token, config)

    def authenticate(
        self,
        tokens: Tokens,
        /,
        *,
        now: datetime | None = None,
        audience: str | Sequence[str] | None = None,
    ) -> AuthenticationResult:
        """Checks a token set, renewing it only if the access token expired."""
        try:
            self.validate_token(tokens.access_token, now=now, audience=audience)
        except TokenExpiredError:
            if not tokens.refresh_token:
                raise
            return AuthenticationResult(tokens=self.refresh(tokens), refreshed=True)
        return AuthenticationResult(tokens=tokens, refreshed=False)

    # -- login flow --------------------------------------------------------

    def complete_login(
        self,
        query: Mapping[str, str],
        cookies: Mapping[str, str],
        redirect_uri: str,
        /,
        *,
        secure: bool = True,
    ) -> LoginResult:
        """Finishes a login from its callback."""
        code, state = self._callback_code(query)
        transaction = self._read_transaction(cookies, state)
        tokens = self.exchange_code(redirect_uri, code)
        return LoginResult(
            tokens=tokens,
            return_to=transaction.return_to,
            nonce=transaction.nonce,
            clear_cookie=LoginCookie(value="", max_age=0, secure=secure),
        )
