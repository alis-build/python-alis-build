"""ASGI middleware that binds a request's trace onto its log lines."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from alis.build import alog
from alis.build.alog.asgi import Message, Scope, TraceContextMiddleware
from alis.build.alog.testing import Captured, capture, fresh_logging_config

TRACED = [(b"x-cloud-trace-context", b"abc123/456;o=1")]


@pytest.fixture(autouse=True)
def fresh() -> Iterator[None]:
    with fresh_logging_config():
        yield


def scope(
    kind: str = "http", path: str = "/v1/users", headers: list[tuple[bytes, bytes]] | None = None
) -> Scope:
    return {"type": kind, "path": path, "headers": headers or []}


async def receive() -> Message:  # pragma: no cover - never called by these handlers
    return {}


async def send(message: Message) -> None:  # pragma: no cover - nothing responds here
    return None


async def handler(scope: Scope, receive: Any, send: Any) -> None:
    """An application that logs, which is the whole thing under test."""
    alog.info("handled")


async def run(app: TraceContextMiddleware, **kwargs: Any) -> Captured:
    with capture() as captured:
        await app(scope(**kwargs), receive, send)
    return captured


async def test_a_request_with_a_trace_correlates_its_lines(project: str) -> None:
    captured = await run(TraceContextMiddleware(handler), headers=TRACED)

    entry = captured.entries[0]
    assert entry["logging.googleapis.com/trace"] == "projects/my-project/traces/abc123"
    assert entry["logging.googleapis.com/spanId"] == "456"


async def test_a_request_without_the_header_is_handled_as_before(project: str) -> None:
    """An absent header is normal, not an error: the lines just carry no correlation."""
    captured = await run(TraceContextMiddleware(handler))

    assert captured.messages == ["handled"]
    assert "logging.googleapis.com/trace" not in captured.entries[0]


async def test_a_malformed_header_never_refuses_the_request(project: str) -> None:
    """An inbound header is not something the service controls."""
    captured = await run(
        TraceContextMiddleware(handler), headers=[(b"x-cloud-trace-context", b"/456;o=1")]
    )

    assert captured.messages == ["handled"]


async def test_the_binding_is_gone_once_the_request_ends(project: str) -> None:
    await run(TraceContextMiddleware(handler), headers=TRACED)
    assert alog.current().trace is None


async def test_labels_can_be_bound_for_every_request() -> None:
    captured = await run(TraceContextMiddleware(handler, labels={"service": "users"}))

    assert captured.entries[0]["logging.googleapis.com/labels"] == {"service": "users"}


async def test_an_excluded_path_is_passed_straight_through(project: str) -> None:
    """Correlating a health check is noise on every line it writes."""
    app = TraceContextMiddleware(handler, exclude_paths={"/healthz"})
    captured = await run(app, path="/healthz", headers=TRACED)

    assert "logging.googleapis.com/trace" not in captured.entries[0]


async def test_a_lifespan_event_is_passed_straight_through() -> None:
    captured = await run(TraceContextMiddleware(handler), kind="lifespan")
    assert captured.messages == ["handled"]


async def test_a_websocket_is_traced_too(project: str) -> None:
    captured = await run(TraceContextMiddleware(handler), kind="websocket", headers=TRACED)
    assert "logging.googleapis.com/trace" in captured.entries[0]
