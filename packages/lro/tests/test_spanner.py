"""The Spanner-backed operation store.

Driven against a fake database that speaks the same shape as the real client:
batch() and snapshot() context managers, insert/update mutations, and read()
returning rows. What the fake cannot prove is asserted directly instead — the
table name, the column order, and that column_info is passed on every read.
"""

from __future__ import annotations

from typing import Any

import pytest
from google.api_core import exceptions as api_exceptions
from google.longrunning import operations_pb2

from alis.build.lro import Config, OperationNotFoundError, OperationRecord
from alis.build.lro.spanner import SpannerStore


class FakeBatch:
    def __init__(self, database: FakeDatabase) -> None:
        self._database = database

    def __enter__(self) -> FakeBatch:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def insert(self, table: str, columns: Any, values: Any) -> None:
        self._database.mutations.append(("insert", table, tuple(columns), list(values)))
        self._database.rows[values[0][0].name] = list(values[0])

    def update(self, table: str, columns: Any, values: Any) -> None:
        self._database.mutations.append(("update", table, tuple(columns), list(values)))
        self._database.rows[values[0][0].name] = list(values[0])


class FakeSnapshot:
    def __init__(self, database: FakeDatabase) -> None:
        self._database = database

    def __enter__(self) -> FakeSnapshot:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self, table: str, columns: Any, keyset: Any, **kwargs: Any) -> list[list[Any]]:
        self._database.reads.append({"table": table, "columns": tuple(columns), **kwargs})
        if self._database.error is not None:
            error, self._database.error = self._database.error, None
            raise error
        name = keyset.keys[0][0]
        row = self._database.rows.get(name)
        return [list(row)] if row is not None else []


class FakeDatabase:
    def __init__(self) -> None:
        self.rows: dict[str, list[Any]] = {}
        self.mutations: list[tuple[str, str, tuple[str, ...], list[Any]]] = []
        self.reads: list[dict[str, Any]] = []
        self.error: Exception | None = None
        self.closed = False

    def batch(self) -> FakeBatch:
        return FakeBatch(self)

    def snapshot(self, **kwargs: Any) -> FakeSnapshot:
        return FakeSnapshot(self)

    def close(self) -> None:
        self.closed = True


def record(name: str = "operations/abc", **fields: Any) -> OperationRecord:
    return OperationRecord(operation=operations_pb2.Operation(name=name), **fields)


@pytest.fixture
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture
def spanner_store(database: FakeDatabase) -> SpannerStore:
    return SpannerStore(database=database, project="my-project", neuron="launchpad-v1")


class TestTableName:
    def test_hyphens_become_underscores(self, database: FakeDatabase) -> None:
        # A mismatch here silently targets a table that does not exist, so it is
        # asserted directly rather than inferred from a round trip.
        store = SpannerStore(database=database, project="my-project", neuron="launchpad-v1")
        assert store.table == "my_project_launchpad_v1_Operations"

    def test_names_without_hyphens_are_untouched(self, database: FakeDatabase) -> None:
        store = SpannerStore(database=database, project="proj", neuron="neuron")
        assert store.table == "proj_neuron_Operations"

    def test_from_config_uses_project_and_neuron(
        self, database: FakeDatabase, make_config: Any
    ) -> None:
        config: Config = make_config(project="my-project", neuron="launchpad-v1")
        store = SpannerStore.from_config(config, database=database)
        assert store.table == "my_project_launchpad_v1_Operations"


class TestRoundTrip:
    def test_insert_then_read(self, spanner_store: SpannerStore) -> None:
        spanner_store.insert(record(state=b'{"a":1}', resume_point="step-1"))
        found = spanner_store.read("operations/abc")
        assert found.operation.name == "operations/abc"
        assert found.state == b'{"a":1}'
        assert found.resume_point == "step-1"

    def test_read_returns_a_decoded_operation_not_bytes(self, spanner_store: SpannerStore) -> None:
        # Without column_info a PROTO column comes back as raw bytes rather than
        # raising, so the decoded type is asserted explicitly.
        spanner_store.insert(record())
        found = spanner_store.read("operations/abc")
        assert isinstance(found.operation, operations_pb2.Operation)

    def test_read_passes_column_info_for_the_operation_column(
        self, spanner_store: SpannerStore, database: FakeDatabase
    ) -> None:
        spanner_store.insert(record())
        spanner_store.read("operations/abc")
        column_info = database.reads[-1]["column_info"]
        assert isinstance(column_info["Operation"], operations_pb2.Operation)

    def test_update_overwrites(self, spanner_store: SpannerStore) -> None:
        spanner_store.insert(record(resume_point="step-1"))
        spanner_store.update(record(resume_point="step-2"))
        assert spanner_store.read("operations/abc").resume_point == "step-2"

    def test_read_missing_row_raises_and_names_it(self, spanner_store: SpannerStore) -> None:
        with pytest.raises(OperationNotFoundError, match="operations/nope"):
            spanner_store.read("operations/nope")


class TestContract:
    def test_columns_are_written_in_contract_order(
        self, spanner_store: SpannerStore, database: FakeDatabase
    ) -> None:
        spanner_store.insert(record())
        assert database.mutations[0][2] == ("Operation", "State", "ResumePoint", "UpdateTime")

    def test_insert_uses_insert_and_update_uses_update(
        self, spanner_store: SpannerStore, database: FakeDatabase
    ) -> None:
        # An insert that used update() would silently no-op on a missing row.
        spanner_store.insert(record())
        spanner_store.update(record())
        assert [mutation[0] for mutation in database.mutations] == ["insert", "update"]

    def test_writes_stamp_update_time(
        self, spanner_store: SpannerStore, database: FakeDatabase
    ) -> None:
        written = record()
        spanner_store.insert(written)
        assert database.mutations[0][3][0][3] is not None

    def test_update_time_is_timezone_aware(self, spanner_store: SpannerStore) -> None:
        spanner_store.insert(record())
        stored = spanner_store.read("operations/abc").update_time
        assert stored is not None and stored.tzinfo is not None


class TestRetries:
    def test_unavailable_is_retried(
        self, spanner_store: SpannerStore, database: FakeDatabase
    ) -> None:
        spanner_store.insert(record())
        database.error = api_exceptions.ServiceUnavailable("try again")

        found = spanner_store.read("operations/abc")

        assert found.operation.name == "operations/abc"
        # One attempt that raised, one that succeeded. insert() does not read.
        assert len(database.reads) == 2

    def test_permanent_error_is_not_retried(
        self, spanner_store: SpannerStore, database: FakeDatabase
    ) -> None:
        database.error = api_exceptions.PermissionDenied("nope")
        with pytest.raises(api_exceptions.PermissionDenied):
            spanner_store.read("operations/abc")


class TestLifecycle:
    def test_close_closes_the_database(
        self, spanner_store: SpannerStore, database: FakeDatabase
    ) -> None:
        spanner_store.close()
        assert database.closed
