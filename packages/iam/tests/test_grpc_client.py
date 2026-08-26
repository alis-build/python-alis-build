"""Client interceptors forwarding the ambient identity on outgoing calls."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from concurrent import futures
from typing import Any

import grpc
import grpc.aio
import pytest

from alis.build.iam import Identity, identity_context
from alis.build.iam.grpc import (
    METADATA_KEY,
    AsyncIdentityClientInterceptor,
    IdentityClientInterceptor,
    identity_client_interceptors,
)
from alis.build.iam.grpc._metadata import LEGACY_METADATA_KEY

SERVICE = "alis.test.Echo"
_IDENTITY = lambda value: value  # noqa: E731 - raw bytes, no protobuf involved


def _echo_metadata_handlers() -> dict[str, Any]:
    """Handlers that report the identity metadata the server actually received."""

    def keys(context: Any) -> bytes:
        pairs = [
            f"{key}={value}"
            for key, value in context.invocation_metadata()
            if key in (METADATA_KEY, LEGACY_METADATA_KEY)
        ]
        return "|".join(sorted(pairs)).encode()

    def unary_unary(request: bytes, context: Any) -> bytes:
        return keys(context)

    def unary_stream(request: bytes, context: Any) -> Iterator[bytes]:
        yield keys(context)

    return {
        "unary_unary": grpc.unary_unary_rpc_method_handler(unary_unary),
        "unary_stream": grpc.unary_stream_rpc_method_handler(unary_stream),
    }


@pytest.fixture
def server_port() -> Iterator[int]:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    server.add_generic_rpc_handlers(
        (grpc.method_handlers_generic_handler(SERVICE, _echo_metadata_handlers()),)
    )
    port = server.add_insecure_port("localhost:0")
    server.start()
    try:
        yield port
    finally:
        server.stop(None)


def _call(channel: grpc.Channel) -> str:
    method = channel.unary_unary(f"/{SERVICE}/unary_unary", _IDENTITY, _IDENTITY)
    response: bytes = method(b"x")
    return response.decode()


def test_ambient_identity_is_forwarded(server_port: int, user: Identity) -> None:
    with grpc.insecure_channel(f"localhost:{server_port}") as raw:
        channel = grpc.intercept_channel(raw, IdentityClientInterceptor())
        with identity_context(user):
            received = _call(channel)
    assert received.startswith(f"{METADATA_KEY}=")
    assert '"sub":"1934872948"' in received


def test_no_identity_means_no_metadata(server_port: int) -> None:
    """Startup and health-check traffic must still work."""
    with grpc.insecure_channel(f"localhost:{server_port}") as raw:
        channel = grpc.intercept_channel(raw, IdentityClientInterceptor())
        assert _call(channel) == ""


def test_legacy_entry_is_opt_in(server_port: int, user: Identity) -> None:
    with grpc.insecure_channel(f"localhost:{server_port}") as raw:
        channel = grpc.intercept_channel(raw, IdentityClientInterceptor(legacy=True))
        with identity_context(user):
            received = _call(channel)
    assert LEGACY_METADATA_KEY in received


def test_existing_metadata_is_preserved(server_port: int, user: Identity) -> None:
    with grpc.insecure_channel(f"localhost:{server_port}") as raw:
        channel = grpc.intercept_channel(raw, IdentityClientInterceptor())
        method = channel.unary_unary(f"/{SERVICE}/unary_unary", _IDENTITY, _IDENTITY)
        with identity_context(user):
            received = method(b"x", metadata=[("trace-id", "abc")]).decode()
    assert '"sub":"1934872948"' in received


def test_response_streaming_call_is_intercepted(server_port: int, user: Identity) -> None:
    with grpc.insecure_channel(f"localhost:{server_port}") as raw:
        channel = grpc.intercept_channel(raw, IdentityClientInterceptor())
        method = channel.unary_stream(f"/{SERVICE}/unary_stream", _IDENTITY, _IDENTITY)
        with identity_context(user):
            responses = [chunk.decode() for chunk in method(b"x")]
    assert '"sub":"1934872948"' in responses[0]


def test_helper_returns_usable_interceptors(server_port: int, user: Identity) -> None:
    with grpc.insecure_channel(f"localhost:{server_port}") as raw:
        channel = grpc.intercept_channel(raw, *identity_client_interceptors())
        with identity_context(user):
            assert '"sub":"1934872948"' in _call(channel)


@pytest.fixture
async def async_server_port() -> AsyncIterator[int]:
    server = grpc.aio.server()
    server.add_generic_rpc_handlers(
        (grpc.method_handlers_generic_handler(SERVICE, _echo_metadata_handlers()),)
    )
    port = server.add_insecure_port("localhost:0")
    await server.start()
    try:
        yield port
    finally:
        await server.stop(None)


async def test_async_ambient_identity_is_forwarded(async_server_port: int, user: Identity) -> None:
    async with grpc.aio.insecure_channel(
        f"localhost:{async_server_port}", interceptors=[AsyncIdentityClientInterceptor()]
    ) as channel:
        method = channel.unary_unary(f"/{SERVICE}/unary_unary", _IDENTITY, _IDENTITY)
        with identity_context(user):
            received = (await method(b"x")).decode()
    assert '"sub":"1934872948"' in received


async def test_async_no_identity_means_no_metadata(async_server_port: int) -> None:
    async with grpc.aio.insecure_channel(
        f"localhost:{async_server_port}", interceptors=[AsyncIdentityClientInterceptor()]
    ) as channel:
        method = channel.unary_unary(f"/{SERVICE}/unary_unary", _IDENTITY, _IDENTITY)
        assert (await method(b"x")).decode() == ""
