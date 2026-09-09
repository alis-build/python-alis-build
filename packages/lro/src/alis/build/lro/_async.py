"""The async surface.

Spanner's Python client has no async layer, so every store call here is a thread
offload. That is not a disguise: the value is that an ASGI service does not stall
its event loop on a blocking read, not that the read stopped being blocking.

Cloud Tasks does ship a genuine async client, so when ``lrocloud`` lands the
scheduling half of this surface is natively async while the storage half stays
on :func:`asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
from typing import Any, TypeVar

from google.longrunning import operations_pb2
from google.protobuf.message import Message

from ._client import Client
from ._config import Config, config_from_env
from ._errors import HandlerError, HandlerNotFoundError, LroError
from ._operation import Operation, OperationRecord
from ._store import TASK_DEADLINE, OperationStore

__all__ = ["AsyncClient", "AsyncOperation", "AsyncResumeHandler"]

_log = logging.getLogger(__name__.rsplit(".", 1)[0])

M = TypeVar("M", bound=Message)
T = TypeVar("T")

AsyncResumeHandler = Callable[["AsyncOperation"], Awaitable[None]]


class AsyncOperation:
    """The async mirror of :class:`~alis.build.lro.Operation`.

    Reads are cheap and stay synchronous; anything that writes is awaitable,
    because writing means a blocking store call on a worker thread.
    """

    __slots__ = ("_client", "_sync")

    def __init__(self, *, sync: Operation, client: AsyncClient) -> None:
        self._sync = sync
        self._client = client

    @property
    def pb(self) -> operations_pb2.Operation:
        """The underlying ``google.longrunning.Operation`` message."""
        return self._sync.pb

    @property
    def name(self) -> str:
        """The operation resource name."""
        return self._sync.name

    @property
    def record(self) -> OperationRecord:
        """The row this operation wraps."""
        return self._sync.record

    @property
    def resume_point(self) -> str:
        """The checkpoint label the handler last set."""
        return self._sync.resume_point

    def set_metadata(self, metadata: Message) -> None:
        """Stages new client-visible metadata without writing it."""
        self._sync.set_metadata(metadata)

    def unmarshal_metadata(self, message_type: type[M]) -> M:
        """Reads the metadata back into a message of the given type."""
        return self._sync.unmarshal_metadata(message_type)

    def set_private_state(self, state: object) -> None:
        """Stages new private workflow state without writing it."""
        self._sync.set_private_state(state)

    def set_resume_point(self, resume_point: str) -> None:
        """Stages a new checkpoint label without writing it."""
        self._sync.set_resume_point(resume_point)

    async def decode_private_state(self, into: type[T] | None = None) -> Any:
        """Reads the private state back."""
        return self._sync.decode_private_state(into)

    async def save_metadata(self, metadata: Message) -> None:
        """Stages new metadata and writes the row."""
        await asyncio.to_thread(self._sync.save_metadata, metadata)

    async def save_private_state(self, state: object) -> None:
        """Stages new private workflow state and writes the row."""
        await asyncio.to_thread(self._sync.save_private_state, state)

    async def save_resume_point(self, resume_point: str) -> None:
        """Stages a new checkpoint label and writes the row."""
        await asyncio.to_thread(self._sync.save_resume_point, resume_point)

    async def save(self) -> None:
        """Writes the current row."""
        await asyncio.to_thread(self._sync.save)

    async def complete(self, response: Message) -> None:
        """Marks the operation done with a successful response and writes it."""
        await asyncio.to_thread(self._sync.complete, response)

    async def fail(self, message: str, *, code: int | None = None) -> None:
        """Marks the operation done with an error and writes it."""
        if code is None:
            await asyncio.to_thread(self._sync.fail, message)
        else:
            await asyncio.to_thread(lambda: self._sync.fail(message, code=code))

    async def resume_via_tasks(self, path: str, delay: float | timedelta = 0) -> None:
        """Schedules this operation to be resumed later by a named handler.

        Raises:
            LroError: No handler is registered for that path, or the client has
                no task queue to schedule with.
        """
        self._client.handler_for(path)
        seconds = delay.total_seconds() if isinstance(delay, timedelta) else float(delay)
        if self._client.is_local():
            self._client.schedule_local(path, self.name, seconds)
            return
        raise LroError(
            f"no task queue is configured, so operation {self.name} cannot be resumed "
            f"through Cloud Tasks; set Config.local=True to resume in process"
        )


class AsyncClient:
    """The async mirror of :class:`~alis.build.lro.Client`.

    Args:
        config: Where this client's operations live and how callbacks reach it.
        store: Where operations are persisted.
    """

    __slots__ = ("_handlers", "_lock", "_sync", "_tasks")

    def __init__(self, config: Config, *, store: OperationStore) -> None:
        self._sync = Client(config, store=store)
        self._handlers: dict[str, AsyncResumeHandler] = {}
        self._lock = threading.Lock()
        # Held so a scheduled resume is not garbage collected mid-flight.
        self._tasks: set[asyncio.Task[None]] = set()

    @classmethod
    def from_env(
        cls, neuron: str, *, store: OperationStore, host: str | None = None
    ) -> AsyncClient:
        """Builds a client from the ALIS-managed environment variables.

        Args:
            neuron: The service name. Names the queue and the operations table.
            store: Where operations are persisted.
            host: Overrides the inferred callback host.

        Raises:
            ConfigError: A required environment variable is unset.
        """
        return cls(config_from_env(neuron, host=host), store=store)

    @property
    def config(self) -> Config:
        """The configuration this client was built with."""
        return self._sync.config

    @property
    def resumable_paths(self) -> frozenset[str]:
        """The callback paths that currently have a handler."""
        with self._lock:
            return frozenset(self._handlers)

    def set_callback_prefix(self, prefix: str) -> None:
        """Moves where resumable callbacks are served."""
        self._sync.set_callback_prefix(prefix)

    def add_resumable_handler(self, path: str, handler: AsyncResumeHandler) -> None:
        """Registers the handler that resumes operations for one callback path."""
        if not path:
            raise LroError("handler path is required")
        if handler is None:
            raise LroError(f"handler is required for path {path!r}")
        with self._lock:
            if path in self._handlers:
                raise LroError(f"a resumable handler is already registered for path {path!r}")
            self._handlers[path] = handler

    def add_resumable_handlers(self, handlers: Mapping[str, AsyncResumeHandler]) -> None:
        """Registers several handlers."""
        for path, handler in handlers.items():
            self.add_resumable_handler(path, handler)

    def handler_for(self, path: str) -> AsyncResumeHandler:
        """Returns the handler registered for a path."""
        with self._lock:
            handler = self._handlers.get(path)
        if handler is None:
            raise HandlerNotFoundError(f"no resumable handler registered for path {path!r}")
        return handler

    def is_local(self) -> bool:
        """Whether resumes run in this process rather than through Cloud Tasks."""
        return self._sync.is_local()

    def schedule_local(self, path: str, name: str, delay: float) -> None:
        """Runs a resumable handler on the event loop after ``delay`` seconds."""
        task = asyncio.get_running_loop().create_task(self._run_local(path, name, delay))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_local(self, path: str, name: str, delay: float) -> None:
        """Task entry point. Never lets an exception escape onto the loop."""
        await asyncio.sleep(min(delay, TASK_DEADLINE.total_seconds()))
        try:
            await self.resume_now(path, name)
        except Exception:
            _log.exception("local resume of %s via %r failed", name, path)

    async def new_operation(self, name: str, metadata: Message) -> AsyncOperation:
        """Creates an operation and writes its first row."""
        sync = await asyncio.to_thread(self._sync.new_operation, name, metadata)
        return AsyncOperation(sync=sync, client=self)

    async def get_operation(self, name: str) -> AsyncOperation:
        """Reads an operation by name."""
        sync = await asyncio.to_thread(self._sync.get_operation, name)
        return AsyncOperation(sync=sync, client=self)

    async def get_operation_pb(self, name: str) -> operations_pb2.Operation:
        """Reads just the operation message, as the Operations API returns it."""
        return await asyncio.to_thread(self._sync.get_operation_pb, name)

    async def resume_now(self, path: str, name: str, /) -> None:
        """Awaits a resumable handler immediately.

        The record is re-read from the store first, for the same reason the sync
        client does it: the callback may reach an instance that never ran the
        request that scheduled it.

        Raises:
            HandlerNotFoundError: No handler is registered for that path.
            HandlerError: The handler raised. Its operation has been marked
                failed before this propagates.
        """
        handler = self.handler_for(path)
        operation = await self.get_operation(name)
        try:
            await handler(operation)
        except Exception as exc:
            _log.exception("resumable handler %r failed for %s", path, name)
            await self._fail_unless_already_done(name, exc)
            raise HandlerError(f"resumable handler {path!r} failed for {name}") from exc

    async def _fail_unless_already_done(self, name: str, exc: BaseException) -> None:
        """Records the exception on the operation, unless it already finished."""
        try:
            operation = await self.get_operation(name)
            if operation.pb.done:
                return
            await operation.fail(f"{type(exc).__name__}: {exc}")
        except Exception:
            _log.exception("could not record the failure of %s", name)

    async def close(self) -> None:
        """Releases anything the client holds and cancels pending local resumes."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.to_thread(self._sync.close)
