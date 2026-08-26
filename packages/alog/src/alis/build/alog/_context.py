"""Ambient metadata that every log line in a request picks up.

The metadata is ambient: it is bound to context variables rather than passed
down through every call, so nothing here takes a context parameter and a
logging call deep in a handler still lands with the request's trace on it.

Three behaviours are worth knowing before relying on this:

* ``asyncio.create_task`` and ``TaskGroup.create_task`` snapshot the current
  context, so child tasks inherit the binding and a child's change never leaks
  back to its parent.
* Awaiting a coroutine does *not* create a new context, so anything bound
  without a matching reset persists in the caller. :func:`bind` is a context
  manager for that reason.
* ``threading.Thread`` and ``Executor.submit`` start with an empty context, so
  the binding does not cross. ``asyncio.to_thread`` copies the context, so it
  does. To carry it explicitly, use ``contextvars.copy_context().run(fn)``.

Where a framework hands you separate enter and exit callbacks rather than a
block, drive :func:`bind` with :class:`contextlib.ExitStack`.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final

from ._trace import TraceContext, parse_trace_context

_EMPTY: Final[Mapping[str, Any]] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class LogOperation:
    """A long-running operation that a group of log lines belongs to.

    Entries sharing an id are shown together, and the first and last markers let
    Logs Explorer show the operation as a unit rather than as loose lines.
    """

    id: str = ""
    """An arbitrary identifier. Entries sharing one are treated as one operation."""

    producer: str = ""
    """An arbitrary producer identifier, unique with ``id``. Often a repository URL."""

    first: bool = False
    """Whether this is the first entry in the operation."""

    last: bool = False
    """Whether this is the last entry in the operation."""


@dataclass(frozen=True, slots=True)
class HttpRequest:
    """The HTTP request a log line describes.

    Populating this is what makes a line render as a request in Logs Explorer,
    with the method, status and latency shown inline.
    """

    request_method: str = ""
    """The method, such as ``"GET"`` or ``"POST"``."""

    request_url: str = ""
    """Scheme, host, path and query of the requested URL."""

    request_size: int = 0
    """Size of the request in bytes, headers and body included."""

    status: int = 0
    """The response status code."""

    response_size: int = 0
    """Size of the response in bytes, headers and body included."""

    user_agent: str = ""
    """The ``User-Agent`` the client sent."""

    remote_ip: str = ""
    """IP of the client that issued the request. May include a port."""

    server_ip: str = ""
    """IP of the origin server the request went to. May include a port."""

    referer: str = ""
    """The ``Referer`` the client sent."""

    latency: str = ""
    """Processing time as seconds with up to nine decimals and a trailing ``s``."""

    cache_lookup: bool = False
    """Whether a cache lookup was attempted."""

    cache_hit: bool = False
    """Whether the response was served from cache."""

    cache_validated_with_origin_server: bool = False
    """Whether a cache hit was revalidated upstream. Only meaningful with ``cache_hit``."""

    cache_fill_bytes: int = 0
    """Response bytes written into the cache, when a fill was attempted."""

    protocol: str = ""
    """The protocol used, such as ``"HTTP/1.1"``."""


@dataclass(frozen=True, slots=True)
class BoundContext:
    """Everything currently bound, as one snapshot."""

    trace: TraceContext | None = None
    labels: Mapping[str, str] = field(default_factory=lambda: _EMPTY)
    operation: LogOperation | None = None
    insert_id: str = ""
    http_request: HttpRequest | None = None
    fields: Mapping[str, Any] = field(default_factory=lambda: _EMPTY)


_trace_var: ContextVar[TraceContext | None] = ContextVar("alis.build.alog.trace", default=None)
_labels_var: ContextVar[Mapping[str, str]] = ContextVar("alis.build.alog.labels", default=_EMPTY)
_operation_var: ContextVar[LogOperation | None] = ContextVar(
    "alis.build.alog.operation", default=None
)
_insert_id_var: ContextVar[str] = ContextVar("alis.build.alog.insert_id", default="")
_http_request_var: ContextVar[HttpRequest | None] = ContextVar(
    "alis.build.alog.http_request", default=None
)
_fields_var: ContextVar[Mapping[str, Any]] = ContextVar("alis.build.alog.fields", default=_EMPTY)


def current() -> BoundContext:
    """Returns everything bound in the current context.

    Useful for reading the request's trace id back out -- to echo it in a
    response header, say -- and for a formatter of your own.
    """
    return BoundContext(
        trace=_trace_var.get(),
        labels=_labels_var.get(),
        operation=_operation_var.get(),
        insert_id=_insert_id_var.get(),
        http_request=_http_request_var.get(),
        fields=_fields_var.get(),
    )


@contextmanager
def bind(
    *,
    trace_context: str | None = None,
    labels: Mapping[str, str] | None = None,
    operation: LogOperation | None = None,
    insert_id: str | None = None,
    http_request: HttpRequest | None = None,
    **fields: Any,
) -> Iterator[None]:
    """Binds metadata onto every log line emitted inside the block.

    Nesting merges the mapping-shaped arguments and replaces the rest, which is
    what each one is usually for: an outer block binds the labels and fields
    that hold for a whole service, an inner one adds a route's, and a nested
    ``bind`` naming a new trace means a genuinely different request. On a key
    collision the inner value wins.

    Args:
        trace_context: A raw ``x-cloud-trace-context`` header value. One naming
            no trace id binds nothing rather than raising, since an inbound
            header is not something the service controls.
        labels: Indexed labels. Merged with any already bound.
        operation: The operation these lines belong to.
        insert_id: A unique id for the entry, used to order entries written
            within the same microsecond and to discard duplicates.
        http_request: The request these lines describe.
        **fields: Arbitrary keys merged into the log line itself. Merged with
            any already bound. A key that collides with one the format defines
            is dropped rather than allowed to overwrite it.

    Yields:
        Nothing. The binding is ambient for the duration of the block.
    """
    tokens: list[tuple[ContextVar[Any], Token[Any]]] = []

    if trace_context is not None and (trace := parse_trace_context(trace_context)) is not None:
        tokens.append((_trace_var, _trace_var.set(trace)))
    if labels:
        tokens.append((_labels_var, _labels_var.set({**_labels_var.get(), **labels})))
    if operation is not None:
        tokens.append((_operation_var, _operation_var.set(operation)))
    if insert_id is not None:
        tokens.append((_insert_id_var, _insert_id_var.set(insert_id)))
    if http_request is not None:
        tokens.append((_http_request_var, _http_request_var.set(http_request)))
    if fields:
        tokens.append((_fields_var, _fields_var.set({**_fields_var.get(), **fields})))

    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
