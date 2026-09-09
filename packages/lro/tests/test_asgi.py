"""The resumable callback route, driven as a bare ASGI app.

No test client library: the middleware is pure ASGI3, so the tests speak the
protocol directly. That is also the guarantee under test — it must work without
Starlette, FastAPI or anything else in the picture.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from google.protobuf.struct_pb2 import Struct

from alis.build.lro import Client, Operation
from alis.build.lro.asgi import Message, ResumeMiddleware, Scope, Send


class Capture:
    """Records what an ASGI app was asked to send."""

    def __init__(self) -> None:
        self.messages: list[Message] = []

    async def __call__(self, message: Message) -> None:
        self.messages.append(message)

    @property
    def status(self) -> int | None:
        for message in self.messages:
            if message["type"] == "http.response.start":
                return int(message["status"])
        return None

    @property
    def body(self) -> bytes:
        return b"".join(
            message.get("body", b"")
            for message in self.messages
            if message["type"] == "http.response.body"
        )


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


def scope(path: str, *, method: str = "PUT", query: str = "", kind: str = "http") -> Scope:
    return {
        "type": kind,
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "headers": [],
    }


class Inner:
    """The wrapped application. Answers 418 so pass-through is unmistakable."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, scope: Scope, receive: Any, send: Send) -> None:
        self.calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 418, "headers": []})
        await send({"type": "http.response.body", "body": b"inner"})


@pytest.fixture
def inner() -> Inner:
    return Inner()


@pytest.fixture
def app(client: Client, inner: Inner) -> ResumeMiddleware:
    client.add_resumable_handler("create-agent", lambda op: op.complete(Struct()))
    return ResumeMiddleware(inner, client)


class TestRouting:
    async def test_put_callback_resumes_and_returns_200(
        self, app: ResumeMiddleware, client: Client
    ) -> None:
        client.new_operation("operations/abc", Struct())
        send = Capture()

        await app(
            scope("/resume-operation/create-agent", query="operation=operations/abc"),
            _receive,
            send,
        )

        assert send.status == 200
        assert client.get_operation_pb("operations/abc").done

    async def test_missing_operation_parameter_is_400(self, app: ResumeMiddleware) -> None:
        send = Capture()
        await app(scope("/resume-operation/create-agent"), _receive, send)
        assert send.status == 400

    async def test_unregistered_path_is_404(self, app: ResumeMiddleware) -> None:
        send = Capture()
        await app(
            scope("/resume-operation/unknown", query="operation=operations/abc"), _receive, send
        )
        assert send.status == 404

    async def test_unknown_operation_is_404(self, app: ResumeMiddleware) -> None:
        send = Capture()
        await app(
            scope("/resume-operation/create-agent", query="operation=operations/nope"),
            _receive,
            send,
        )
        assert send.status == 404

    async def test_malformed_operation_name_is_400(self, app: ResumeMiddleware) -> None:
        send = Capture()
        await app(
            scope("/resume-operation/create-agent", query="operation=agents/1"), _receive, send
        )
        assert send.status == 400


class TestPassThrough:
    async def test_other_paths_reach_the_app(self, app: ResumeMiddleware, inner: Inner) -> None:
        send = Capture()
        await app(scope("/v1/agents", method="GET"), _receive, send)
        assert inner.calls == ["/v1/agents"]
        assert send.status == 418

    async def test_other_methods_under_the_prefix_reach_the_app(
        self, app: ResumeMiddleware, inner: Inner
    ) -> None:
        # Only PUT is the callback. A GET here belongs to the service.
        send = Capture()
        await app(scope("/resume-operation/create-agent", method="GET"), _receive, send)
        assert inner.calls == ["/resume-operation/create-agent"]

    async def test_non_http_scopes_reach_the_app(self, app: ResumeMiddleware, inner: Inner) -> None:
        send = Capture()
        await app(scope("/ws", kind="websocket"), _receive, send)
        assert inner.calls == ["/ws"]


class TestPrefix:
    async def test_custom_prefix_serves_the_new_path(self, client: Client, inner: Inner) -> None:
        client.add_resumable_handler("create-agent", lambda op: op.complete(Struct()))
        client.new_operation("operations/abc", Struct())
        app = ResumeMiddleware(inner, client, prefix="lro")
        send = Capture()

        await app(scope("/lro/create-agent", query="operation=operations/abc"), _receive, send)

        assert send.status == 200

    async def test_custom_prefix_updates_the_client(self, client: Client, inner: Inner) -> None:
        # The client builds callback URLs from this. If the middleware kept its
        # own copy, the URL scheduled and the path served would drift apart.
        ResumeMiddleware(inner, client, prefix="lro")
        assert client.config.callback_prefix == "/lro/"

    async def test_old_prefix_no_longer_matches(self, client: Client, inner: Inner) -> None:
        client.add_resumable_handler("create-agent", lambda op: None)
        app = ResumeMiddleware(inner, client, prefix="lro")
        send = Capture()

        await app(scope("/resume-operation/create-agent"), _receive, send)

        assert inner.calls == ["/resume-operation/create-agent"]


class TestHandlerFailure:
    async def test_raising_handler_is_500_and_fails_the_operation(
        self, client: Client, inner: Inner, caplog: pytest.LogCaptureFixture
    ) -> None:
        def explode(operation: Operation) -> None:
            raise ValueError("upstream said no")

        client.add_resumable_handler("boom", explode)
        client.new_operation("operations/abc", Struct())
        app = ResumeMiddleware(inner, client)
        send = Capture()

        with caplog.at_level(logging.ERROR, logger="alis.build.lro"):
            await app(
                scope("/resume-operation/boom", query="operation=operations/abc"),
                _receive,
                send,
            )

        assert send.status == 500
        assert client.get_operation_pb("operations/abc").done
        assert "ValueError" in client.get_operation_pb("operations/abc").error.message
