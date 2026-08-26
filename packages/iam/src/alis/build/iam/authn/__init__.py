"""Signing users in, and checking the tokens they arrive with.

Requires the ``authn`` extra::

    pip install 'alis-build-iam[authn]'

Starting and finishing a login::

    client = AuthnClient.from_base_url("https://auth.example.com",
                                       client_id=..., client_secret=...)

    # On the sign-in route: send the user to start.url and set start.cookie.
    start = client.start_login("https://app.example.com/callback", return_to="/dashboard")

    # On the callback route:
    result = await client.complete_login(request.query, request.cookies,
                                         "https://app.example.com/callback")
    client.validate_id_token_nonce(result.tokens.id_token, result.nonce)

Checking a token on each request::

    identity = await client.verified_identity(bearer_token)

:class:`SyncAuthnClient` offers the same surface for a blocking server.
"""

from __future__ import annotations

from .._optional import missing_dependency

try:
    import cryptography as _cryptography
except ImportError as exc:  # pragma: no cover - exercised by the bare-core CI job
    raise missing_dependency(__name__, "authn", "cryptography") from exc

try:
    import httpx as _httpx
except ImportError as exc:  # pragma: no cover - exercised by the bare-core CI job
    raise missing_dependency(__name__, "authn", "httpx") from exc

del _cryptography, _httpx

from .._errors import (  # noqa: E402 - must follow the dependency checks above
    AuthenticationError,
    InvalidAudienceError,
    InvalidTokenError,
    LoginError,
    LoginExpiredError,
    LoginStateMismatchError,
    NonceMismatchError,
    SignatureVerificationError,
    TokenEndpointError,
    TokenExpiredError,
    UnknownSigningKeyError,
)
from ._base import DEFAULT_TIMEOUT, BaseAuthnClient  # noqa: E402
from ._client import AuthnClient  # noqa: E402
from ._keys import (  # noqa: E402
    DEFAULT_KEY_TTL,
    DEFAULT_MIN_REFRESH_INTERVAL,
    SUPPORTED_ALGORITHMS,
    KeyStore,
    SigningKey,
    parse_jwks,
)
from ._login import (  # noqa: E402
    LoginTransaction,
    generate_nonce,
    generate_state,
)
from ._models import (  # noqa: E402
    LOGIN_COOKIE_NAME,
    LOGIN_TRANSACTION_TTL,
    AuthenticationResult,
    Claims,
    LoginCookie,
    LoginResult,
    LoginStart,
    Tokens,
)
from ._protocol import DEFAULT_LEEWAY, GrantType  # noqa: E402
from ._sync_client import SyncAuthnClient  # noqa: E402

__all__ = [
    "DEFAULT_KEY_TTL",
    "DEFAULT_LEEWAY",
    "DEFAULT_MIN_REFRESH_INTERVAL",
    "DEFAULT_TIMEOUT",
    "LOGIN_COOKIE_NAME",
    "LOGIN_TRANSACTION_TTL",
    "SUPPORTED_ALGORITHMS",
    "AuthenticationError",
    "AuthenticationResult",
    "AuthnClient",
    "BaseAuthnClient",
    "Claims",
    "GrantType",
    "InvalidAudienceError",
    "InvalidTokenError",
    "KeyStore",
    "LoginCookie",
    "LoginError",
    "LoginExpiredError",
    "LoginResult",
    "LoginStart",
    "LoginStateMismatchError",
    "LoginTransaction",
    "NonceMismatchError",
    "SignatureVerificationError",
    "SigningKey",
    "SyncAuthnClient",
    "TokenEndpointError",
    "TokenExpiredError",
    "Tokens",
    "UnknownSigningKeyError",
    "generate_nonce",
    "generate_state",
    "parse_jwks",
]
