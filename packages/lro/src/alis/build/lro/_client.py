"""The client: configuration, the handler registry, and operation lifecycle."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Protocol

from google.longrunning import operations_pb2
from google.protobuf.message import Message

from ._config import Config, config_from_env
from ._errors import ConfigError, HandlerError, HandlerNotFoundError, LroError
from ._names import validate_operation_name
from ._operation import Operation, OperationRecord
from ._store import TASK_DEADLINE, OperationStore

__all__ = ["Client", "ResumeHandler"]

_log = logging.getLogger(__name__.rsplit(".", 1)[0])

ResumeHandler = Callable[[Operation], None]


class TaskQueue(Protocol):
    """The part of a Cloud Tasks queue a client needs."""

    def schedule(self, url: str, *, when: datetime) -> None: ...
    def close(self) -> None: ...


class Client:
    """Creates, reads and resumes long-running operations for one neuron.

    Build one at process startup and share it. Resumable handlers must be
    registered at startup too, not when the RPC that schedules them runs: a
    callback may land on an instance that never executed the scheduling request,
    and an instance that does not recognise the path cannot resume anything.

    Args:
        config: Where this client's operations live and how callbacks reach it.
        store: Where operations are persisted.

    Raises:
        ConfigError: The config is missing a required value.
    """

    __slots__ = ("_config", "_handlers", "_lock", "_queue", "_store", "_timers")

    def __init__(
        self,
        config: Config,
        *,
        store: OperationStore | None = None,
        queue: TaskQueue | None = None,
    ) -> None:
        config.validate()
        self._config = config
        self._store, self._queue = _backends(config, store, queue)
        self._handlers: dict[str, ResumeHandler] = {}
        self._lock = threading.Lock()
        # Timers are held so a pending local resume is not garbage collected,
        # and so close() can cancel work that has not fired yet.
        self._timers: set[threading.Timer] = set()

    @property
    def queue(self) -> TaskQueue | None:
        """The task queue resumes are scheduled on, when there is one."""
        return self._queue

    @classmethod
    def from_env(
        cls,
        neuron: str,
        *,
        store: OperationStore | None = None,
        queue: TaskQueue | None = None,
        host: str | None = None,
    ) -> Client:
        """Builds a client from the ALIS-managed environment variables.

        Args:
            neuron: The service name. Names the queue and the operations table.
            store: Where operations are persisted. Defaults to Spanner.
            queue: Where resumes are scheduled. Defaults to Cloud Tasks.
            host: Overrides the inferred callback host.

        Returns:
            A ready client.

        Raises:
            ConfigError: A required environment variable is unset.
        """
        return cls(config_from_env(neuron, host=host), store=store, queue=queue)

    @property
    def config(self) -> Config:
        """The configuration this client was built with."""
        return self._config

    def set_callback_prefix(self, prefix: str) -> None:
        """Moves where resumable callbacks are served.

        The client builds callback URLs from this and the ASGI middleware
        matches incoming paths against it, so it lives in one place rather than
        being passed to each of them separately.
        """
        self._config = replace(self._config, callback_prefix=prefix)

    @property
    def resumable_paths(self) -> frozenset[str]:
        """The callback paths that currently have a handler."""
        with self._lock:
            return frozenset(self._handlers)

    def add_resumable_handler(self, path: str, handler: ResumeHandler) -> None:
        """Registers the handler that resumes operations for one callback path.

        Args:
            path: The path segment under the callback prefix, for example
                ``create-agent``.
            handler: Called with the operation when the callback arrives.

        Raises:
            LroError: The path is empty, the handler is missing, or the path is
                already registered.
        """
        if not path:
            raise LroError("handler path is required")
        if handler is None:
            raise LroError(f"handler is required for path {path!r}")
        with self._lock:
            if path in self._handlers:
                raise LroError(f"a resumable handler is already registered for path {path!r}")
            self._handlers[path] = handler

    def add_resumable_handlers(self, handlers: Mapping[str, ResumeHandler]) -> None:
        """Registers several handlers.

        Takes a mapping rather than keyword arguments because callback paths are
        hyphenated, and ``create-agent`` is not a Python identifier.

        Args:
            handlers: Callback path to handler.

        Raises:
            LroError: Any path is empty, already registered, or missing a
                handler. The message names the offending path.
        """
        for path, handler in handlers.items():
            self.add_resumable_handler(path, handler)

    def handler_for(self, path: str) -> ResumeHandler:
        """Returns the handler registered for a path.

        Raises:
            HandlerNotFoundError: No handler is registered for that path.
        """
        with self._lock:
            handler = self._handlers.get(path)
        if handler is None:
            raise HandlerNotFoundError(f"no resumable handler registered for path {path!r}")
        return handler

    def new_operation(self, name: str, metadata: Message) -> Operation:
        """Creates an operation and writes its first row.

        Args:
            name: The resource name, shaped ``operations/{id}``.
            metadata: Client-visible metadata, packed into the operation.

        Returns:
            The new operation.

        Raises:
            OperationNameError: The name is malformed. Nothing is written.
            LroError: An operation with that name already exists.
        """
        validate_operation_name(name)
        record = OperationRecord(operation=operations_pb2.Operation(name=name))
        operation = Operation(record=record, store=self._store, client=self)
        operation.set_metadata(metadata)
        self._store.insert(record)
        return operation

    def get_operation(self, name: str) -> Operation:
        """Reads an operation by name.

        Raises:
            OperationNameError: The name is malformed.
            OperationNotFoundError: No operation with that name.
        """
        validate_operation_name(name)
        return Operation(record=self._store.read(name), store=self._store, client=self)

    def get_operation_pb(self, name: str) -> operations_pb2.Operation:
        """Reads just the operation message, as the Operations API returns it.

        Raises:
            OperationNameError: The name is malformed.
            OperationNotFoundError: No operation with that name.
        """
        return self.get_operation(name).pb

    def is_local(self) -> bool:
        """Whether resumes run in this process rather than through Cloud Tasks.

        ``Config.local`` decides when it is set. Left at ``None`` it is inferred
        per call from ``K_SERVICE``, which Cloud Run sets and a laptop does not.
        """
        if self._config.local is not None:
            return self._config.local
        return not os.environ.get("K_SERVICE")

    def schedule_local(self, path: str, name: str, delay: float) -> None:
        """Runs a resumable handler in this process after ``delay`` seconds.

        Bounded by :data:`~alis.build.lro.TASK_DEADLINE`, so a handler is not
        given more time locally than Cloud Tasks would allow it in production.
        """
        timer = threading.Timer(
            min(delay, TASK_DEADLINE.total_seconds()),
            self._run_local,
            args=(path, name),
        )
        timer.daemon = True
        with self._lock:
            self._timers.add(timer)
        timer.start()

    def _run_local(self, path: str, name: str) -> None:
        """Timer entry point. Never lets an exception escape onto the thread."""
        try:
            self.resume_now(path, name)
        except Exception:
            _log.exception("local resume of %s via %r failed", name, path)

    def resume_now(self, path: str, name: str, /) -> None:
        """Runs a resumable handler immediately, in the calling thread.

        The record is re-read from the store first. A callback may land on an
        instance that never ran the request that scheduled it, so whatever that
        request was holding is not what the handler should see.

        Args:
            path: The registered callback path.
            name: The operation to resume.

        Raises:
            HandlerNotFoundError: No handler is registered for that path.
            HandlerError: The handler raised. Its operation has been marked
                failed before this propagates.
            OperationNotFoundError: No operation with that name.
        """
        handler = self.handler_for(path)
        operation = self.get_operation(name)
        try:
            handler(operation)
        except Exception as exc:
            _log.exception("resumable handler %r failed for %s", path, name)
            self._fail_unless_already_done(name, exc)
            raise HandlerError(f"resumable handler {path!r} failed for {name}") from exc

    def _fail_unless_already_done(
        self, name: str, exc: BaseException, *, reason: str | None = None
    ) -> None:
        """Records the exception on the operation, unless it already finished.

        A handler that failed the operation itself gave a better reason than the
        exception that followed, so its message wins.
        """
        try:
            operation = self.get_operation(name)
            if operation.pb.done:
                return
            operation.fail(reason or f"{type(exc).__name__}: {exc}")
        except Exception:
            # The operation is unreachable. The log above is all we can offer.
            _log.exception("could not record the failure of %s", name)

    def schedule_task(self, path: str, name: str, delay: float) -> None:
        """Schedules a resume through Cloud Tasks.

        Raises:
            LroError: No queue is configured, or scheduling failed. A failure
                marks the operation failed before it propagates, because an
                operation whose callback was never scheduled would otherwise
                hang until its TTL.
        """
        if self._queue is None:
            raise LroError(
                f"no task queue is configured, so operation {name} cannot be resumed "
                f"through Cloud Tasks; set Config.local=True to resume in process"
            )
        url = f"{self._config.host}{self._config.callback_prefix}{path}?operation={name}"
        when = datetime.now(UTC) + timedelta(seconds=delay)
        try:
            self._queue.schedule(url, when=when)
        except Exception as exc:
            _log.exception("scheduling a resume of %s via %r failed", name, path)
            self._fail_unless_already_done(name, exc, reason=f"scheduling cloudtask: {exc}")
            raise LroError(f"scheduling a resume of {name} via {path!r} failed") from exc

    def close(self) -> None:
        """Releases both backends and cancels pending local resumes."""
        with self._lock:
            timers, self._timers = self._timers, set()
        for timer in timers:
            timer.cancel()
        for backend in (self._store, self._queue):
            closer = getattr(backend, "close", None)
            if callable(closer):
                closer()


def _backends(
    config: Config, store: OperationStore | None, queue: TaskQueue | None
) -> tuple[OperationStore, TaskQueue | None]:
    """Returns the backends to use, connecting only when none were supplied.

    Defaulting is all or nothing, on purpose. Supplying any backend means you
    are saying which ones you want, so a test that passes an in-memory store
    does not then try to open a Cloud Tasks connection and fail for want of
    credentials. Supply both to run against both.

    The extras are imported lazily and only when defaulting, so a client handed
    its backends works with neither extra installed.
    """
    if store is not None or queue is not None:
        if store is None:
            raise ConfigError(
                "a client given a queue must also be given a store; backend defaults "
                "are all or nothing, so pass store= as well"
            )
        return store, queue

    from .spanner import SpannerStore

    connected = SpannerStore.from_config(config)
    try:
        from .tasks import CloudTasksQueue

        return connected, CloudTasksQueue.from_config(config)
    except Exception:
        # Match Go's New(): close the database before giving up, or its session
        # pool leaks for the life of the process.
        connected.close()
        raise
