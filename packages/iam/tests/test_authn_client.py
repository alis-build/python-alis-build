"""The authentication clients, driven against a mock transport."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from alis.build.iam import IdentityType
from alis.build.iam.authn import (
    AuthenticationError,
    AuthnClient,
    InvalidAudienceError,
    InvalidTokenError,
    SignatureVerificationError,
    SyncAuthnClient,
    TokenEndpointError,
    TokenExpiredError,
    Tokens,
    UnknownSigningKeyError,
)

BASE = "https://auth.example.com"
TOKEN_URL = f"{BASE}/token"
JWKS_URL = f"{BASE}/.well-known/jwks.json"


class Server:
    """A stand-in authorization server that records what it was asked."""

    def __init__(self, jwks_document: dict[str, Any]) -> None:
        self.jwks_document = jwks_document
        self.requests: list[httpx.Request] = []
        self.token_response: tuple[int, dict[str, Any]] = (
            200,
            {"access_token": "new-at", "id_token": "new-it"},
        )
        self.jwks_status = 200
        self.fail_with: Exception | None = None

    @property
    def jwks_fetches(self) -> int:
        return sum(1 for request in self.requests if request.url.path.endswith("jwks.json"))

    @property
    def token_posts(self) -> list[dict[str, Any]]:
        return [
            json.loads(request.content)
            for request in self.requests
            if request.url.path.endswith("/token")
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_with is not None:
            raise self.fail_with
        if request.url.path.endswith("jwks.json"):
            if self.jwks_status != 200:
                return httpx.Response(self.jwks_status, text="unavailable")
            return httpx.Response(200, json=self.jwks_document)
        status, body = self.token_response
        return httpx.Response(status, json=body)


@pytest.fixture
def server(jwks: Any) -> Server:
    return Server(jwks("key-1"))


def build_async(server: Server, **options: Any) -> AuthnClient:
    transport = httpx.MockTransport(server.handle)
    return AuthnClient(
        authorize_url=f"{BASE}/authorize",
        token_url=TOKEN_URL,
        jwks_url=JWKS_URL,
        client_id="cid",
        client_secret="sec",
        http_client=httpx.AsyncClient(transport=transport),
        **options,
    )


def build_sync(server: Server, **options: Any) -> SyncAuthnClient:
    transport = httpx.MockTransport(server.handle)
    return SyncAuthnClient(
        authorize_url=f"{BASE}/authorize",
        token_url=TOKEN_URL,
        jwks_url=JWKS_URL,
        client_id="cid",
        client_secret="sec",
        http_client=httpx.Client(transport=transport),
        **options,
    )


# -- the token endpoint ----------------------------------------------------


async def test_exchange_code(server: Server) -> None:
    tokens = await build_async(server).exchange_code("https://app/cb", "the-code")

    assert tokens.access_token == "new-at"
    posted = server.token_posts[0]
    assert posted["grant_type"] == "authorization_code"
    assert posted["code"] == "the-code"
    assert posted["redirect_uri"] == "https://app/cb"
    assert posted["client_secret"] == "sec"


async def test_refresh_keeps_an_unrotated_refresh_token(server: Server) -> None:
    """The server omits the field when it does not rotate; that must not wipe it."""
    server.token_response = (200, {"access_token": "fresh-at"})
    existing = Tokens(access_token="old", refresh_token="keep-me", id_token="old-it")

    renewed = await build_async(server).refresh(existing)

    assert renewed.access_token == "fresh-at"
    assert renewed.refresh_token == "keep-me"
    assert renewed.id_token == "old-it"


async def test_refresh_accepts_a_rotated_token(server: Server) -> None:
    server.token_response = (200, {"access_token": "a", "refresh_token": "rotated"})
    renewed = await build_async(server).refresh(Tokens(refresh_token="old"))
    assert renewed.refresh_token == "rotated"


async def test_refresh_without_a_refresh_token(server: Server) -> None:
    with pytest.raises(InvalidTokenError, match="no refresh token"):
        await build_async(server).refresh(Tokens())


async def test_a_refused_exchange_reports_the_server_response(server: Server) -> None:
    server.token_response = (400, {"error": "invalid_grant"})
    with pytest.raises(TokenEndpointError) as excinfo:
        await build_async(server).exchange_code("https://app/cb", "bad")
    assert excinfo.value.status == 400
    assert "invalid_grant" in excinfo.value.body


async def test_an_unreachable_server_is_an_authentication_error(server: Server) -> None:
    server.fail_with = httpx.ConnectError("no route to host")
    with pytest.raises(AuthenticationError, match="could not be reached"):
        await build_async(server).exchange_code("https://app/cb", "code")


# -- verification ----------------------------------------------------------


async def test_a_valid_token_passes_and_fetches_keys_once(server: Server, make_token: Any) -> None:
    client = build_async(server)
    token = make_token(kid="key-1", sub="alice")

    claims = await client.validate_token(token)
    await client.validate_token(token)

    assert claims.subject == "alice"
    assert server.jwks_fetches == 1, "the second check must use the cached keys"


async def test_a_burst_of_first_requests_triggers_one_fetch(
    server: Server, make_token: Any
) -> None:
    """Otherwise a cold start sends one key request per in-flight call."""
    client = build_async(server)
    token = make_token(kid="key-1")

    await asyncio.gather(*(client.validate_token(token) for _ in range(20)))

    assert server.jwks_fetches == 1


async def test_an_unknown_key_id_is_refused(server: Server, make_token: Any) -> None:
    with pytest.raises(UnknownSigningKeyError):
        await build_async(server).validate_token(make_token(kid="never-published"))


async def test_an_unknown_key_id_does_not_refetch_forever(server: Server, make_token: Any) -> None:
    """A token naming a key that was never published must not turn every
    request into a request to the issuer."""
    client = build_async(server)
    token = make_token(kid="never-published")

    for _ in range(5):
        with pytest.raises(UnknownSigningKeyError):
            await client.validate_token(token)

    assert server.jwks_fetches == 1


async def test_a_forged_signature_is_refused(server: Server, make_token: Any) -> None:
    with pytest.raises(SignatureVerificationError):
        await build_async(server).validate_token(make_token(kid="key-1", sign=False))


@pytest.mark.parametrize("algorithm", ["none", "HS256"])
async def test_another_algorithm_is_refused(
    server: Server, make_token: Any, algorithm: str
) -> None:
    with pytest.raises(InvalidTokenError, match="only"):
        await build_async(server).validate_token(make_token(kid="key-1", algorithm=algorithm))


async def test_an_expired_token_is_refused(server: Server, make_token: Any) -> None:
    with pytest.raises(TokenExpiredError):
        await build_async(server).validate_token(make_token(kid="key-1", expires_in=-3600))


async def test_signature_checking_can_be_switched_off(server: Server, make_token: Any) -> None:
    """Only for tokens this service issued and stored itself."""
    client = build_async(server, verify_signatures=False)
    await client.validate_token(make_token(kid="whatever", sign=False))
    assert server.jwks_fetches == 0


async def test_an_unfetchable_key_set_is_raised_not_swallowed(
    server: Server, make_token: Any
) -> None:
    """With no keys every token is refused, which must not happen silently."""
    server.jwks_status = 503
    with pytest.raises(AuthenticationError, match="could not fetch"):
        await build_async(server).validate_token(make_token(kid="key-1"))


async def test_an_unreadable_key_set_is_raised(server: Server, make_token: Any) -> None:
    server.jwks_document = {"not": "a key set"}
    with pytest.raises(AuthenticationError, match="could not read"):
        await build_async(server).validate_token(make_token(kid="key-1"))


async def test_verified_identity(server: Server, make_token: Any) -> None:
    token = make_token(kid="key-1", sub="1934872948", email="john@example.com")
    identity = await build_async(server).verified_identity(token)

    assert identity.id == "1934872948"
    assert identity.type == IdentityType.USER
    assert identity.policy_member() == "user:1934872948"


# -- authenticate ----------------------------------------------------------


async def test_a_token_just_inside_the_leeway_is_not_renewed(
    server: Server, make_token: Any
) -> None:
    """Clock drift of a few seconds must not cost a refresh round trip."""
    tokens = Tokens(access_token=make_token(kid="key-1", expires_in=-10), refresh_token="rt")
    result = await build_async(server).authenticate(tokens)
    assert result.refreshed is False
    assert server.token_posts == []


async def test_a_live_token_is_left_alone(server: Server, make_token: Any) -> None:
    tokens = Tokens(access_token=make_token(kid="key-1"), refresh_token="rt")
    result = await build_async(server).authenticate(tokens)

    assert result.refreshed is False
    assert result.tokens == tokens
    assert server.token_posts == []


async def test_an_expired_token_is_renewed(server: Server, make_token: Any) -> None:
    tokens = Tokens(access_token=make_token(kid="key-1", expires_in=-3600), refresh_token="rt")
    result = await build_async(server).authenticate(tokens)

    assert result.refreshed is True
    assert result.tokens.access_token == "new-at"
    assert result.tokens.refresh_token == "rt"
    assert server.token_posts[0]["grant_type"] == "refresh_token"


async def test_an_expired_token_with_nothing_to_renew_with(server: Server, make_token: Any) -> None:
    tokens = Tokens(access_token=make_token(kid="key-1", expires_in=-3600))
    with pytest.raises(TokenExpiredError):
        await build_async(server).authenticate(tokens)


async def test_a_forged_token_is_not_renewed(server: Server, make_token: Any) -> None:
    """Renewing on any failure spends refresh tokens during a transient outage
    and papers over a genuinely bad token."""
    tokens = Tokens(access_token=make_token(kid="key-1", sign=False), refresh_token="rt")
    with pytest.raises(SignatureVerificationError):
        await build_async(server).authenticate(tokens)
    assert server.token_posts == []


async def test_a_key_outage_is_not_treated_as_expiry(server: Server, make_token: Any) -> None:
    server.jwks_status = 503
    tokens = Tokens(access_token=make_token(kid="key-1"), refresh_token="rt")
    with pytest.raises(AuthenticationError):
        await build_async(server).authenticate(tokens)
    assert server.token_posts == []


# -- the blocking client ---------------------------------------------------


def test_sync_exchange_code(server: Server) -> None:
    tokens = build_sync(server).exchange_code("https://app/cb", "the-code")
    assert tokens.access_token == "new-at"


def test_sync_validate_and_cache(server: Server, make_token: Any) -> None:
    client = build_sync(server)
    token = make_token(kid="key-1", sub="alice")

    assert client.validate_token(token).subject == "alice"
    client.validate_token(token)
    assert server.jwks_fetches == 1


def test_sync_expired_token_is_renewed(server: Server, make_token: Any) -> None:
    tokens = Tokens(access_token=make_token(kid="key-1", expires_in=-3600), refresh_token="rt")
    result = build_sync(server).authenticate(tokens)
    assert result.refreshed is True
    assert result.tokens.refresh_token == "rt"


def test_sync_client_never_touches_asyncio(server: Server, make_token: Any) -> None:
    """So calling it from inside a running loop is merely blocking, not an error."""

    async def inside_a_loop() -> str:
        return build_sync(server).validate_token(make_token(kid="key-1", sub="alice")).subject

    assert asyncio.run(inside_a_loop()) == "alice"


def test_sync_client_closes_only_what_it_created(server: Server) -> None:
    shared = httpx.Client(transport=httpx.MockTransport(server.handle))
    with SyncAuthnClient(
        authorize_url=f"{BASE}/authorize",
        token_url=TOKEN_URL,
        jwks_url=JWKS_URL,
        http_client=shared,
    ):
        pass
    assert not shared.is_closed
    shared.close()


async def test_async_client_closes_what_it_created() -> None:
    client = AuthnClient.from_base_url(BASE)
    async with client:
        pass
    assert client is not None


def test_from_base_url_derives_the_endpoints() -> None:
    client = SyncAuthnClient.from_base_url("https://auth.example.com/")
    assert client.jwks_url == "https://auth.example.com/.well-known/jwks.json"
    assert client.authorize_url("https://app/cb", "s").startswith(
        "https://auth.example.com/authorize?"
    )
    client.close()


# -- the token a refresh hands back ----------------------------------------


async def test_a_refreshed_token_with_the_wrong_audience_is_refused(
    server: Server, make_token: Any
) -> None:
    """A refresh is not a way around the audience check.

    Whoever holds a refresh token would otherwise trade a token minted for
    another audience for one that is never checked at all.
    """
    server.token_response = (
        200,
        {"access_token": make_token(kid="key-1", aud="other"), "id_token": "new-it"},
    )
    tokens = Tokens(
        access_token=make_token(kid="key-1", aud="wanted", expires_in=-3600), refresh_token="rt"
    )
    with pytest.raises(InvalidAudienceError):
        await build_async(server).authenticate(tokens, audience="wanted")


async def test_a_refreshed_token_that_is_already_expired_is_refused(
    server: Server, make_token: Any
) -> None:
    server.token_response = (
        200,
        {"access_token": make_token(kid="key-1", expires_in=-3600), "id_token": "new-it"},
    )
    tokens = Tokens(access_token=make_token(kid="key-1", expires_in=-3600), refresh_token="rt")
    with pytest.raises(TokenExpiredError):
        await build_async(server).authenticate(tokens)


async def test_a_refreshed_token_that_does_not_verify_is_refused(
    server: Server, make_token: Any
) -> None:
    server.token_response = (
        200,
        {"access_token": make_token(kid="key-1", sign=False), "id_token": "new-it"},
    )
    tokens = Tokens(access_token=make_token(kid="key-1", expires_in=-3600), refresh_token="rt")
    with pytest.raises(SignatureVerificationError):
        await build_async(server).authenticate(tokens)


async def test_a_valid_refreshed_token_is_returned(server: Server, make_token: Any) -> None:
    server.token_response = (
        200,
        {"access_token": make_token(kid="key-1"), "id_token": "new-it"},
    )
    tokens = Tokens(access_token=make_token(kid="key-1", expires_in=-3600), refresh_token="rt")
    result = await build_async(server).authenticate(tokens)

    assert result.refreshed is True
    assert result.tokens.refresh_token == "rt"


def test_sync_refreshed_token_with_the_wrong_audience_is_refused(
    server: Server, make_token: Any
) -> None:
    """The blocking client carries its own copy of authenticate."""
    server.token_response = (
        200,
        {"access_token": make_token(kid="key-1", aud="other"), "id_token": "new-it"},
    )
    tokens = Tokens(
        access_token=make_token(kid="key-1", aud="wanted", expires_in=-3600), refresh_token="rt"
    )
    with pytest.raises(InvalidAudienceError):
        build_sync(server).authenticate(tokens, audience="wanted")
