"""The operation wrapper and the row it persists."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol, TypeVar, cast

from google.longrunning import operations_pb2
from google.protobuf import any_pb2
from google.protobuf.message import Message
from google.rpc import code_pb2, status_pb2

from ._errors import LroError, StateCodecError
from ._state import decode_state, encode_state

__all__ = ["Operation", "OperationRecord"]

M = TypeVar("M", bound=Message)
T2 = TypeVar("T2")


@dataclass(slots=True)
class OperationRecord:
    """One row: the operation itself plus the state needed to resume it.

    Args:
        operation: The client-visible operation message.
        state: Private workflow state, opaque to callers of the Operations API.
        resume_point: A checkpoint label the handler sets to know where it got to.
        update_time: Last write time. Set by the store, not by callers.
    """

    operation: operations_pb2.Operation
    state: bytes = b""
    resume_point: str = ""
    update_time: datetime | None = None


class _Persist(Protocol):
    def update(self, record: OperationRecord) -> None: ...


class _Resumer(Protocol):
    """The part of the client an operation needs to schedule its own resume."""

    def handler_for(self, path: str) -> object: ...
    def is_local(self) -> bool: ...
    def schedule_local(self, path: str, name: str, delay: float) -> None: ...


class Operation:
    """A long-running operation, and the operations you can perform on it.

    Mutators come in pairs. ``set_*`` stages a change in memory; ``save_*``
    stages it and writes the row. A handler that changes three things should set
    twice and save once rather than writing three times.

    Args:
        record: The row this operation wraps.
        store: Where :meth:`save` writes.
    """

    __slots__ = ("_client", "_record", "_store")

    def __init__(
        self,
        *,
        record: OperationRecord,
        store: _Persist,
        client: _Resumer | None = None,
    ) -> None:
        self._record = record
        self._store = store
        self._client = client

    @property
    def pb(self) -> operations_pb2.Operation:
        """The underlying ``google.longrunning.Operation`` message."""
        return self._record.operation

    @property
    def name(self) -> str:
        """The operation resource name."""
        # operations_pb2 ships no py.typed, so its fields arrive as Any.
        return cast(str, self._record.operation.name)

    @property
    def record(self) -> OperationRecord:
        """The row this operation wraps."""
        return self._record

    @property
    def resume_point(self) -> str:
        """The checkpoint label the handler last set."""
        return self._record.resume_point

    def set_metadata(self, metadata: Message) -> None:
        """Stages new client-visible metadata without writing it."""
        packed = any_pb2.Any()
        packed.Pack(metadata)
        self._record.operation.metadata.CopyFrom(packed)

    def save_metadata(self, metadata: Message) -> None:
        """Stages new metadata and writes the row."""
        self.set_metadata(metadata)
        self.save()

    def unmarshal_metadata(self, message_type: type[M]) -> M:
        """Reads the metadata back into a message of the given type.

        Args:
            message_type: The message class the metadata was packed from.

        Returns:
            A populated message.

        Raises:
            LroError: There is no metadata, or it holds a different type.
        """
        message = message_type()
        if not self._record.operation.HasField("metadata"):
            raise LroError(f"operation {self.name} has no metadata")
        if not self._record.operation.metadata.Unpack(message):
            raise LroError(
                f"operation {self.name} metadata is not a {message_type.DESCRIPTOR.full_name}"
            )
        return message

    def set_private_state(self, state: object) -> None:
        """Stages new private workflow state without writing it."""
        self._record.state = encode_state(state)

    def save_private_state(self, state: object) -> None:
        """Stages new private workflow state and writes the row."""
        self.set_private_state(state)
        self.save()

    def decode_private_state(self, into: type[T2] | None = None) -> Any:
        """Reads the private state back.

        Args:
            into: A dataclass type to reconstruct. Omit it for parsed JSON.

        Returns:
            An instance of ``into``, or the parsed JSON.

        Raises:
            StateCodecError: The operation has no state, or the stored state no
                longer fits ``into``.
        """
        if not self._record.state:
            raise StateCodecError(f"operation {self.name} has no private state")
        return decode_state(self._record.state, into)

    def set_resume_point(self, resume_point: str) -> None:
        """Stages a new checkpoint label without writing it."""
        self._record.resume_point = resume_point

    def save_resume_point(self, resume_point: str) -> None:
        """Stages a new checkpoint label and writes the row."""
        self.set_resume_point(resume_point)
        self.save()

    def resume_via_tasks(self, path: str, delay: float | timedelta = 0) -> None:
        """Schedules this operation to be resumed later by a named handler.

        Locally the handler runs on a timer in this process. On Cloud Run it is
        scheduled as a Cloud Task, which arrives as an HTTP callback — possibly
        on a different instance, which is why the handler must have been
        registered at startup.

        Args:
            path: A callback path already registered on the client.
            delay: How long to wait, in seconds or as a timedelta.

        Raises:
            LroError: No handler is registered for that path, or the client has
                no task queue to schedule with.
        """
        if self._client is None:
            raise LroError(f"operation {self.name} is not attached to a client")
        # Validate before scheduling: a task pointing at an unknown path would
        # be dispatched, fail, and retry until the queue gave up.
        self._client.handler_for(path)

        seconds = delay.total_seconds() if isinstance(delay, timedelta) else float(delay)
        if self._client.is_local():
            self._client.schedule_local(path, self.name, seconds)
            return
        raise LroError(
            f"no task queue is configured, so operation {self.name} cannot be resumed "
            f"through Cloud Tasks; set Config.local=True to resume in process"
        )

    def save(self) -> None:
        """Writes the current row."""
        self._store.update(self._record)

    def complete(self, response: Message) -> None:
        """Marks the operation done with a successful response and writes it.

        Args:
            response: The result callers will read off the operation.

        Raises:
            LroError: ``response`` is ``None``. An operation that is done with
                neither a response nor an error tells a caller nothing.
        """
        if response is None:
            raise LroError(f"cannot complete operation {self.name} with no response")
        packed = any_pb2.Any()
        packed.Pack(response)
        self._record.operation.done = True
        self._record.operation.response.CopyFrom(packed)
        self.save()

    def fail(self, message: str, *, code: int = code_pb2.UNKNOWN) -> None:
        """Marks the operation done with an error and writes it.

        The default code is ``UNKNOWN`` rather than the ``0`` the Go package
        leaves in place, because ``0`` is ``OK`` — a failed operation would
        otherwise decode as a successful one.

        Args:
            message: What went wrong, in terms the caller can act on.
            code: A ``google.rpc.Code`` value.
        """
        self._record.operation.done = True
        self._record.operation.error.CopyFrom(status_pb2.Status(code=code, message=message))
        self.save()
