"""gRPC interceptors that bind a call's trace onto its log lines.

Driven through the interceptor's own public surface with hand-built handlers
rather than a running server: an in-process server would add a port, a thread
and a wait to a suite that has none of those.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import grpc
import pytest

from alis.build import alog
from alis.build.alog.grpc import (
    METADATA_KEY,
    AsyncTraceContextInterceptor,
    TraceContextInterceptor,
    trace_context_from_metadata,
    trace_metadata,
)
from alis.build.alog.testing import Captured, capture, fresh_logging_config

TRACED = [(METADATA_KEY, "abc123/456;o=1")]


@pytest.fixture(autouse=True)
def fresh() -> Iterator[None]:
    with fresh_logging_config():
        yield


class Details:
    """Stands in for gRPC's handler_call_details."""

    def __init__(self, method: str = "/example.v1.Books/Get", metadata: Any = ()) -> None:
        self.method = method
        self.invocation_metadata = metadata


def unary_handler() -> Any:
    def behavior(request: Any, context: Any) -> str:
        alog.info("handled")
        return "ok"

    return grpc.unary_unary_rpc_method_handler(behavior)


def stream_handler() -> Any:
    def behavior(request: Any, context: Any) -> Iterator[str]:
        alog.info("first")
        yield "a"
        alog.info("second")
        yield "b"

    return grpc.unary_stream_rpc_method_handler(behavior)


def async_unary_handler() -> Any:
    async def behavior(request: Any, context: Any) -> str:
        alog.info("handled")
        return "ok"

    return grpc.unary_unary_rpc_method_handler(behavior)


def async_stream_handler() -> Any:
    async def behavior(request: Any, context: Any) -> Any:
        alog.info("first")
        yield "a"
        alog.info("second")
        yield "b"

    return grpc.unary_stream_rpc_method_handler(behavior)


# --------------------------------------------------------------------------
# metadata
# --------------------------------------------------------------------------


def test_the_key_matches_the_http_header() -> None:
    assert METADATA_KEY == "x-cloud-trace-context"


@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param([("x-cloud-trace-context", "abc/1")], id="text"),
        pytest.param([("x-cloud-trace-context", b"abc/1")], id="bytes"),
        pytest.param([("X-Cloud-Trace-Context", "abc/1")], id="hand_built_with_capitals"),
    ],
)
def test_the_header_is_read_however_it_arrives(metadata: Any) -> None:
    assert trace_context_from_metadata(metadata) == "abc/1"


def test_the_most_recent_value_wins() -> None:
    metadata = [("x-cloud-trace-context", "old/1"), ("x-cloud-trace-context", "new/2")]
    assert trace_context_from_metadata(metadata) == "new/2"


def test_metadata_without_the_key_reads_as_empty() -> None:
    assert trace_context_from_metadata([("authorization", "bearer x")]) == ""


def test_forwarding_builds_one_pair() -> None:
    assert trace_metadata("abc/1") == [("x-cloud-trace-context", "abc/1")]


def test_there_is_nothing_to_forward_without_a_trace() -> None:
    assert trace_metadata("") == []


# --------------------------------------------------------------------------
# the synchronous interceptor
# --------------------------------------------------------------------------


def run_sync(handler: Any, details: Details) -> Captured:
    interceptor = TraceContextInterceptor()
    wrapped = interceptor.intercept_service(lambda _: handler, details)
    with capture() as captured:
        result = wrapped.unary_unary or wrapped.unary_stream
        outcome = result(object(), object())
        if hasattr(outcome, "__iter__") and not isinstance(outcome, str):
            list(outcome)
    return captured


def test_a_call_with_a_trace_correlates_its_lines(project: str) -> None:
    captured = run_sync(unary_handler(), Details(metadata=TRACED))

    assert captured.entries[0]["logging.googleapis.com/trace"] == (
        "projects/my-project/traces/abc123"
    )


def test_a_call_without_the_key_is_handled_as_before(project: str) -> None:
    captured = run_sync(unary_handler(), Details())

    assert captured.messages == ["handled"]
    assert "logging.googleapis.com/trace" not in captured.entries[0]


def test_the_trace_stays_bound_for_the_whole_stream(project: str) -> None:
    """The binding has to wrap the yields, not merely the generator's creation."""
    captured = run_sync(stream_handler(), Details(metadata=TRACED))

    assert captured.messages == ["first", "second"]
    assert all("logging.googleapis.com/trace" in entry for entry in captured.entries)


def test_the_binding_does_not_outlive_the_call(project: str) -> None:
    """A synchronous server reuses pooled threads, so a leak lands on the next call."""
    run_sync(unary_handler(), Details(metadata=TRACED))
    assert alog.current().trace is None


def test_an_excluded_method_is_passed_straight_through(project: str) -> None:
    interceptor = TraceContextInterceptor(exclude_methods={"/grpc.health.v1.Health/Check"})
    handler = unary_handler()
    details = Details(method="/grpc.health.v1.Health/Check", metadata=TRACED)

    assert interceptor.intercept_service(lambda _: handler, details) is handler


def test_an_unknown_method_stays_unknown() -> None:
    """Returning a handler where the server found none would answer a call it should not."""
    interceptor = TraceContextInterceptor()
    assert interceptor.intercept_service(lambda _: None, Details()) is None


def test_labels_can_be_bound_for_every_call() -> None:
    interceptor = TraceContextInterceptor(labels={"service": "books"})
    wrapped = interceptor.intercept_service(lambda _: unary_handler(), Details())

    with capture() as captured:
        wrapped.unary_unary(object(), object())

    assert captured.entries[0]["logging.googleapis.com/labels"] == {"service": "books"}


# --------------------------------------------------------------------------
# the asyncio interceptor
# --------------------------------------------------------------------------


async def test_an_async_call_correlates_its_lines(project: str) -> None:
    interceptor = AsyncTraceContextInterceptor()

    async def continuation(_: Any) -> Any:
        return async_unary_handler()

    wrapped = await interceptor.intercept_service(continuation, Details(metadata=TRACED))
    with capture() as captured:
        await wrapped.unary_unary(object(), object())

    assert "logging.googleapis.com/trace" in captured.entries[0]


async def test_an_async_generator_stays_bound_for_the_whole_stream(project: str) -> None:
    interceptor = AsyncTraceContextInterceptor()

    async def continuation(_: Any) -> Any:
        return async_stream_handler()

    wrapped = await interceptor.intercept_service(continuation, Details(metadata=TRACED))
    with capture() as captured:
        async for _ in wrapped.unary_stream(object(), object()):
            pass

    assert captured.messages == ["first", "second"]
    assert all("logging.googleapis.com/trace" in entry for entry in captured.entries)


async def test_an_async_unknown_method_stays_unknown() -> None:
    interceptor = AsyncTraceContextInterceptor()

    async def continuation(_: Any) -> Any:
        return None

    assert await interceptor.intercept_service(continuation, Details()) is None
