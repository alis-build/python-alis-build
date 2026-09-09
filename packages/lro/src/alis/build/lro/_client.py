"""The client: configuration, the handler registry, and operation lifecycle."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping

from google.longrunning import operations_pb2
from google.protobuf.message import Message

from ._config import Config, config_from_env
from ._errors import LroError
from ._names import validate_operation_name
from ._operation import Operation, OperationRecord
from ._store import OperationStore

__all__ = ["Client", "ResumeHandler"]

ResumeHandler = Callable[[Operation], None]


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

    __slots__ = ("_config", "_handlers", "_lock", "_store")

    def __init__(self, config: Config, *, store: OperationStore) -> None:
        config.validate()
        self._config = config
        self._store = store
        self._handlers: dict[str, ResumeHandler] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, neuron: str, *, store: OperationStore, host: str | None = None) -> Client:
        """Builds a client from the ALIS-managed environment variables.

        Args:
            neuron: The service name. Names the queue and the operations table.
            store: Where operations are persisted.
            host: Overrides the inferred callback host.

        Returns:
            A ready client.

        Raises:
            ConfigError: A required environment variable is unset.
        """
        return cls(config_from_env(neuron, host=host), store=store)

    @property
    def config(self) -> Config:
        """The configuration this client was built with."""
        return self._config

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
            LroError: No handler is registered for that path.
        """
        with self._lock:
            handler = self._handlers.get(path)
        if handler is None:
            raise LroError(f"no resumable handler registered for path {path!r}")
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
        operation = Operation(record=record, store=self._store)
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
        return Operation(record=self._store.read(name), store=self._store)

    def get_operation_pb(self, name: str) -> operations_pb2.Operation:
        """Reads just the operation message, as the Operations API returns it.

        Raises:
            OperationNameError: The name is malformed.
            OperationNotFoundError: No operation with that name.
        """
        return self.get_operation(name).pb

    def close(self) -> None:
        """Releases anything the client holds.

        A no-op while the store is the only backend. The Cloud Tasks queue and
        the Spanner client both need closing, so callers should already be
        writing this call.
        """
        closer = getattr(self._store, "close", None)
        if callable(closer):
            closer()
