"""The async surface, and its parity with the sync one.

Spanner has no async client, so every store call here is a thread offload. The
point of the surface is that an ASGI service does not stall its event loop on a
blocking read, not that the read stopped being blocking.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from google.protobuf.struct_pb2 import Struct

from alis.build.lro import AsyncClient, AsyncOperation, Client, Config, LroError
from alis.build.lro.testing import InMemoryStore


@dataclass
class State:
    owner: str
    polls: int = 0


@pytest.fixture(autouse=True)
def _not_on_cloud_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("K_SERVICE", raising=False)


@pytest.fixture
def async_client(store: InMemoryStore, make_config: Callable[..., Config]) -> AsyncClient:
    return AsyncClient(make_config(local=True), store=store)


async def wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


class TestLifecycle:
    async def test_new_operation_inserts(
        self, async_client: AsyncClient, store: InMemoryStore
    ) -> None:
        operation = await async_client.new_operation("operations/abc", Struct())
        assert isinstance(operation, AsyncOperation)
        assert store.read("operations/abc").operation.name == "operations/abc"

    async def test_get_operation_reads_back(self, async_client: AsyncClient) -> None:
        await async_client.new_operation("operations/abc", Struct())
        operation = await async_client.get_operation("operations/abc")
        assert operation.name == "operations/abc"

    async def test_private_state_round_trips(self, async_client: AsyncClient) -> None:
        operation = await async_client.new_operation("operations/abc", Struct())
        await operation.save_private_state(State(owner="users/1", polls=2))
        assert await operation.decode_private_state(State) == State("users/1", 2)

    async def test_complete_marks_it_done(self, async_client: AsyncClient) -> None:
        operation = await async_client.new_operation("operations/abc", Struct())
        await operation.complete(Struct())
        assert (await async_client.get_operation_pb("operations/abc")).done

    async def test_fail_records_the_reason(self, async_client: AsyncClient) -> None:
        operation = await async_client.new_operation("operations/abc", Struct())
        await operation.fail("upstream said no")
        pb = await async_client.get_operation_pb("operations/abc")
        assert pb.done and pb.error.message == "upstream said no"

    async def test_metadata_round_trips(self, async_client: AsyncClient) -> None:
        operation = await async_client.new_operation("operations/abc", Struct())
        await operation.save_metadata(Struct())
        assert operation.unmarshal_metadata(Struct) is not None

    async def test_resume_point_round_trips(self, async_client: AsyncClient) -> None:
        operation = await async_client.new_operation("operations/abc", Struct())
        await operation.save_resume_point("step-2")
        fetched = await async_client.get_operation("operations/abc")
        assert fetched.resume_point == "step-2"


class TestResume:
    async def test_async_handler_runs_as_a_task(self, async_client: AsyncClient) -> None:
        seen: list[str] = []

        async def handler(operation: AsyncOperation) -> None:
            seen.append(operation.name)
            await operation.complete(Struct())

        async_client.add_resumable_handler("create-agent", handler)
        operation = await async_client.new_operation("operations/abc", Struct())

        await operation.resume_via_tasks("create-agent", 0)

        assert await wait_for(lambda: seen == ["operations/abc"])
        assert (await async_client.get_operation_pb("operations/abc")).done

    async def test_raising_handler_fails_the_operation(
        self, async_client: AsyncClient, store: InMemoryStore
    ) -> None:
        async def handler(operation: AsyncOperation) -> None:
            raise ValueError("upstream said no")

        async_client.add_resumable_handler("boom", handler)
        operation = await async_client.new_operation("operations/abc", Struct())

        await operation.resume_via_tasks("boom", 0)

        # Polled through the store, which is synchronous, so the predicate stays
        # a plain callable and the test never sleeps a fixed time.
        assert await wait_for(lambda: store.read("operations/abc").operation.done)
        pb = await async_client.get_operation_pb("operations/abc")
        assert "ValueError" in pb.error.message
        assert "upstream said no" in pb.error.message

    async def test_unregistered_path_raises(self, async_client: AsyncClient) -> None:
        operation = await async_client.new_operation("operations/abc", Struct())
        with pytest.raises(LroError, match="create-agent"):
            await operation.resume_via_tasks("create-agent", 0)

    async def test_cloud_branch_refuses_without_a_queue(
        self, store: InMemoryStore, make_config: Callable[..., Config]
    ) -> None:
        client = AsyncClient(make_config(local=False), store=store)

        async def handler(operation: AsyncOperation) -> None:
            return None

        client.add_resumable_handler("create-agent", handler)
        operation = await client.new_operation("operations/abc", Struct())

        with pytest.raises(LroError, match="no task queue"):
            await operation.resume_via_tasks("create-agent", 0)

    async def test_resume_now_awaits_the_handler(self, async_client: AsyncClient) -> None:
        seen: list[str] = []

        async def handler(operation: AsyncOperation) -> None:
            seen.append(operation.name)

        async_client.add_resumable_handler("create-agent", handler)
        await async_client.new_operation("operations/abc", Struct())

        await async_client.resume_now("create-agent", "operations/abc")

        assert seen == ["operations/abc"]


class TestParity:
    def test_the_two_clients_expose_the_same_surface(self) -> None:
        # A method added to one and forgotten on the other is the failure this
        # package is most likely to ship, and the least likely to be noticed.
        def public(cls: type) -> set[str]:
            return {name for name in dir(cls) if not name.startswith("_")}

        assert public(AsyncClient) == public(Client)

    def test_the_two_operations_expose_the_same_surface(self) -> None:
        from alis.build.lro import Operation

        def public(cls: type) -> set[str]:
            return {name for name in dir(cls) if not name.startswith("_")}

        assert public(AsyncOperation) == public(Operation)

    async def test_from_env_matches_the_sync_client(
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

        assert (
            AsyncClient.from_env("launchpad-v1", store=store).config
            == Client.from_env("launchpad-v1", store=store).config
        )

    async def test_close_is_safe(self, async_client: AsyncClient) -> None:
        await async_client.close()
