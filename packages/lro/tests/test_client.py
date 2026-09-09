"""Client construction, the resumable handler registry, and operation CRUD."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from google.protobuf.struct_pb2 import Struct, Value

from alis.build.lro import (
    Client,
    Config,
    ConfigError,
    LroError,
    Operation,
    OperationNameError,
    OperationNotFoundError,
)
from alis.build.lro.testing import InMemoryStore


def noop(operation: Operation) -> None:
    return None


class TestConstruction:
    def test_validates_its_config(
        self, store: InMemoryStore, make_config: Callable[..., Config]
    ) -> None:
        with pytest.raises(ConfigError, match="host"):
            Client(make_config(host=""), store=store)

    def test_exposes_its_config(self, client: Client) -> None:
        assert client.config.neuron == "launchpad-v1"

    def test_from_env_builds_a_working_client(
        self, store: InMemoryStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key, value in {
            "ALIS_OS_PROJECT": "p",
            "ALIS_REGION": "europe-west1",
            "ALIS_PROJECT_NR": "42",
            "ALIS_MANAGED_SPANNER_PROJECT": "sp",
            "ALIS_MANAGED_SPANNER_INSTANCE": "si",
            "ALIS_MANAGED_SPANNER_DB": "sd",
        }.items():
            monkeypatch.setenv(key, value)
        built = Client.from_env("launchpad-v1", store=store)
        assert built.config.cloud_tasks_queue == "launchpad-v1-operations"

    def test_close_is_safe_without_cloud_backends(self, client: Client) -> None:
        client.close()


class TestHandlerRegistry:
    def test_registers_and_reports(self, client: Client) -> None:
        client.add_resumable_handler("create-agent", noop)
        assert "create-agent" in client.resumable_paths

    def test_duplicate_path_raises_and_names_it(self, client: Client) -> None:
        client.add_resumable_handler("create-agent", noop)
        with pytest.raises(LroError, match="create-agent"):
            client.add_resumable_handler("create-agent", noop)

    def test_empty_path_raises(self, client: Client) -> None:
        with pytest.raises(LroError):
            client.add_resumable_handler("", noop)

    def test_missing_handler_raises(self, client: Client) -> None:
        with pytest.raises(LroError):
            client.add_resumable_handler("create-agent", None)  # type: ignore[arg-type]

    def test_batch_registration(self, client: Client) -> None:
        # A mapping, not **kwargs: "create-agent" is not a Python identifier.
        client.add_resumable_handlers({"create-agent": noop, "publish-agent": noop})
        assert client.resumable_paths == frozenset({"create-agent", "publish-agent"})

    def test_batch_failure_names_the_offending_path(self, client: Client) -> None:
        client.add_resumable_handler("create-agent", noop)
        with pytest.raises(LroError, match="create-agent"):
            client.add_resumable_handlers({"publish-agent": noop, "create-agent": noop})


class TestOperationCrud:
    def test_new_operation_inserts_and_sets_metadata(
        self, client: Client, store: InMemoryStore
    ) -> None:
        metadata = Struct(fields={"target": Value(string_value="streams/abc")})
        operation = client.new_operation("operations/abc", metadata)
        assert operation.name == "operations/abc"
        assert store.read("operations/abc").operation.HasField("metadata")

    def test_new_operation_rejects_a_bad_name_before_touching_the_store(
        self, client: Client, store: InMemoryStore
    ) -> None:
        with pytest.raises(OperationNameError):
            client.new_operation("agents/1", Struct())
        with pytest.raises(OperationNotFoundError):
            store.read("agents/1")

    def test_get_operation_returns_a_live_wrapper(self, client: Client) -> None:
        client.new_operation("operations/abc", Struct())
        fetched = client.get_operation("operations/abc")
        fetched.save_resume_point("step-2")
        assert client.get_operation("operations/abc").resume_point == "step-2"

    def test_get_operation_missing_raises(self, client: Client) -> None:
        with pytest.raises(OperationNotFoundError, match="operations/nope"):
            client.get_operation("operations/nope")

    def test_get_operation_validates_the_name(self, client: Client) -> None:
        with pytest.raises(OperationNameError):
            client.get_operation("agents/1")

    def test_get_operation_pb_returns_the_message(self, client: Client) -> None:
        client.new_operation("operations/abc", Struct())
        assert client.get_operation_pb("operations/abc").name == "operations/abc"

    def test_new_operation_twice_with_one_name_raises(self, client: Client) -> None:
        client.new_operation("operations/abc", Struct())
        with pytest.raises(LroError, match="operations/abc"):
            client.new_operation("operations/abc", Struct())
