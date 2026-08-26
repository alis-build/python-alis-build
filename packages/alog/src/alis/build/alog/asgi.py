"""ASGI middleware that binds a request's trace onto its log lines.

Pure ASGI3 with no framework import, so it works under Starlette, FastAPI,
Litestar, Quart and async Django alike::

    app = TraceContextMiddleware(app)

Every log line written while handling a request then carries that request's
trace, whichever library wrote it, without any handler having to pass a context
around.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Container, Mapping, MutableMapping
from typing import Any

from ._context import bind
from ._trace import TRACE_HEADER

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

__all__ = ["ASGIApp", "Message", "Receive", "Scope", "Send", "TraceContextMiddleware"]


class TraceContextMiddleware:
    """Binds the inbound trace context for the duration of each request.

    A request arriving without the header, or with one naming no trace, is
    handled exactly as before -- its lines simply carry no correlation. An
    inbound header is not something the service controls, so a malformed one is
    never a reason to refuse a request.

    Args:
        app: The ASGI application to wrap.
        labels: Labels to bind on every request, such as the service name.
        exclude_paths: Paths to pass straight through. Health and readiness
            endpoints usually belong here, since correlating them is noise.
    """

    __slots__ = ("_app", "_exclude_paths", "_labels")

    def __init__(
        self,
        app: ASGIApp,
        /,
        *,
        labels: Mapping[str, str] | None = None,
        exclude_paths: Container[str] = (),
    ) -> None:
        self._app = app
        self._labels = labels
        self._exclude_paths = exclude_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handles one ASGI event stream."""
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return
        if scope.get("path", "") in self._exclude_paths:
            await self._app(scope, receive, send)
            return

        with bind(trace_context=header(scope), labels=self._labels):
            await self._app(scope, receive, send)


def header(scope: Scope, /) -> str:
    """Returns the trace header a request carries, or empty if it carries none.

    ASGI guarantees lowercase header names, so no case folding is needed.

    Args:
        scope: The ASGI connection scope.
    """
    wanted = TRACE_HEADER.encode("latin-1")
    for name, value in scope.get("headers", ()):
        if name == wanted:
            return str(value.decode("latin-1"))
    return ""
