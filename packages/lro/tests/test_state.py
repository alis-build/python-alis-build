"""The JSON private-state codec.

State is written now and read back minutes or hours later, possibly by an
instance running redeployed code. These tests pin the properties that matters
for: readable on the wire, stable, and loud when a shape no longer fits.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from google.longrunning import operations_pb2

from alis.build.lro import Operation, OperationRecord, StateCodecError, decode_state, encode_state


@dataclass
class CreateAgentState:
    owner: str
    poll_count: int = 0


@dataclass
class NestedState:
    owner: str
    tags: list[str] = field(default_factory=list)
    limits: dict[str, int] = field(default_factory=dict)


class RecordingStore:
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


class TestEncoding:
    def test_dataclass_round_trips(self) -> None:
        raw = encode_state(CreateAgentState(owner="users/123", poll_count=2))
        assert decode_state(raw, CreateAgentState) == CreateAgentState("users/123", 2)

    def test_nested_containers_round_trip(self) -> None:
        state = NestedState(owner="users/1", tags=["a", "b"], limits={"max": 3})
        assert decode_state(encode_state(state), NestedState) == state

    def test_encoding_is_stable_for_equal_values(self) -> None:
        # A resumed handler that re-saves unchanged state must not churn the row.
        assert encode_state({"b": 1, "a": 2}) == encode_state({"a": 2, "b": 1})

    def test_readable_as_json_in_the_console(self) -> None:
        # Being able to read stuck state straight from Spanner is the whole
        # reason this is JSON and not pickle.
        assert json.loads(encode_state(CreateAgentState("users/123"))) == {
            "owner": "users/123",
            "poll_count": 0,
        }

    def test_dict_and_list_supported(self) -> None:
        assert decode_state(encode_state([1, 2])) == [1, 2]
        assert decode_state(encode_state({"a": 1})) == {"a": 1}

    def test_unencodable_value_raises(self) -> None:
        with pytest.raises(StateCodecError):
            encode_state({"when": object()})


class TestDecoding:
    def test_malformed_state_raises(self) -> None:
        with pytest.raises(StateCodecError):
            decode_state(b"{not json", CreateAgentState)

    def test_unknown_field_is_rejected_clearly(self) -> None:
        # A deploy that removed a field must fail with a readable message rather
        # than a bare TypeError out of the dataclass constructor.
        with pytest.raises(StateCodecError, match="gone"):
            decode_state(b'{"owner": "u", "gone": 1}', CreateAgentState)

    def test_missing_required_field_is_rejected_clearly(self) -> None:
        with pytest.raises(StateCodecError, match="owner"):
            decode_state(b'{"poll_count": 1}', CreateAgentState)

    def test_missing_optional_field_takes_its_default(self) -> None:
        assert decode_state(b'{"owner": "u"}', CreateAgentState) == CreateAgentState("u", 0)

    def test_decoding_a_non_object_into_a_dataclass_raises(self) -> None:
        with pytest.raises(StateCodecError):
            decode_state(b"[1, 2]", CreateAgentState)


class TestOnOperation:
    def test_set_does_not_persist(self, operation: Operation, store: RecordingStore) -> None:
        operation.set_private_state(CreateAgentState("users/1"))
        assert store.updates == []

    def test_save_persists(self, operation: Operation, store: RecordingStore) -> None:
        operation.save_private_state(CreateAgentState("users/1"))
        assert len(store.updates) == 1

    def test_round_trips_through_the_record(self, operation: Operation) -> None:
        operation.set_private_state(CreateAgentState("users/1", 3))
        assert operation.decode_private_state(CreateAgentState) == CreateAgentState("users/1", 3)

    def test_decoding_empty_state_names_the_operation(self, operation: Operation) -> None:
        with pytest.raises(StateCodecError, match="operations/abc"):
            operation.decode_private_state(CreateAgentState)

    def test_decoding_without_a_type_returns_parsed_json(self, operation: Operation) -> None:
        operation.set_private_state({"owner": "users/1"})
        assert operation.decode_private_state() == {"owner": "users/1"}

    def test_state_is_visible_on_the_record(self, operation: Operation) -> None:
        operation.set_private_state({"a": 1})
        assert json.loads(operation.record.state) == {"a": 1}
