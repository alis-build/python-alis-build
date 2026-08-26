"""ASGI middleware that binds the caller identity for each request.

Pure ASGI3 with no framework import, so it works under Starlette, FastAPI,
Litestar, Quart, and async Django alike::

    app = IdentityMiddleware(app)

Inside a handler the identity is then available from
:func:`alis.build.iam.from_context`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Container, MutableMapping
from typing import Any, Final

from ._context import identity_context
from ._errors import IdentityError
from ._registry import SystemConfig
from .headers import identity_from_headers

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_UNAUTHENTICATED_BODY: Final = b"unauthenticated\n"
_WEBSOCKET_POLICY_VIOLATION: Final = 1008

__all__ = ["ASGIApp", "IdentityMiddleware", "Message", "Receive", "Scope", "Send"]


class IdentityMiddleware:
    """Reads the identity header and binds it for the duration of the request.

    Args:
        app: The ASGI application to wrap.
        required: Reject requests that carry no usable identity with ``401``.
            Set it to ``False`` to let anonymous requests through, in which case
            handlers should use
            :func:`~alis.build.iam.from_context_or_none`.
        exclude_paths: Paths that skip the check entirely, whatever ``required``
            says. Health and readiness endpoints usually belong here.
        config: System email registry to consult. Defaults to the process-wide one.
    """

    __slots__ = ("_app", "_config", "_exclude_paths", "_required")

    def __init__(
        self,
        app: ASGIApp,
        /,
        *,
        required: bool = True,
        exclude_paths: Container[str] = (),
        config: SystemConfig | None = None,
    ) -> None:
        self._app = app
        self._required = required
        self._exclude_paths = exclude_paths
        self._config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handles one ASGI event stream."""
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return
        if scope.get("path", "") in self._exclude_paths:
            await self._app(scope, receive, send)
            return

        try:
            identity = identity_from_headers(_headers(scope), self._config)
        except IdentityError:
            if self._required:
                await _reject(scope, send)
                return
            await self._app(scope, receive, send)
            return

        with identity_context(identity):
            await self._app(scope, receive, send)


def _headers(scope: Scope) -> dict[str, str]:
    """Decodes the raw ASGI header pairs into a string mapping.

    ASGI guarantees lowercase header names, so no case folding is needed here.
    """
    return {
        name.decode("latin-1"): value.decode("latin-1") for name, value in scope.get("headers", ())
    }


async def _reject(scope: Scope, send: Send) -> None:
    """Refuses the request in whichever way its protocol allows."""
    if scope["type"] == "websocket":
        # A websocket has no response to reject with, so close before the
        # handshake completes.
        await send({"type": "websocket.close", "code": _WEBSOCKET_POLICY_VIOLATION})
        return
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(_UNAUTHENTICATED_BODY)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _UNAUTHENTICATED_BODY})
