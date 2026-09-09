"""The ASGI route that resumable callbacks arrive on.

Pure ASGI3 with no framework import, so it works under Starlette, FastAPI,
Litestar, Quart and async Django alike, and under a bare ASGI app with no
router at all::

    app = ResumeMiddleware(app, client)

Anything that is not a callback passes straight through untouched.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, MutableMapping
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import parse_qs

from ._errors import (
    HandlerError,
    HandlerNotFoundError,
    OperationNameError,
    OperationNotFoundError,
)

if TYPE_CHECKING:
    from ._client import Client

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

__all__ = ["ASGIApp", "Message", "Receive", "ResumeMiddleware", "Scope", "Send"]

_CALLBACK_METHOD: Final = "PUT"


class ResumeMiddleware:
    """Serves the resumable callback route and passes everything else through.

    Args:
        app: The application to wrap.
        client: The client whose handlers resume the operations.
        prefix: Where callbacks are served. Defaults to the client's
            ``callback_prefix``; passing one overrides it **on the client** too,
            so the URL the client schedules and the path served here cannot
            drift apart.
    """

    __slots__ = ("_app", "_client", "_prefix")

    def __init__(self, app: ASGIApp, client: Client, /, *, prefix: str | None = None) -> None:
        self._app = app
        self._client = client
        if prefix is not None:
            client.set_callback_prefix(prefix)
        self._prefix = client.config.callback_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (
            scope.get("type") != "http"
            or scope.get("method") != _CALLBACK_METHOD
            or not path.startswith(self._prefix)
        ):
            await self._app(scope, receive, send)
            return

        handler_path = path[len(self._prefix) :]
        name = _query_param(scope.get("query_string", b""), "operation")
        if not name:
            await _respond(send, 400, b"missing operation query parameter\n")
            return

        try:
            # The handler is synchronous and may block on the store, so it runs
            # off the event loop rather than stalling every other request.
            await asyncio.to_thread(self._client.resume_now, handler_path, name)
        except OperationNameError:
            await _respond(send, 400, b"invalid operation name\n")
        except HandlerNotFoundError:
            await _respond(send, 404, b"no handler for that path\n")
        except OperationNotFoundError:
            await _respond(send, 404, b"operation not found\n")
        except HandlerError:
            # resume_now already marked the operation failed. The 500 stops
            # Cloud Tasks retrying work that is already terminal.
            await _respond(send, 500, b"resumable handler failed\n")
        else:
            await _respond(send, 200, b"")


def _query_param(query_string: bytes, key: str) -> str:
    """Returns one query parameter, or an empty string when it is absent."""
    values = parse_qs(query_string.decode("latin-1")).get(key, [])
    return values[0] if values else ""


async def _respond(send: Send, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body})
