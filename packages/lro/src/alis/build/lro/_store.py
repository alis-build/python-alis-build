"""Where operations are persisted.

Storage is a protocol rather than a class so the workflow can run in tests with
no cloud, and so a Spanner implementation can be dropped in without anything
above it changing. :class:`alis.build.lro.testing.InMemoryStore` is the
reference implementation and the one the package's own tests use.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final, Protocol

from ._operation import OperationRecord

__all__ = ["TASK_DEADLINE", "OperationStore"]

# How long a resumable callback may run. Cloud Tasks enforces this as the task's
# dispatch deadline; a local resume is bounded by the same value so the two
# paths cannot diverge in how long a handler is allowed to take.
TASK_DEADLINE: Final = timedelta(minutes=30)


class OperationStore(Protocol):
    """The three operations a backing store must provide.

    Implementations must set ``update_time`` on every write, and must not hand
    out references into their own storage: a caller that mutates a record it
    read should not thereby rewrite the row.
    """

    def insert(self, record: OperationRecord) -> None:
        """Writes a new row.

        Raises:
            LroError: A row with that name already exists.
        """
        ...

    def read(self, name: str) -> OperationRecord:
        """Reads a row by operation name.

        Raises:
            OperationNotFoundError: No row with that name.
        """
        ...

    def update(self, record: OperationRecord) -> None:
        """Overwrites an existing row.

        Raises:
            OperationNotFoundError: No row with that name.
        """
        ...
