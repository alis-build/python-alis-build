"""The OperationStore contract, exercised against the in-memory implementation.

Every store must satisfy these, so a Spanner store can be dropped in later and
run the same assertions.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest
from google.longrunning import operations_pb2

from alis.build.lro import OperationNotFoundError, OperationRecord
from alis.build.lro.testing import InMemoryStore


def record(name: str = "operations/abc", **fields: object) -> OperationRecord:
    return OperationRecord(operation=operations_pb2.Operation(name=name), **fields)  # type: ignore[arg-type]


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


class TestRoundTrip:
    def test_insert_then_read(self, store: InMemoryStore) -> None:
        store.insert(record(state=b'{"a":1}', resume_point="step-1"))
        found = store.read("operations/abc")
        assert found.operation.name == "operations/abc"
        assert found.state == b'{"a":1}'
        assert found.resume_point == "step-1"

    def test_update_overwrites(self, store: InMemoryStore) -> None:
        store.insert(record(resume_point="step-1"))
        store.update(record(resume_point="step-2"))
        assert store.read("operations/abc").resume_point == "step-2"

    def test_read_missing_raises_and_names_it(self, store: InMemoryStore) -> None:
        with pytest.raises(OperationNotFoundError, match="operations/nope"):
            store.read("operations/nope")

    def test_insert_twice_raises(self, store: InMemoryStore) -> None:
        store.insert(record())
        with pytest.raises(Exception, match="operations/abc"):
            store.insert(record())

    def test_update_missing_raises(self, store: InMemoryStore) -> None:
        with pytest.raises(OperationNotFoundError, match="operations/abc"):
            store.update(record())


class TestUpdateTime:
    def test_insert_sets_it(self, store: InMemoryStore) -> None:
        store.insert(record())
        assert isinstance(store.read("operations/abc").update_time, datetime)

    def test_update_bumps_it(self, store: InMemoryStore) -> None:
        store.insert(record())
        first = store.read("operations/abc").update_time
        store.update(record(resume_point="step-2"))
        second = store.read("operations/abc").update_time
        assert first is not None and second is not None
        assert second >= first

    def test_it_is_timezone_aware(self, store: InMemoryStore) -> None:
        # A naive timestamp compared against a Spanner TIMESTAMP is a bug
        # waiting for the first daylight-saving change.
        store.insert(record())
        stored = store.read("operations/abc").update_time
        assert stored is not None and stored.tzinfo is not None
        assert stored.tzinfo.utcoffset(stored) == UTC.utcoffset(None)


class TestIsolation:
    def test_reads_do_not_alias_stored_state(self, store: InMemoryStore) -> None:
        # Mutating what you read must not silently rewrite the row, or a store
        # swap to Spanner changes behaviour that tests never covered.
        store.insert(record(resume_point="step-1"))
        found = store.read("operations/abc")
        found.resume_point = "mutated"
        assert store.read("operations/abc").resume_point == "step-1"

    def test_writes_do_not_alias_the_caller_record(self, store: InMemoryStore) -> None:
        held = record(resume_point="step-1")
        store.insert(held)
        held.resume_point = "mutated"
        assert store.read("operations/abc").resume_point == "step-1"


class TestConcurrency:
    def test_concurrent_updates_leave_a_consistent_row(self, store: InMemoryStore) -> None:
        store.insert(record())
        errors: list[BaseException] = []

        def write(index: int) -> None:
            try:
                store.update(record(resume_point=f"step-{index}"))
            except Exception as exc:  # collected and asserted, not swallowed
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert store.read("operations/abc").resume_point.startswith("step-")
