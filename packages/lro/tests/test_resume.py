"""Local resume, the local tri-state, and what happens when a handler raises.

Every wait here polls with a ceiling rather than sleeping a fixed time, so the
suite stays fast and cannot flake into a false pass on a loaded machine.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import pytest
from google.protobuf.struct_pb2 import Struct

from alis.build.lro import Client, Config, LroError, Operation
from alis.build.lro.testing import InMemoryStore


def wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    """Polls until the predicate holds or the ceiling is reached."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


@pytest.fixture(autouse=True)
def _not_on_cloud_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every test to the local branch unless it says otherwise."""
    monkeypatch.delenv("K_SERVICE", raising=False)


class TestLocalResume:
    def test_invokes_the_handler(self, client: Client) -> None:
        seen: list[str] = []
        client.add_resumable_handler("create-agent", lambda op: seen.append(op.name))
        operation = client.new_operation("operations/abc", Struct())

        operation.resume_via_tasks("create-agent", 0)

        assert wait_for(lambda: seen == ["operations/abc"])

    def test_rereads_the_record_from_the_store(self, client: Client) -> None:
        # The callback may land on an instance that never ran the scheduling
        # request, so the handler must see stored state, not an in-hand copy.
        seen: list[str] = []
        client.add_resumable_handler("create-agent", lambda op: seen.append(op.resume_point))
        operation = client.new_operation("operations/abc", Struct())
        operation.save_resume_point("written-after-creation")

        operation.resume_via_tasks("create-agent", 0)

        assert wait_for(lambda: seen == ["written-after-creation"])

    def test_handler_can_complete_the_operation(self, client: Client) -> None:
        client.add_resumable_handler("create-agent", lambda op: op.complete(Struct()))
        operation = client.new_operation("operations/abc", Struct())

        operation.resume_via_tasks("create-agent", 0)

        assert wait_for(lambda: client.get_operation_pb("operations/abc").done)

    def test_delay_defers_the_handler(self, client: Client) -> None:
        seen: list[str] = []
        client.add_resumable_handler("create-agent", lambda op: seen.append(op.name))
        operation = client.new_operation("operations/abc", Struct())

        operation.resume_via_tasks("create-agent", 0.2)

        assert seen == []
        assert wait_for(lambda: seen == ["operations/abc"])

    def test_unregistered_path_raises_before_scheduling(self, client: Client) -> None:
        operation = client.new_operation("operations/abc", Struct())
        with pytest.raises(LroError, match="create-agent"):
            operation.resume_via_tasks("create-agent", 0)

    def test_empty_path_raises(self, client: Client) -> None:
        operation = client.new_operation("operations/abc", Struct())
        with pytest.raises(LroError):
            operation.resume_via_tasks("", 0)


class TestLocalTriState:
    def test_none_infers_local_when_k_service_is_unset(self, client: Client) -> None:
        seen: list[str] = []
        client.add_resumable_handler("create-agent", lambda op: seen.append(op.name))
        operation = client.new_operation("operations/abc", Struct())

        operation.resume_via_tasks("create-agent", 0)

        assert wait_for(lambda: seen == ["operations/abc"])

    def test_none_infers_cloud_when_k_service_is_set(
        self, client: Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("K_SERVICE", "launchpad-v1")
        client.add_resumable_handler("create-agent", lambda op: None)
        operation = client.new_operation("operations/abc", Struct())

        with pytest.raises(LroError, match="no task queue"):
            operation.resume_via_tasks("create-agent", 0)

    def test_true_wins_over_k_service(
        self,
        store: InMemoryStore,
        make_config: Callable[..., Config],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("K_SERVICE", "launchpad-v1")
        client = Client(make_config(local=True), store=store)
        seen: list[str] = []
        client.add_resumable_handler("create-agent", lambda op: seen.append(op.name))
        operation = client.new_operation("operations/abc", Struct())

        operation.resume_via_tasks("create-agent", 0)

        assert wait_for(lambda: seen == ["operations/abc"])

    def test_false_wins_over_k_service(
        self, store: InMemoryStore, make_config: Callable[..., Config]
    ) -> None:
        client = Client(make_config(local=False), store=store)
        client.add_resumable_handler("create-agent", lambda op: None)
        operation = client.new_operation("operations/abc", Struct())

        with pytest.raises(LroError, match="no task queue"):
            operation.resume_via_tasks("create-agent", 0)


class TestHandlerFailure:
    def test_raising_handler_fails_the_operation(
        self, client: Client, caplog: pytest.LogCaptureFixture
    ) -> None:
        def explode(operation: Operation) -> None:
            raise ValueError("upstream said no")

        client.add_resumable_handler("boom", explode)
        operation = client.new_operation("operations/abc", Struct())

        with caplog.at_level(logging.ERROR, logger="alis.build.lro"):
            operation.resume_via_tasks("boom", 0)
            assert wait_for(lambda: client.get_operation_pb("operations/abc").done)

        failed = client.get_operation_pb("operations/abc")
        assert "ValueError" in failed.error.message
        assert "upstream said no" in failed.error.message

    def test_the_traceback_is_logged(
        self, client: Client, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The operation records that it failed; the log records why.
        def explode(operation: Operation) -> None:
            raise ValueError("upstream said no")

        client.add_resumable_handler("boom", explode)
        operation = client.new_operation("operations/abc", Struct())

        with caplog.at_level(logging.ERROR, logger="alis.build.lro"):
            operation.resume_via_tasks("boom", 0)
            assert wait_for(lambda: client.get_operation_pb("operations/abc").done)

        assert any(record.exc_info for record in caplog.records)

    def test_a_handler_that_already_failed_the_operation_is_not_overwritten(
        self, client: Client
    ) -> None:
        def fail_then_raise(operation: Operation) -> None:
            operation.fail("known reason")
            raise ValueError("secondary")

        client.add_resumable_handler("boom", fail_then_raise)
        operation = client.new_operation("operations/abc", Struct())

        operation.resume_via_tasks("boom", 0)

        assert wait_for(lambda: client.get_operation_pb("operations/abc").done)
        assert client.get_operation_pb("operations/abc").error.message == "known reason"


class TestInvokeDirectly:
    def test_resume_now_runs_the_handler_in_the_calling_thread(self, client: Client) -> None:
        # The ASGI and gRPC layers need a synchronous entry point; only
        # resume_via_tasks defers.
        seen: list[str] = []
        client.add_resumable_handler("create-agent", lambda op: seen.append(op.name))
        client.new_operation("operations/abc", Struct())

        client.resume_now("create-agent", "operations/abc")

        assert seen == ["operations/abc"]

    def test_resume_now_unknown_path_raises(self, client: Client) -> None:
        client.new_operation("operations/abc", Struct())
        with pytest.raises(LroError, match="create-agent"):
            client.resume_now("create-agent", "operations/abc")
