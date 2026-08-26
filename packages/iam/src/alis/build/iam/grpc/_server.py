"""Server interceptors that bind the caller identity for each RPC.

The identity is bound around the *handler*, not around interception itself.
That matters for two reasons: the handler may run in a different task from the
interceptor, and a synchronous server reuses pooled threads, so an identity
that is set and never reset would bleed into the next, unrelated RPC.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Container, Iterator
from typing import Any

import grpc

from .._context import identity_context
from .._errors import IdentityError
from .._identity import Identity
from .._registry import SystemConfig
from ._metadata import identity_from_metadata

_UNAUTHENTICATED = grpc.StatusCode.UNAUTHENTICATED

_FACTORIES = {
    ("unary_unary"): grpc.unary_unary_rpc_method_handler,
    ("unary_stream"): grpc.unary_stream_rpc_method_handler,
    ("stream_unary"): grpc.stream_unary_rpc_method_handler,
    ("stream_stream"): grpc.stream_stream_rpc_method_handler,
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


class IdentityInterceptor(grpc.ServerInterceptor):  # type: ignore[misc]
    """Binds the caller identity for each RPC on a synchronous server.

    Args:
        required: Reject calls that carry no usable identity with
            ``UNAUTHENTICATED``. Set it to ``False`` to let anonymous calls
            through, in which case handlers should use
            :func:`~alis.build.iam.from_context_or_none`.
        exclude_methods: Fully qualified method names that skip the check,
            such as ``"/grpc.health.v1.Health/Check"``.
        config: System email registry to consult. Defaults to the process-wide one.
    """

    __slots__ = ("_config", "_exclude_methods", "_required")

    def __init__(
        self,
        *,
        required: bool = True,
        exclude_methods: Container[str] = (),
        config: SystemConfig | None = None,
    ) -> None:
        self._required = required
        self._exclude_methods = exclude_methods
        self._config = config

    def intercept_service(
        self, continuation: Callable[[Any], Any], handler_call_details: Any
    ) -> Any:
        """Wraps the handler for one RPC, or replaces it with a refusal."""
        handler = continuation(handler_call_details)
        if handler is None:
            return None
        if handler_call_details.method in self._exclude_methods:
            return handler

        try:
            identity = identity_from_metadata(
                handler_call_details.invocation_metadata, self._config
            )
        except IdentityError as exc:
            if self._required:
                return _sync_deny(handler, str(exc))
            return handler

        shape, behavior = _shape(handler)
        return _rebuild(handler, shape, _sync_wrap(behavior, identity, shape))


class AsyncIdentityInterceptor(grpc.aio.ServerInterceptor):  # type: ignore[misc]
    """Binds the caller identity for each RPC on an asyncio server.

    Takes the same arguments as :class:`IdentityInterceptor`.
    """

    __slots__ = ("_config", "_exclude_methods", "_required")

    def __init__(
        self,
        *,
        required: bool = True,
        exclude_methods: Container[str] = (),
        config: SystemConfig | None = None,
    ) -> None:
        self._required = required
        self._exclude_methods = exclude_methods
        self._config = config

    async def intercept_service(
        self, continuation: Callable[[Any], Awaitable[Any]], handler_call_details: Any
    ) -> Any:
        """Wraps the handler for one RPC, or replaces it with a refusal."""
        handler = await continuation(handler_call_details)
        if handler is None:
            return None
        if handler_call_details.method in self._exclude_methods:
            return handler

        try:
            identity = identity_from_metadata(
                handler_call_details.invocation_metadata, self._config
            )
        except IdentityError as exc:
            if self._required:
                return _async_deny(handler, str(exc))
            return handler

        shape, behavior = _shape(handler)
        return _rebuild(handler, shape, _async_wrap(behavior, identity, shape))


def _sync_wrap(behavior: Callable[..., Any], identity: Identity, shape: str) -> Callable[..., Any]:
    """Binds the identity around a synchronous handler."""
    if _is_response_streaming(shape):

        def stream(request: Any, context: Any) -> Iterator[Any]:
            # The identity must stay bound while the generator runs, not merely
            # while it is created, so the context wraps the whole yield.
            with identity_context(identity):
                yield from behavior(request, context)

        return stream

    def unary(request: Any, context: Any) -> Any:
        with identity_context(identity):
            return behavior(request, context)

    return unary


def _async_wrap(behavior: Callable[..., Any], identity: Identity, shape: str) -> Callable[..., Any]:
    """Binds the identity around an asyncio handler.

    A response-streaming handler may be written either as an async generator or
    as a coroutine that writes through the call context, so both are supported.
    """
    if _is_response_streaming(shape) and inspect.isasyncgenfunction(behavior):

        async def generator(request: Any, context: Any) -> AsyncIterator[Any]:
            with identity_context(identity):
                async for response in behavior(request, context):
                    yield response

        return generator

    async def coroutine(request: Any, context: Any) -> Any:
        with identity_context(identity):
            result = behavior(request, context)
            if inspect.isawaitable(result):
                return await result
            return result

    return coroutine


def _sync_deny(handler: Any, message: str) -> Any:
    """Returns a same-shape handler that refuses the call."""
    shape, _ = _shape(handler)

    if _is_response_streaming(shape):

        def stream(request: Any, context: Any) -> Iterator[Any]:
            context.abort(_UNAUTHENTICATED, message)
            yield  # pragma: no cover - abort raises before this runs

        return _rebuild(handler, shape, stream)

    def unary(request: Any, context: Any) -> Any:
        context.abort(_UNAUTHENTICATED, message)

    return _rebuild(handler, shape, unary)


def _async_deny(handler: Any, message: str) -> Any:
    """Returns a same-shape handler that refuses the call."""
    shape, _ = _shape(handler)

    if _is_response_streaming(shape):

        async def stream(request: Any, context: Any) -> AsyncIterator[Any]:
            await context.abort(_UNAUTHENTICATED, message)
            yield  # pragma: no cover - abort raises before this runs

        return _rebuild(handler, shape, stream)

    async def unary(request: Any, context: Any) -> Any:
        await context.abort(_UNAUTHENTICATED, message)

    return _rebuild(handler, shape, unary)
