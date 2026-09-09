"""A google.longrunning.Operations server backed by an LRO client.

Only ``GetOperation`` and ``WaitOperation`` are implemented, matching the Go
server. ``ListOperations``, ``CancelOperation`` and ``DeleteOperation`` are left
unimplemented there too, and inventing them here would put a surface in front of
callers that the platform does not actually support.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any, Final

import grpc
from google.longrunning import operations_pb2, operations_pb2_grpc

from .._client import Client
from .._errors import OperationNameError, OperationNotFoundError

__all__ = ["OperationsServicer", "register_grpc"]

_DEFAULT_TIMEOUT: Final = timedelta(minutes=10)
_DEFAULT_INITIAL_POLL: Final = 1.0
_DEFAULT_MAX_POLL: Final = 10.0


# The generated base is Any to mypy, since googleapis-common-protos ships no
# py.typed. Subclassing it is still required: add_OperationsServicer_to_server
# reads ListOperations, CancelOperation and DeleteOperation off the servicer,
# and the base supplies their UNIMPLEMENTED defaults.
class OperationsServicer(operations_pb2_grpc.OperationsServicer):  # type: ignore[misc]
    """Serves the standard Operations RPCs from an LRO client's store.

    Args:
        client: The client whose operations are served.
        default_timeout: How long ``WaitOperation`` waits when the request does
            not say.
        initial_poll_delay: First gap between reads, in seconds.
        max_poll_delay: Ceiling the gap doubles up to, in seconds.
    """

    def __init__(
        self,
        client: Client,
        *,
        default_timeout: timedelta = _DEFAULT_TIMEOUT,
        initial_poll_delay: float = _DEFAULT_INITIAL_POLL,
        max_poll_delay: float = _DEFAULT_MAX_POLL,
    ) -> None:
        self._client = client
        self._default_timeout = default_timeout.total_seconds()
        self._initial_poll_delay = initial_poll_delay or _DEFAULT_INITIAL_POLL
        self._max_poll_delay = max_poll_delay or _DEFAULT_MAX_POLL

    def GetOperation(  # noqa: N802 - the generated servicer names it
        self, request: operations_pb2.GetOperationRequest, context: Any
    ) -> operations_pb2.Operation:
        """Returns the latest state of one operation."""
        return self._read(request.name, context)

    def WaitOperation(  # noqa: N802 - the generated servicer names it
        self, request: operations_pb2.WaitOperationRequest, context: Any
    ) -> operations_pb2.Operation:
        """Waits until the operation is done or the effective timeout elapses.

        Running out of time is not an error: the caller gets the operation as it
        stands and decides whether to wait again.
        """
        timeout = self._default_timeout
        if request.HasField("timeout"):
            timeout = request.timeout.ToTimedelta().total_seconds()
        remaining = _rpc_time_remaining(context)
        if remaining is not None:
            timeout = min(timeout, remaining)

        deadline = time.monotonic() + timeout
        delay = self._initial_poll_delay
        operation = self._read(request.name, context)
        while not operation.done:
            now = time.monotonic()
            if now >= deadline:
                break
            time.sleep(min(delay, deadline - now))
            operation = self._read(request.name, context)
            delay = min(delay * 2, self._max_poll_delay)
        return operation

    def _read(self, name: str, context: Any) -> operations_pb2.Operation:
        """Reads an operation, mapping this package's errors onto status codes."""
        try:
            return self._client.get_operation_pb(name)
        except OperationNameError as exc:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
            raise
        except OperationNotFoundError as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, str(exc))
            raise


def _rpc_time_remaining(context: Any) -> float | None:
    """Returns the RPC's remaining time, when the context reports one."""
    getter = getattr(context, "time_remaining", None)
    if getter is None:
        return None
    remaining = getter()
    return None if remaining is None else float(remaining)


def register_grpc(registrar: Any, client: Client, **options: Any) -> None:
    """Wires the Operations service onto a gRPC server.

    Args:
        registrar: A ``grpc.Server``, or anything else with
            ``add_generic_rpc_handlers``.
        client: The client whose operations are served.
        **options: Passed through to :class:`OperationsServicer`.
    """
    operations_pb2_grpc.add_OperationsServicer_to_server(
        OperationsServicer(client, **options), registrar
    )
