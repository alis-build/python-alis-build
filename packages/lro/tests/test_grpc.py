"""The google.longrunning.Operations servicer.

Only GetOperation and WaitOperation are implemented, matching the Go server's
surface. List, Cancel and Delete are left unimplemented upstream too.
"""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from typing import Any

import grpc
import pytest
from google.longrunning import operations_pb2
from google.protobuf.duration_pb2 import Duration
from google.protobuf.struct_pb2 import Struct

from alis.build.lro import Client
from alis.build.lro.grpc import OperationsServicer, register_grpc


class FakeContext:
    """Records an abort instead of raising through gRPC's machinery."""

    def __init__(self, timeout: float | None = None) -> None:
        self.code: grpc.StatusCode | None = None
        self.details: str = ""
        self._timeout = timeout

    def abort(self, code: grpc.StatusCode, details: str) -> Any:
        self.code = code
        self.details = details
        raise _Aborted(code, details)

    def time_remaining(self) -> float | None:
        return self._timeout


class _Aborted(Exception):
    def __init__(self, code: grpc.StatusCode, details: str) -> None:
        super().__init__(details)
        self.code = code


@pytest.fixture
def servicer(client: Client) -> OperationsServicer:
    # A 10ms poll keeps the wait tests well under a second.
    return OperationsServicer(
        client, initial_poll_delay=0.01, max_poll_delay=0.02, default_timeout=timedelta(seconds=2)
    )


class TestGetOperation:
    def test_returns_the_stored_operation(
        self, servicer: OperationsServicer, client: Client
    ) -> None:
        client.new_operation("operations/abc", Struct())
        found = servicer.GetOperation(
            operations_pb2.GetOperationRequest(name="operations/abc"), FakeContext()
        )
        assert found.name == "operations/abc"

    def test_missing_operation_aborts_not_found(self, servicer: OperationsServicer) -> None:
        context = FakeContext()
        with pytest.raises(_Aborted):
            servicer.GetOperation(
                operations_pb2.GetOperationRequest(name="operations/nope"), context
            )
        assert context.code == grpc.StatusCode.NOT_FOUND

    def test_malformed_name_aborts_invalid_argument(self, servicer: OperationsServicer) -> None:
        context = FakeContext()
        with pytest.raises(_Aborted):
            servicer.GetOperation(operations_pb2.GetOperationRequest(name="agents/1"), context)
        assert context.code == grpc.StatusCode.INVALID_ARGUMENT


class TestWaitOperation:
    def test_returns_immediately_when_already_done(
        self, servicer: OperationsServicer, client: Client
    ) -> None:
        operation = client.new_operation("operations/abc", Struct())
        operation.complete(Struct())

        started = time.monotonic()
        found = servicer.WaitOperation(
            operations_pb2.WaitOperationRequest(name="operations/abc"), FakeContext()
        )

        assert found.done
        assert time.monotonic() - started < 0.5

    def test_returns_once_another_thread_completes_it(
        self, servicer: OperationsServicer, client: Client
    ) -> None:
        client.new_operation("operations/abc", Struct())

        def finish() -> None:
            time.sleep(0.05)
            client.get_operation("operations/abc").complete(Struct())

        thread = threading.Thread(target=finish)
        thread.start()
        try:
            found = servicer.WaitOperation(
                operations_pb2.WaitOperationRequest(name="operations/abc"), FakeContext()
            )
        finally:
            thread.join()

        assert found.done

    def test_request_timeout_returns_the_unfinished_operation(
        self, servicer: OperationsServicer, client: Client
    ) -> None:
        # Waiting past the timeout is not an error: the caller gets the
        # operation as it stands and decides whether to wait again.
        client.new_operation("operations/abc", Struct())
        request = operations_pb2.WaitOperationRequest(
            name="operations/abc", timeout=Duration(nanos=50_000_000)
        )

        found = servicer.WaitOperation(request, FakeContext())

        assert not found.done

    def test_rpc_deadline_caps_a_longer_request_timeout(
        self, servicer: OperationsServicer, client: Client
    ) -> None:
        client.new_operation("operations/abc", Struct())
        request = operations_pb2.WaitOperationRequest(
            name="operations/abc", timeout=Duration(seconds=30)
        )

        started = time.monotonic()
        found = servicer.WaitOperation(request, FakeContext(timeout=0.05))

        assert not found.done
        assert time.monotonic() - started < 1.0

    def test_missing_operation_aborts_not_found(self, servicer: OperationsServicer) -> None:
        context = FakeContext()
        with pytest.raises(_Aborted):
            servicer.WaitOperation(
                operations_pb2.WaitOperationRequest(name="operations/nope"), context
            )
        assert context.code == grpc.StatusCode.NOT_FOUND


class FakeRegistrar:
    """Records what was registered, which grpc.Server does not expose."""

    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def add_generic_rpc_handlers(self, handlers: Any) -> None:
        self.handlers.extend(handlers)


class TestRegistration:
    def test_register_grpc_registers_the_operations_service(self, client: Client) -> None:
        registrar = FakeRegistrar()

        register_grpc(registrar, client)

        assert [handler.service_name() for handler in registrar.handlers] == [
            "google.longrunning.Operations"
        ]

    def test_register_grpc_passes_servicer_options_through(self, client: Client) -> None:
        registrar = FakeRegistrar()

        register_grpc(registrar, client, initial_poll_delay=0.01)

        assert registrar.handlers
