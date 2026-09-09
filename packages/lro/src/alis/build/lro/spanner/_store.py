"""An OperationStore backed by a Spanner table."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from google.api_core import exceptions as api_exceptions
from google.api_core import retry as retries
from google.cloud import spanner
from google.longrunning import operations_pb2

from .._config import Config
from .._errors import OperationNotFoundError
from .._operation import OperationRecord

__all__ = ["SpannerStore"]

# The order Go writes them in. This is a contract with an existing table, not a
# preference: a reordering here writes each value into the wrong column.
_COLUMNS: Final = ("Operation", "State", "ResumePoint", "UpdateTime")

_RETRY: Final = retries.Retry(
    predicate=retries.if_exception_type(api_exceptions.ServiceUnavailable),
    initial=0.1,
    maximum=5.0,
    multiplier=1.5,
)


def operations_table(project: str, neuron: str) -> str:
    """Returns the operations table name for a project and neuron.

    The name is derived, never configured, and must match what the Terraform
    module provisioned. Hyphens are not legal in a Spanner identifier, so both
    parts have them replaced.
    """
    return f"{project}_{neuron}_Operations".replace("-", "_")


class SpannerStore:
    """Persists operations in the neuron's Spanner operations table.

    Args:
        database: The Spanner database to read and write.
        project: The owning project, which names the table.
        neuron: The service, which names the table.
    """

    __slots__ = ("_database", "_table")

    def __init__(self, *, database: Any, project: str, neuron: str) -> None:
        self._database = database
        self._table = operations_table(project, neuron)

    @classmethod
    def from_config(cls, config: Config, *, database: Any | None = None) -> SpannerStore:
        """Builds a store from configuration, connecting unless given a database.

        Args:
            config: Supplies the Spanner coordinates and the table name parts.
            database: An existing database handle, mostly for tests.
        """
        if database is None:
            client = spanner.Client(project=config.spanner_project)
            instance = client.instance(config.spanner_instance)
            database = instance.database(
                config.spanner_database,
                database_role=config.database_role or None,
            )
        return cls(database=database, project=config.project, neuron=config.neuron)

    @property
    def table(self) -> str:
        """The operations table this store reads and writes."""
        return self._table

    def insert(self, record: OperationRecord) -> None:
        """Writes a new row, stamping ``update_time``."""
        self._write("insert", record)

    def update(self, record: OperationRecord) -> None:
        """Overwrites an existing row, stamping ``update_time``.

        Spanner treats an update of a missing row as a no-op rather than an
        error, so this is only correct for a row ``insert`` already created.
        """
        self._write("update", record)

    def read(self, name: str) -> OperationRecord:
        """Reads a row by operation name.

        Raises:
            OperationNotFoundError: No row with that name.
        """
        rows = _RETRY(self._read_rows)(name)
        if not rows:
            raise OperationNotFoundError(f"operation {name} not found")
        operation, state, resume_point, update_time = rows[0]
        return OperationRecord(
            operation=operation,
            state=bytes(state) if state else b"",
            resume_point=resume_point or "",
            update_time=update_time,
        )

    def close(self) -> None:
        """Releases the database handle."""
        closer = getattr(self._database, "close", None)
        if callable(closer):
            closer()

    def _write(self, kind: str, record: OperationRecord) -> None:
        record.update_time = datetime.now(UTC)
        values = [
            [
                record.operation,
                record.state,
                record.resume_point,
                record.update_time,
            ]
        ]
        with self._database.batch() as batch:
            getattr(batch, kind)(table=self._table, columns=_COLUMNS, values=values)

    def _read_rows(self, name: str) -> list[list[Any]]:
        with self._database.snapshot() as snapshot:
            return list(
                snapshot.read(
                    table=self._table,
                    columns=_COLUMNS,
                    keyset=spanner.KeySet(keys=[[name]]),
                    # Without this a PROTO column comes back as raw bytes rather
                    # than raising, and the failure surfaces far from here.
                    column_info={"Operation": operations_pb2.Operation()},
                )
            )
