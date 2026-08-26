"""Ambient binding of per-request metadata."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack

from alis.build import alog
from alis.build.alog import HttpRequest, LogOperation


def test_nothing_is_bound_by_default() -> None:
    assert alog.current() == alog.BoundContext()


def test_a_binding_is_gone_after_the_block() -> None:
    with alog.bind(insert_id="a"):
        assert alog.current().insert_id == "a"

    assert alog.current().insert_id == ""


def test_a_binding_is_released_even_when_the_block_raises() -> None:
    try:
        with alog.bind(insert_id="a"):
            raise RuntimeError
    except RuntimeError:
        pass

    assert alog.current().insert_id == ""


def test_labels_and_fields_merge_when_nested() -> None:
    """An outer block binds what holds for the service, an inner one adds a route's."""
    with alog.bind(labels={"service": "users"}, region="eu"):
        with alog.bind(labels={"route": "/v1"}, request_id="r-1"):
            context = alog.current()
            assert dict(context.labels) == {"service": "users", "route": "/v1"}
            assert dict(context.fields) == {"region": "eu", "request_id": "r-1"}

        assert dict(alog.current().labels) == {"service": "users"}


def test_the_inner_value_wins_on_a_collision() -> None:
    with alog.bind(labels={"route": "outer"}, who="outer"):
        with alog.bind(labels={"route": "inner"}, who="inner"):
            assert alog.current().labels["route"] == "inner"
            assert alog.current().fields["who"] == "inner"

        assert alog.current().labels["route"] == "outer"
        assert alog.current().fields["who"] == "outer"


def test_the_singular_bindings_replace_rather_than_merge() -> None:
    with alog.bind(operation=LogOperation(id="a"), insert_id="a"):
        with alog.bind(operation=LogOperation(id="b"), insert_id="b"):
            assert alog.current().operation == LogOperation(id="b")
            assert alog.current().insert_id == "b"

        assert alog.current().operation == LogOperation(id="a")


def test_a_header_naming_no_trace_leaves_the_outer_one_alone() -> None:
    """A malformed inbound header should not blank out correlation that already works."""
    with alog.bind(trace_context="abc/1;o=1"):
        with alog.bind(trace_context=""):
            inner = alog.current().trace
            assert inner is not None
            assert inner.trace_id == "abc"

        outer = alog.current().trace
        assert outer is not None
        assert outer.trace_id == "abc"


def test_an_http_request_binds_whole() -> None:
    request = HttpRequest(request_method="GET", status=200)
    with alog.bind(http_request=request):
        assert alog.current().http_request == request


async def test_a_task_inherits_the_binding_but_does_not_leak_back() -> None:
    """Task-local by construction, which is what makes this safe on a shared server."""
    seen: list[str] = []

    async def child() -> None:
        seen.append(alog.current().insert_id)
        with alog.bind(insert_id="child"):
            seen.append(alog.current().insert_id)

    with alog.bind(insert_id="parent"):
        async with asyncio.TaskGroup() as group:
            group.create_task(child())
        assert alog.current().insert_id == "parent"

    assert seen == ["parent", "child"]


def test_an_exit_stack_drives_it_across_separate_callbacks() -> None:
    """The shape a framework with separate enter and exit hooks needs."""
    stack = ExitStack()
    stack.enter_context(alog.bind(insert_id="a"))
    assert alog.current().insert_id == "a"

    stack.close()
    assert alog.current().insert_id == ""
