"""The operation wrapper: names, metadata, and how an operation finishes."""

from __future__ import annotations

import pytest
from google.longrunning import operations_pb2
from google.protobuf.empty_pb2 import Empty
from google.protobuf.struct_pb2 import Struct, Value
from google.rpc import code_pb2

from alis.build.lro import (
    LroError,
    Operation,
    OperationNameError,
    OperationRecord,
    validate_operation_name,
)


class RecordingStore:
    """Minimal store standing in until the real seam arrives in P4."""

    def __init__(self) -> None:
        self.updates: list[OperationRecord] = []

    def insert(self, record: OperationRecord) -> None:
        self.updates.append(record)

    def read(self, name: str) -> OperationRecord:
        raise NotImplementedError

    def update(self, record: OperationRecord) -> None:
        self.updates.append(record)


@pytest.fixture
def store() -> RecordingStore:
    return RecordingStore()


@pytest.fixture
def operation(store: RecordingStore) -> Operation:
    record = OperationRecord(operation=operations_pb2.Operation(name="operations/abc"))
    return Operation(record=record, store=store)


class TestOperationName:
    @pytest.mark.parametrize(
        "name",
        ["", "operations/", "agents/1", "operations", "/operations/1", "Operations/1"],
    )
    def test_invalid_names_rejected(self, name: str) -> None:
        with pytest.raises(OperationNameError):
            validate_operation_name(name)

    @pytest.mark.parametrize("name", ["operations/abc-123", "operations/1", "operations/a/b"])
    def test_valid_names_accepted(self, name: str) -> None:
        validate_operation_name(name)

    def test_error_names_the_offending_value(self) -> None:
        with pytest.raises(OperationNameError, match="agents/1"):
            validate_operation_name("agents/1")


class TestMetadata:
    def test_round_trips_through_any(self, operation: Operation) -> None:
        operation.set_metadata(Struct(fields={"target": Value(string_value="streams/abc")}))
        assert operation.unmarshal_metadata(Struct)["target"] == "streams/abc"

    def test_set_does_not_persist(self, operation: Operation, store: RecordingStore) -> None:
        operation.set_metadata(Struct())
        assert store.updates == []

    def test_save_persists(self, operation: Operation, store: RecordingStore) -> None:
        operation.save_metadata(Struct())
        assert len(store.updates) == 1

    def test_unmarshal_wrong_type_raises(self, operation: Operation) -> None:
        operation.set_metadata(Struct())
        with pytest.raises(LroError):
            operation.unmarshal_metadata(Empty)

    def test_unmarshal_without_metadata_raises(self, operation: Operation) -> None:
        with pytest.raises(LroError):
            operation.unmarshal_metadata(Struct)


class TestFinishing:
    def test_complete_sets_done_and_response(
        self, operation: Operation, store: RecordingStore
    ) -> None:
        operation.complete(Empty())
        assert operation.pb.done is True
        assert operation.pb.HasField("response")
        assert len(store.updates) == 1

    def test_complete_with_none_raises(self, operation: Operation) -> None:
        with pytest.raises(LroError, match="operations/abc"):
            operation.complete(None)  # type: ignore[arg-type]

    def test_fail_defaults_to_unknown_not_ok(self, operation: Operation) -> None:
        # Go leaves Status.code at 0, which decodes as OK on a failed operation.
        # This port defaults to UNKNOWN so a failure never reads as success.
        operation.fail("boom")
        assert operation.pb.done is True
        assert operation.pb.error.code == code_pb2.UNKNOWN
        assert operation.pb.error.message == "boom"

    def test_fail_accepts_an_explicit_code(self, operation: Operation) -> None:
        operation.fail("nope", code=code_pb2.PERMISSION_DENIED)
        assert operation.pb.error.code == code_pb2.PERMISSION_DENIED

    def test_fail_persists(self, operation: Operation, store: RecordingStore) -> None:
        operation.fail("boom")
        assert len(store.updates) == 1

    def test_completing_twice_is_the_callers_problem_not_a_crash(
        self, operation: Operation
    ) -> None:
        operation.complete(Empty())
        operation.fail("changed my mind")
        assert operation.pb.error.message == "changed my mind"
        assert operation.pb.HasField("error")


class TestResumePoint:
    def test_defaults_to_empty(self, operation: Operation) -> None:
        assert operation.resume_point == ""

    def test_set_does_not_persist(self, operation: Operation, store: RecordingStore) -> None:
        operation.set_resume_point("step-2")
        assert operation.resume_point == "step-2"
        assert store.updates == []

    def test_save_persists(self, operation: Operation, store: RecordingStore) -> None:
        operation.save_resume_point("step-2")
        assert operation.resume_point == "step-2"
        assert len(store.updates) == 1


class TestAccessors:
    def test_name_reads_through_to_the_message(self, operation: Operation) -> None:
        assert operation.name == "operations/abc"

    def test_pb_is_the_stored_message(self, operation: Operation) -> None:
        assert operation.pb.name == "operations/abc"

    def test_record_defaults(self) -> None:
        record = OperationRecord(operation=operations_pb2.Operation(name="operations/a"))
        assert record.state == b""
        assert record.resume_point == ""
        assert record.update_time is None
