"""Server interceptors, exercised against real in-process gRPC servers.

The interceptor rebuilds each method handler, and whether that rebuild is
correct depends on details of the server implementation, so these tests run
against actual servers rather than fakes for all four call shapes.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from concurrent import futures
from typing import Any

import grpc
import grpc.aio
import pytest

from alis.build.iam import Identity, from_context, from_context_or_none
from alis.build.iam.grpc import METADATA_KEY, AsyncIdentityInterceptor, IdentityInterceptor
from alis.build.iam.grpc._metadata import identity_metadata

SERVICE = "alis.test.Identity"
SHAPES = ["unary_unary", "unary_stream", "stream_unary", "stream_stream"]


def _caller() -> bytes:
    """Reports the identity the handler can see, so tests can assert on it."""
    identity = from_context_or_none()
    return (identity.id if identity else "<none>").encode()


def _sync_handlers() -> dict[str, Any]:
    def unary_unary(request: bytes, context: Any) -> bytes:
        return _caller()

    def unary_stream(request: bytes, context: Any) -> Iterator[bytes]:
        yield _caller()
        yield _caller()

    def stream_unary(requests: Iterator[bytes], context: Any) -> bytes:
        list(requests)
        return _caller()

    def stream_stream(requests: Iterator[bytes], context: Any) -> Iterator[bytes]:
        for _ in requests:
            yield _caller()

    return {
        "unary_unary": grpc.unary_unary_rpc_method_handler(unary_unary),
        "unary_stream": grpc.unary_stream_rpc_method_handler(unary_stream),
        "stream_unary": grpc.stream_unary_rpc_method_handler(stream_unary),
        "stream_stream": grpc.stream_stream_rpc_method_handler(stream_stream),
    }


def _async_handlers() -> dict[str, Any]:
    async def unary_unary(request: bytes, context: Any) -> bytes:
        await asyncio.sleep(0)
        return _caller()

    async def unary_stream(request: bytes, context: Any) -> AsyncIterator[bytes]:
        await asyncio.sleep(0)
        yield _caller()
        yield _caller()

    async def stream_unary(requests: Any, context: Any) -> bytes:
        async for _ in requests:
            pass
        return _caller()

    async def stream_stream(requests: Any, context: Any) -> AsyncIterator[bytes]:
        async for _ in requests:
            yield _caller()

    return {
        "unary_unary": grpc.unary_unary_rpc_method_handler(unary_unary),
        "unary_stream": grpc.unary_stream_rpc_method_handler(unary_stream),
        "stream_unary": grpc.stream_unary_rpc_method_handler(stream_unary),
        "stream_stream": grpc.stream_stream_rpc_method_handler(stream_stream),
    }


def _writing_stream_handler() -> Any:
    """A response-streaming handler written as a coroutine, not a generator.

    Both spellings are legal on an asyncio server, and they need different
    wrapping, so the interceptor has to cope with each.
    """

    async def behavior(request: bytes, context: Any) -> None:
        await context.write(_caller())
        await context.write(_caller())

    return grpc.unary_stream_rpc_method_handler(behavior)


@pytest.fixture
def sync_channel(user: Identity) -> Iterator[grpc.Channel]:
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=4),
        interceptors=[IdentityInterceptor(exclude_methods={f"/{SERVICE}/open"})],
    )
    handlers = _sync_handlers()
    handlers["open"] = handlers["unary_unary"]
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(SERVICE, handlers),))
    port = server.add_insecure_port("localhost:0")
    server.start()
    try:
        with grpc.insecure_channel(f"localhost:{port}") as channel:
            yield channel
    finally:
        server.stop(None)


@pytest.fixture
async def async_channel() -> AsyncIterator[grpc.aio.Channel]:
    server = grpc.aio.server(interceptors=[AsyncIdentityInterceptor()])
    handlers = _async_handlers()
    handlers["writing_stream"] = _writing_stream_handler()
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(SERVICE, handlers),))
    port = server.add_insecure_port("localhost:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"localhost:{port}") as channel:
            yield channel
    finally:
        await server.stop(None)


def _call_sync(channel: grpc.Channel, shape: str, metadata: list[tuple[str, str]]) -> list[bytes]:
    target = f"/{SERVICE}/{shape}"
    identity = lambda value: value  # noqa: E731 - raw bytes, no protobuf involved
    if shape == "unary_unary":
        return [channel.unary_unary(target, identity, identity)(b"x", metadata=metadata)]
    if shape == "unary_stream":
        return list(channel.unary_stream(target, identity, identity)(b"x", metadata=metadata))
    if shape == "stream_unary":
        return [channel.stream_unary(target, identity, identity)(iter([b"x"]), metadata=metadata)]
    return list(channel.stream_stream(target, identity, identity)(iter([b"x"]), metadata=metadata))


async def _call_async(
    channel: grpc.aio.Channel, shape: str, metadata: list[tuple[str, str]]
) -> list[bytes]:
    target = f"/{SERVICE}/{shape}"
    identity = lambda value: value  # noqa: E731
    if shape == "unary_unary":
        return [await channel.unary_unary(target, identity, identity)(b"x", metadata=metadata)]
    if shape == "unary_stream":
        call = channel.unary_stream(target, identity, identity)(b"x", metadata=metadata)
        return [response async for response in call]
    if shape == "stream_unary":
        call = channel.stream_unary(target, identity, identity)(metadata=metadata)
        await call.write(b"x")
        await call.done_writing()
        return [await call]
    call = channel.stream_stream(target, identity, identity)(metadata=metadata)
    await call.write(b"x")
    await call.done_writing()
    return [response async for response in call]


@pytest.mark.parametrize("shape", SHAPES)
def test_sync_identity_reaches_every_shape(
    sync_channel: grpc.Channel, user: Identity, shape: str
) -> None:
    responses = _call_sync(sync_channel, shape, identity_metadata(user))
    assert responses and all(response == b"1934872948" for response in responses)


@pytest.mark.parametrize("shape", SHAPES)
def test_sync_missing_identity_is_unauthenticated(sync_channel: grpc.Channel, shape: str) -> None:
    with pytest.raises(grpc.RpcError) as excinfo:
        _call_sync(sync_channel, shape, [])
    assert excinfo.value.code() == grpc.StatusCode.UNAUTHENTICATED


def test_sync_malformed_identity_is_unauthenticated(sync_channel: grpc.Channel) -> None:
    with pytest.raises(grpc.RpcError) as excinfo:
        _call_sync(sync_channel, "unary_unary", [(METADATA_KEY, "not json")])
    assert excinfo.value.code() == grpc.StatusCode.UNAUTHENTICATED


def test_sync_excluded_method_skips_the_check(sync_channel: grpc.Channel) -> None:
    assert _call_sync(sync_channel, "open", []) == [b"<none>"]


def test_sync_identity_does_not_leak_between_calls(
    sync_channel: grpc.Channel, user: Identity
) -> None:
    """A synchronous server reuses pooled threads, so the reset must happen."""
    assert _call_sync(sync_channel, "unary_unary", identity_metadata(user)) == [b"1934872948"]
    assert _call_sync(sync_channel, "open", []) == [b"<none>"]


@pytest.mark.parametrize("shape", SHAPES)
async def test_async_identity_reaches_every_shape(
    async_channel: grpc.aio.Channel, user: Identity, shape: str
) -> None:
    responses = await _call_async(async_channel, shape, identity_metadata(user))
    assert responses and all(response == b"1934872948" for response in responses)


@pytest.mark.parametrize("shape", SHAPES)
async def test_async_missing_identity_is_unauthenticated(
    async_channel: grpc.aio.Channel, shape: str
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as excinfo:
        await _call_async(async_channel, shape, [])
    assert excinfo.value.code() == grpc.StatusCode.UNAUTHENTICATED


async def test_async_write_style_streaming_handler(
    async_channel: grpc.aio.Channel, user: Identity
) -> None:
    """A streaming handler written as a coroutine must work too."""
    responses = await _call_async(async_channel, "writing_stream", identity_metadata(user))
    assert responses == [b"1934872948", b"1934872948"]


async def test_concurrent_calls_do_not_see_each_others_identity(
    async_channel: grpc.aio.Channel,
) -> None:
    """The property the whole design turns on."""
    identities = [Identity(id=f"id-{index}") for index in range(20)]
    results = await asyncio.gather(
        *(
            _call_async(async_channel, "unary_unary", identity_metadata(identity))
            for identity in identities
        )
    )
    assert [result[0] for result in results] == [f"id-{index}".encode() for index in range(20)]


async def test_handler_can_use_from_context(
    async_channel: grpc.aio.Channel, user: Identity
) -> None:
    responses = await _call_async(async_channel, "unary_unary", identity_metadata(user))
    assert responses[0].decode() == user.id
    assert from_context_or_none() is None, "the identity must not escape the server"


def test_from_context_raises_outside_a_call() -> None:
    with pytest.raises(Exception, match="no identity"):
        from_context()
