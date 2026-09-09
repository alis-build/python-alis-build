"""The asyncio authentication client."""

from __future__ import annotations

import asyncio
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


class AuthnClient(BaseAuthnClient):
    """Talks to an authorization server.

    Every decision this client makes lives in the surrounding module and is
    tested without a network; the client itself only performs the calls.

    Use it as a context manager so the underlying connection pool is closed::

        async with AuthnClient.from_base_url("https://auth.example.com") as client:
            tokens = await client.exchange_code(redirect_uri, code)

    Args:
        http_client: An existing client to send through, so a service can share
            one connection pool. One is created if you do not pass one.
        **options: See :class:`~alis.build.iam.authn.BaseAuthnClient`.
    """

    __slots__ = ("_http", "_lock", "_owns_http")

    def __init__(self, *, http_client: httpx.AsyncClient | None = None, **options: Any) -> None:
        super().__init__(**options)
        self._http = http_client or httpx.AsyncClient(timeout=self.timeout)
        self._owns_http = http_client is None
        # Guards key fetches so a burst of requests triggers one, not many.
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Closes the connection pool, if this client created it."""
        if self._owns_http:
            await self._http.aclose()

    # -- token endpoint ----------------------------------------------------

    async def _post_token(self, request: TokenRequest) -> Tokens:
        try:
            response = await self._http.post(
                request.url, headers=dict(request.headers), content=request.content
            )
        except httpx.HTTPError as exc:
            raise AuthenticationError(f"the token endpoint could not be reached: {exc}") from exc
        return parse_token_response(response.status_code, response.content)

    async def exchange_code(self, redirect_uri: str, code: str, /) -> Tokens:
        """Exchanges an authorization code for tokens.

        Raises:
            TokenEndpointError: If the server refuses the exchange.
            AuthenticationError: If the server cannot be reached.
        """
        return await self._post_token(self._code_request(redirect_uri, code))

    async def refresh(self, tokens: Tokens, /) -> Tokens:
        """Renews an access token.

        Returns:
            A new token set. A server that does not rotate refresh tokens omits
            the field, so the existing refresh token is carried across rather
            than being replaced with an empty one.

        Raises:
            InvalidTokenError: If there is no refresh token to renew with.
            TokenEndpointError: If the server refuses the renewal.
        """
        issued = await self._post_token(self._refresh_request(tokens.refresh_token))
        return tokens.merged_with(issued)

    # -- verification ------------------------------------------------------

    async def sync_keys(self) -> None:
        """Fetches the issuer's published signing keys.

        Failures are raised rather than logged and swallowed: with no keys,
        every token is refused, and that should not be silent.

        Raises:
            AuthenticationError: If the key set cannot be fetched or read.
        """
        self._keys.record_attempt()
        try:
            response = await self._http.get(self.jwks_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AuthenticationError(f"could not fetch the signing keys: {exc}") from exc
        try:
            self._keys.replace(parse_jwks(response.content))
        except ValueError as exc:
            raise AuthenticationError(f"could not read the signing keys: {exc}") from exc

    async def validate_token(
        self,
        token: str,
        /,
        *,
        now: datetime | None = None,
        audience: str | Sequence[str] | None = None,
    ) -> Claims:
        """Checks a token's signature and claims.

        Args:
            token: The compact token to check.
            now: The moment to check the expiry against.
            audience: Who the token must be for, overriding the client's own.

        Returns:
            The token's claims.

        Raises:
            InvalidTokenError: If the token is malformed or its claims do not
                hold up. Expiry raises the more specific
                :class:`~alis.build.iam.TokenExpiredError`.
            SignatureVerificationError: If the signature does not verify.
            AuthenticationError: If the signing keys cannot be fetched.
        """
        parts, kid = self._prepare(token)
        key = self._keys.get(kid) if kid else None
        if kid and key is None and self._keys.should_fetch(kid):
            async with self._lock:
                # Another task may have fetched while this one waited.
                key = self._keys.get(kid)
                if key is None and self._keys.should_fetch(kid):
                    await self.sync_keys()
                    key = self._keys.get(kid)
        self._verify(parts, key)
        return self._check_claims(parts, now=now, audience=audience)

    async def verified_identity(
        self,
        token: str,
        /,
        *,
        now: datetime | None = None,
        audience: str | Sequence[str] | None = None,
        config: SystemConfig | None = None,
    ) -> Identity:
        """Checks a token and returns the identity it describes.

        This is the one place an identity is derived from a token that has
        actually been verified.
        """
        await self.validate_token(token, now=now, audience=audience)
        return self._identity_from(token, config)

    async def authenticate(
        self,
        tokens: Tokens,
        /,
        *,
        now: datetime | None = None,
        audience: str | Sequence[str] | None = None,
    ) -> AuthenticationResult:
        """Checks a token set, renewing it if the access token has expired.

        Only an expired access token triggers a renewal. A malformed token, a
        bad signature, or an unreachable key endpoint all raise instead, so a
        transient outage does not spend refresh tokens.

        The renewed token is validated too, against the same audience. A refresh
        is not a way around a check the original token failed.

        Returns:
            The tokens to use, and whether they were renewed. Persist them if
            they were.
        """
        try:
            await self.validate_token(tokens.access_token, now=now, audience=audience)
        except TokenExpiredError:
            if not tokens.refresh_token:
                raise
            renewed = await self.refresh(tokens)
            # The token the issuer just minted goes through the same door as
            # any other. Returning it unchecked makes the audience check
            # bypassable by anyone holding a refresh token, which for a browser
            # session is always.
            await self.validate_token(renewed.access_token, now=now, audience=audience)
            return AuthenticationResult(tokens=renewed, refreshed=True)
        return AuthenticationResult(tokens=tokens, refreshed=False)

    # -- login flow --------------------------------------------------------

    async def complete_login(
        self,
        query: Mapping[str, str],
        cookies: Mapping[str, str],
        redirect_uri: str,
        /,
        *,
        secure: bool = True,
    ) -> LoginResult:
        """Finishes a login from its callback.

        The identity token's nonce is deliberately not checked here, because
        that token is not verified and its claims are not evidence on their
        own. Call :meth:`validate_id_token_nonce` with the returned nonce if
        you need it.

        Args:
            query: The callback's query parameters.
            cookies: The request's cookies, carrying the login transaction.
            redirect_uri: The same value the login was started with.
            secure: Whether the cookie that clears the transaction is
                HTTPS-only.

        Raises:
            LoginError: If the callback is missing what it needs, the server
                reported a failure, or the transaction does not match.
        """
        code, state = self._callback_code(query)
        transaction = self._read_transaction(cookies, state)
        tokens = await self.exchange_code(redirect_uri, code)
        return LoginResult(
            tokens=tokens,
            return_to=transaction.return_to,
            nonce=transaction.nonce,
            clear_cookie=LoginCookie(value="", max_age=0, secure=secure),
        )
