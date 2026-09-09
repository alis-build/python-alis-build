"""Test helpers: a store that keeps operations in memory.

This is what makes the whole workflow — create, save state, resume, complete —
runnable with no Spanner, no Cloud Tasks and no credentials.
"""

from __future__ import annotations

import copy
import threading
from datetime import UTC, datetime

from ._errors import LroError, OperationNotFoundError
from ._operation import OperationRecord

__all__ = ["InMemoryStore"]


class InMemoryStore:
    """An :class:`~alis.build.lro.OperationStore` backed by a dict.

    Safe to share across threads, which matters because a local resume runs its
    handler on a timer thread while the test that scheduled it is still going.
    """

    __slots__ = ("_lock", "_rows")

    def __init__(self) -> None:
        self._rows: dict[str, OperationRecord] = {}
        self._lock = threading.Lock()

    def insert(self, record: OperationRecord) -> None:
        """Writes a new row, stamping ``update_time``.

        Args:
            record: The row to write. Copied, so later mutation of the caller's
                object does not reach into storage.

        Raises:
            LroError: A row with that name already exists.
        """
        name = record.operation.name
        with self._lock:
            if name in self._rows:
                raise LroError(f"operation {name} already exists")
            self._rows[name] = self._stamped(record)

    def read(self, name: str) -> OperationRecord:
        """Returns a copy of the stored row.

        Raises:
            OperationNotFoundError: No row with that name.
        """
        with self._lock:
            stored = self._rows.get(name)
            if stored is None:
                raise OperationNotFoundError(f"operation {name} not found")
            return copy.deepcopy(stored)

    def update(self, record: OperationRecord) -> None:
        """Overwrites an existing row, stamping ``update_time``.

        Raises:
            OperationNotFoundError: No row with that name.
        """
        name = record.operation.name
        with self._lock:
            if name not in self._rows:
                raise OperationNotFoundError(f"operation {name} not found")
            self._rows[name] = self._stamped(record)

    @staticmethod
    def _stamped(record: OperationRecord) -> OperationRecord:
        """Returns a detached copy carrying a fresh, timezone-aware timestamp."""
        stored = copy.deepcopy(record)
        stored.update_time = datetime.now(UTC)
        return stored
