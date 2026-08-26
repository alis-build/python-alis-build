"""Client interceptors that forward the ambient identity on outgoing calls.

Attach one to a channel and every call made through it carries the identity
that is bound for the current context, so a service handling one request and
calling another passes the caller through without threading it by hand::

    channel = grpc.intercept_channel(channel, *identity_client_interceptors())

Calls made with no identity bound are left untouched, which keeps startup and
health-check traffic working.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from typing import Any, NamedTuple

import grpc

from .._context import from_context_or_none
from ._metadata import identity_metadata

__all__ = [
    "AsyncIdentityClientInterceptor",
    "IdentityClientInterceptor",
    "identity_client_interceptors",
]


class _CallDetails(NamedTuple):
    """A client call's details, rebuilt so the metadata can be extended."""

    method: Any
    timeout: Any
    metadata: Any
    credentials: Any
    wait_for_ready: Any
    compression: Any


class _IdentityForwarder:
    """Shared behaviour for the synchronous client interceptors."""

    __slots__ = ("_legacy",)

    def __init__(self, *, legacy: bool = False) -> None:
        self._legacy = legacy

    def _details(self, details: Any) -> Any:
        identity = from_context_or_none()
        if identity is None:
            return details
        metadata: list[tuple[str, Any]] = list(details.metadata or ())
        metadata.extend(identity_metadata(identity, legacy=self._legacy))
        return _CallDetails(
            method=details.method,
            timeout=details.timeout,
            metadata=metadata,
            credentials=details.credentials,
            wait_for_ready=details.wait_for_ready,
            compression=getattr(details, "compression", None),
        )


class IdentityClientInterceptor(
    _IdentityForwarder,
    grpc.UnaryUnaryClientInterceptor,  # type: ignore[misc]
    grpc.UnaryStreamClientInterceptor,  # type: ignore[misc]
    grpc.StreamUnaryClientInterceptor,  # type: ignore[misc]
    grpc.StreamStreamClientInterceptor,  # type: ignore[misc]
):
    """Forwards the ambient identity on every call through a channel.

    Args:
        legacy: Also send the unsigned-token entry some older services read.
    """

    def intercept_unary_unary(
        self, continuation: Callable[[Any, Any], Any], client_call_details: Any, request: Any
    ) -> Any:
        return continuation(self._details(client_call_details), request)

    def intercept_unary_stream(
        self, continuation: Callable[[Any, Any], Any], client_call_details: Any, request: Any
    ) -> Any:
        return continuation(self._details(client_call_details), request)

    def intercept_stream_unary(
        self,
        continuation: Callable[[Any, Any], Any],
        client_call_details: Any,
        request_iterator: Iterator[Any],
    ) -> Any:
        return continuation(self._details(client_call_details), request_iterator)

    def intercept_stream_stream(
        self,
        continuation: Callable[[Any, Any], Any],
        client_call_details: Any,
        request_iterator: Iterator[Any],
    ) -> Any:
        return continuation(self._details(client_call_details), request_iterator)


class AsyncIdentityClientInterceptor(
    grpc.aio.UnaryUnaryClientInterceptor,  # type: ignore[misc]
    grpc.aio.UnaryStreamClientInterceptor,  # type: ignore[misc]
    grpc.aio.StreamUnaryClientInterceptor,  # type: ignore[misc]
    grpc.aio.StreamStreamClientInterceptor,  # type: ignore[misc]
):
    """Forwards the ambient identity on every call through an asyncio channel.

    Args:
        legacy: Also send the unsigned-token entry some older services read.
    """

    __slots__ = ("_legacy",)

    def __init__(self, *, legacy: bool = False) -> None:
        self._legacy = legacy

    def _details(self, details: Any) -> Any:
        identity = from_context_or_none()
        if identity is None:
            return details
        metadata = grpc.aio.Metadata(*(details.metadata or ()))
        for key, value in identity_metadata(identity, legacy=self._legacy):
            metadata.add(key, value)
        return grpc.aio.ClientCallDetails(
            method=details.method,
            timeout=details.timeout,
            metadata=metadata,
            credentials=details.credentials,
            wait_for_ready=details.wait_for_ready,
        )

    async def intercept_unary_unary(
        self, continuation: Callable[[Any, Any], Any], client_call_details: Any, request: Any
    ) -> Any:
        return await continuation(self._details(client_call_details), request)

    async def intercept_unary_stream(
        self, continuation: Callable[[Any, Any], Any], client_call_details: Any, request: Any
    ) -> Any:
        return await continuation(self._details(client_call_details), request)

    async def intercept_stream_unary(
        self,
        continuation: Callable[[Any, Any], Any],
        client_call_details: Any,
        request_iterator: Any,
    ) -> Any:
        return await continuation(self._details(client_call_details), request_iterator)

    async def intercept_stream_stream(
        self,
        continuation: Callable[[Any, Any], Any],
        client_call_details: Any,
        request_iterator: Any,
    ) -> Any:
        return await continuation(self._details(client_call_details), request_iterator)


def identity_client_interceptors(*, legacy: bool = False) -> Sequence[Any]:
    """Returns the interceptors to pass to ``grpc.intercept_channel``."""
    return (IdentityClientInterceptor(legacy=legacy),)
