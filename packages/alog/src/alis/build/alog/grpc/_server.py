"""Server interceptors that bind a call's trace onto its log lines.

The trace is bound around the *handler*, not around interception itself. That
matters for two reasons: the handler may run in a different task from the
interceptor, and a synchronous server reuses pooled threads, so a trace that is
set and never reset would bleed into the next, unrelated call.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Container, Iterator, Mapping
from typing import Any

import grpc

from .._context import bind
from ._metadata import trace_context_from_metadata

_FACTORIES = {
    "unary_unary": grpc.unary_unary_rpc_method_handler,
    "unary_stream": grpc.unary_stream_rpc_method_handler,
    "stream_unary": grpc.stream_unary_rpc_method_handler,
    "stream_stream": grpc.stream_stream_rpc_method_handler,
}


def _shape(handler: Any) -> tuple[str, Callable[..., Any]]:
    """Returns which of the four RPC shapes a handler implements."""
    for name in _FACTORIES:
        behavior = getattr(handler, name, None)
        if behavior is not None:
            return name, behavior
    raise ValueError("rpc method handler implements none of the four call shapes")


def _rebuild(handler: Any, shape: str, behavior: Callable[..., Any]) -> Any:
    """Returns a handler of the same shape with a replaced behavior.

    Built through the public factories rather than by mutating the handler,
    whose concrete type is an implementation detail.
    """
    return _FACTORIES[shape](
        behavior,
        request_deserializer=handler.request_deserializer,
        response_serializer=handler.response_serializer,
    )


def _is_response_streaming(shape: str) -> bool:
    return shape in ("unary_stream", "stream_stream")


class TraceContextInterceptor(grpc.ServerInterceptor):  # type: ignore[misc]
    """Binds the inbound trace context for each RPC on a synchronous server.

    A call arriving without the header, or with one naming no trace, is handled
    exactly as before -- its lines simply carry no correlation.

    Args:
        labels: Labels to bind on every call, such as the service name.
        exclude_methods: Fully qualified method names to pass straight through,
            such as ``"/grpc.health.v1.Health/Check"``.
    """

    __slots__ = ("_exclude_methods", "_labels")

    def __init__(
        self,
        *,
        labels: Mapping[str, str] | None = None,
        exclude_methods: Container[str] = (),
    ) -> None:
        self._labels = labels
        self._exclude_methods = exclude_methods

    def intercept_service(
        self, continuation: Callable[[Any], Any], handler_call_details: Any
    ) -> Any:
        """Wraps the handler for one RPC so its log lines carry the call's trace."""
        handler = continuation(handler_call_details)
        if handler is None:
            return None
        if handler_call_details.method in self._exclude_methods:
            return handler

        header = trace_context_from_metadata(handler_call_details.invocation_metadata or ())
        shape, behavior = _shape(handler)
        return _rebuild(handler, shape, _sync_wrap(behavior, header, self._labels, shape))


class AsyncTraceContextInterceptor(grpc.aio.ServerInterceptor):  # type: ignore[misc]
    """Binds the inbound trace context for each RPC on an asyncio server.

    Takes the same arguments as :class:`TraceContextInterceptor`.
    """

    __slots__ = ("_exclude_methods", "_labels")

    def __init__(
        self,
        *,
        labels: Mapping[str, str] | None = None,
        exclude_methods: Container[str] = (),
    ) -> None:
        self._labels = labels
        self._exclude_methods = exclude_methods

    async def intercept_service(
        self, continuation: Callable[[Any], Awaitable[Any]], handler_call_details: Any
    ) -> Any:
        """Wraps the handler for one RPC so its log lines carry the call's trace."""
        handler = await continuation(handler_call_details)
        if handler is None:
            return None
        if handler_call_details.method in self._exclude_methods:
            return handler

        header = trace_context_from_metadata(handler_call_details.invocation_metadata or ())
        shape, behavior = _shape(handler)
        return _rebuild(handler, shape, _async_wrap(behavior, header, self._labels, shape))


def _sync_wrap(
    behavior: Callable[..., Any],
    header: str,
    labels: Mapping[str, str] | None,
    shape: str,
) -> Callable[..., Any]:
    """Binds the trace around a synchronous handler."""
    if _is_response_streaming(shape):

        def stream(request: Any, context: Any) -> Iterator[Any]:
            # The trace must stay bound while the generator runs, not merely
            # while it is created, so the block wraps the whole yield.
            with bind(trace_context=header, labels=labels):
                yield from behavior(request, context)

        return stream

    def unary(request: Any, context: Any) -> Any:
        with bind(trace_context=header, labels=labels):
            return behavior(request, context)

    return unary


def _async_wrap(
    behavior: Callable[..., Any],
    header: str,
    labels: Mapping[str, str] | None,
    shape: str,
) -> Callable[..., Any]:
    """Binds the trace around an asyncio handler.

    A response-streaming handler may be written either as an async generator or
    as a coroutine that writes through the call context, so both are supported.
    """
    if _is_response_streaming(shape) and inspect.isasyncgenfunction(behavior):

        async def generator(request: Any, context: Any) -> AsyncIterator[Any]:
            with bind(trace_context=header, labels=labels):
                async for response in behavior(request, context):
                    yield response

        return generator

    async def coroutine(request: Any, context: Any) -> Any:
        with bind(trace_context=header, labels=labels):
            result = behavior(request, context)
            if inspect.isawaitable(result):
                return await result
            return result

    return coroutine
