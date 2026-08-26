"""The ASGI identity middleware, driven without any web framework."""

from __future__ import annotations

from typing import Any

import pytest

from alis.build.iam import Identity, dumps, from_context, from_context_or_none
from alis.build.iam.asgi import IdentityMiddleware, Receive, Scope, Send
from alis.build.iam.headers import HEADER_NAME


class RecordingApp:
    """An inner app that records what it saw, so tests can assert on it."""

    def __init__(self) -> None:
        self.calls = 0
        self.seen: list[Identity | None] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.calls += 1
        self.seen.append(from_context_or_none())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def http_scope(headers: dict[str, str] | None = None, path: str = "/") -> Scope:
    return {
        "type": "http",
        "path": path,
        "scheme": "http",
        "headers": [
            (name.encode("latin-1"), value.encode("latin-1"))
            for name, value in (headers or {}).items()
        ],
    }


async def drive(app: Any, scope: Scope) -> list[dict[str, Any]]:
    """Runs one request and returns the messages the app sent."""
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Any) -> None:
        sent.append(dict(message))

    await app(scope, receive, send)
    return sent


async def test_identity_reaches_the_handler(user: Identity) -> None:
    inner = RecordingApp()
    scope = http_scope({HEADER_NAME: dumps(user).decode()})
    sent = await drive(IdentityMiddleware(inner), scope)

    assert inner.seen == [user]
    assert sent[0]["status"] == 200


async def test_identity_does_not_leak_past_the_request(user: Identity) -> None:
    inner = RecordingApp()
    await drive(IdentityMiddleware(inner), http_scope({HEADER_NAME: dumps(user).decode()}))
    assert from_context_or_none() is None


async def test_missing_identity_is_rejected() -> None:
    inner = RecordingApp()
    sent = await drive(IdentityMiddleware(inner), http_scope())

    assert inner.calls == 0, "the handler must not run for a rejected request"
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 401
    assert sent[1]["body"] == b"unauthenticated\n"
    assert (b"content-length", b"16") in sent[0]["headers"]


async def test_malformed_identity_is_rejected() -> None:
    inner = RecordingApp()
    sent = await drive(IdentityMiddleware(inner), http_scope({HEADER_NAME: "not json"}))
    assert inner.calls == 0
    assert sent[0]["status"] == 401


async def test_optional_mode_lets_anonymous_requests_through() -> None:
    inner = RecordingApp()
    sent = await drive(IdentityMiddleware(inner, required=False), http_scope())

    assert inner.seen == [None]
    assert sent[0]["status"] == 200


async def test_excluded_paths_skip_the_check() -> None:
    inner = RecordingApp()
    app = IdentityMiddleware(inner, exclude_paths={"/healthz"})

    sent = await drive(app, http_scope(path="/healthz"))
    assert inner.seen == [None]
    assert sent[0]["status"] == 200

    sent = await drive(app, http_scope(path="/other"))
    assert sent[0]["status"] == 401


async def test_lifespan_scope_passes_through() -> None:
    """Anything that is not a request must not be touched."""
    seen: list[Scope] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope)

    await drive(IdentityMiddleware(inner), {"type": "lifespan"})
    assert seen == [{"type": "lifespan"}]


async def test_websocket_is_closed_rather_than_given_a_401() -> None:
    """A websocket has no response to reject with, so close the handshake."""
    inner = RecordingApp()
    scope: Scope = {"type": "websocket", "path": "/ws", "scheme": "ws", "headers": []}
    sent = await drive(IdentityMiddleware(inner), scope)

    assert inner.calls == 0
    assert sent == [{"type": "websocket.close", "code": 1008}]


async def test_websocket_with_an_identity_reaches_the_handler(user: Identity) -> None:
    inner = RecordingApp()
    scope: Scope = {
        "type": "websocket",
        "path": "/ws",
        "scheme": "ws",
        "headers": [(HEADER_NAME.encode(), dumps(user))],
    }
    await drive(IdentityMiddleware(inner), scope)
    assert inner.seen == [user]


async def test_handlers_can_use_from_context(user: Identity) -> None:
    resolved: list[str] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        resolved.append(from_context().policy_member())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    scope = http_scope({HEADER_NAME: dumps(user).decode()})
    sent = await drive(IdentityMiddleware(inner), scope)

    assert resolved == ["user:1934872948"]
    assert sent[0]["status"] == 204


@pytest.mark.parametrize("header", ["x-alis-identity", "X-Alis-Identity"])
async def test_header_case_from_the_wire(user: Identity, header: str) -> None:
    """ASGI lowercases header names, but tolerate a server that does not."""
    inner = RecordingApp()
    await drive(IdentityMiddleware(inner), http_scope({header: dumps(user).decode()}))
    assert inner.seen == [user]
