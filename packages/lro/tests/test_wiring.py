"""Where the two cloud backends meet the client.

The cloud branch of resume_via_tasks raised a placeholder throughout the core
track. These tests replace it with the real thing and pin what happens when
scheduling fails, which is the case that would otherwise leave an operation
hanging until its TTL.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from google.api_core import exceptions as api_exceptions
from google.protobuf.struct_pb2 import Struct

from alis.build.lro import AsyncClient, Client, Config, LroError
from alis.build.lro.testing import InMemoryStore


class FakeQueue:
    """Records what was scheduled, or fails on demand."""

    def __init__(self, error: Exception | None = None) -> None:
        self.scheduled: list[tuple[str, datetime]] = []
        self.error = error
        self.closed = False

    def schedule(self, url: str, *, when: datetime) -> None:
        if self.error is not None:
            raise self.error
        self.scheduled.append((url, when))

    def close(self) -> None:
        self.closed = True


class FakeAsyncQueue(FakeQueue):
    async def schedule(self, url: str, *, when: datetime) -> None:  # type: ignore[override]
        FakeQueue.schedule(self, url, when=when)

    async def close(self) -> None:  # type: ignore[override]
        self.closed = True


@pytest.fixture
def cloud_client(store: InMemoryStore, make_config: Callable[..., Config]) -> Client:
    return Client(make_config(local=False), store=store, queue=FakeQueue())


class TestCloudBranch:
    def test_schedules_a_task_at_the_callback_url(self, cloud_client: Client) -> None:
        queue: Any = cloud_client.queue
        cloud_client.add_resumable_handler("create-agent", lambda op: None)
        operation = cloud_client.new_operation("operations/abc", Struct())

        operation.resume_via_tasks("create-agent", 0)

        url, _ = queue.scheduled[0]
        assert url == (
            "https://launchpad-v1-42.europe-west1.run.app"
            "/resume-operation/create-agent?operation=operations/abc"
        )

    def test_custom_prefix_is_reflected_in_the_url(self, cloud_client: Client) -> None:
        # The client builds the URL from the same prefix the middleware serves.
        queue: Any = cloud_client.queue
        cloud_client.set_callback_prefix("lro")
        cloud_client.add_resumable_handler("create-agent", lambda op: None)
        operation = cloud_client.new_operation("operations/abc", Struct())

        operation.resume_via_tasks("create-agent", 0)

        assert "/lro/create-agent?operation=operations/abc" in queue.scheduled[0][0]

    def test_delay_becomes_the_schedule_time(self, cloud_client: Client) -> None:
        queue: Any = cloud_client.queue
        cloud_client.add_resumable_handler("create-agent", lambda op: None)
        operation = cloud_client.new_operation("operations/abc", Struct())

        before = datetime.now().astimezone()
        operation.resume_via_tasks("create-agent", 30)

        _, when = queue.scheduled[0]
        assert timedelta(seconds=25) <= when - before <= timedelta(seconds=35)

    def test_timedelta_delay_is_accepted(self, cloud_client: Client) -> None:
        queue: Any = cloud_client.queue
        cloud_client.add_resumable_handler("create-agent", lambda op: None)
        operation = cloud_client.new_operation("operations/abc", Struct())

        before = datetime.now().astimezone()
        operation.resume_via_tasks("create-agent", timedelta(minutes=1))

        _, when = queue.scheduled[0]
        assert when - before >= timedelta(seconds=55)

    def test_unregistered_path_never_reaches_the_queue(self, cloud_client: Client) -> None:
        queue: Any = cloud_client.queue
        operation = cloud_client.new_operation("operations/abc", Struct())

        with pytest.raises(LroError, match="create-agent"):
            operation.resume_via_tasks("create-agent", 0)

        assert queue.scheduled == []


class TestSchedulingFailure:
    def test_failure_marks_the_operation_failed(
        self, store: InMemoryStore, make_config: Callable[..., Config]
    ) -> None:
        # An operation whose callback was never scheduled would otherwise hang
        # until its TTL, with no signal to whoever is polling it.
        queue = FakeQueue(error=api_exceptions.InvalidArgument("bad task"))
        client = Client(make_config(local=False), store=store, queue=queue)
        client.add_resumable_handler("create-agent", lambda op: None)
        operation = client.new_operation("operations/abc", Struct())

        with pytest.raises(LroError):
            operation.resume_via_tasks("create-agent", 0)

        failed = client.get_operation_pb("operations/abc")
        assert failed.done
        assert "schedul" in failed.error.message

    def test_the_original_error_is_the_cause(
        self, store: InMemoryStore, make_config: Callable[..., Config]
    ) -> None:
        queue = FakeQueue(error=api_exceptions.InvalidArgument("bad task"))
        client = Client(make_config(local=False), store=store, queue=queue)
        client.add_resumable_handler("create-agent", lambda op: None)
        operation = client.new_operation("operations/abc", Struct())

        with pytest.raises(LroError) as caught:
            operation.resume_via_tasks("create-agent", 0)

        assert isinstance(caught.value.__cause__, api_exceptions.InvalidArgument)


class TestConstruction:
    def test_explicit_store_and_queue_are_used(
        self, store: InMemoryStore, make_config: Callable[..., Config]
    ) -> None:
        queue = FakeQueue()
        client = Client(make_config(), store=store, queue=queue)
        assert client.queue is queue

    def test_store_stays_optional_and_defaults_are_not_built_when_given(
        self, store: InMemoryStore, make_config: Callable[..., Config]
    ) -> None:
        # Widening store from required to optional must not change behaviour for
        # callers written against the core track.
        client = Client(make_config(), store=store)
        assert client.queue is None


class TestClose:
    def test_closes_both_backends(
        self, store: InMemoryStore, make_config: Callable[..., Config]
    ) -> None:
        queue = FakeQueue()
        client = Client(make_config(), store=store, queue=queue)
        client.close()
        assert queue.closed

    def test_tolerates_a_missing_queue(
        self, store: InMemoryStore, make_config: Callable[..., Config]
    ) -> None:
        Client(make_config(), store=store).close()


class TestAsyncWiring:
    async def test_schedules_through_the_async_queue(
        self, store: InMemoryStore, make_config: Callable[..., Config]
    ) -> None:
        queue = FakeAsyncQueue()
        client = AsyncClient(make_config(local=False), store=store, queue=queue)

        async def handler(operation: Any) -> None:
            return None

        client.add_resumable_handler("create-agent", handler)
        operation = await client.new_operation("operations/abc", Struct())

        await operation.resume_via_tasks("create-agent", 0)

        assert "create-agent?operation=operations/abc" in queue.scheduled[0][0]

    async def test_scheduling_failure_fails_the_operation(
        self, store: InMemoryStore, make_config: Callable[..., Config]
    ) -> None:
        queue = FakeAsyncQueue(error=api_exceptions.InvalidArgument("bad task"))
        client = AsyncClient(make_config(local=False), store=store, queue=queue)

        async def handler(operation: Any) -> None:
            return None

        client.add_resumable_handler("create-agent", handler)
        operation = await client.new_operation("operations/abc", Struct())

        with pytest.raises(LroError):
            await operation.resume_via_tasks("create-agent", 0)

        assert (await client.get_operation_pb("operations/abc")).done

    async def test_close_closes_the_queue(
        self, store: InMemoryStore, make_config: Callable[..., Config]
    ) -> None:
        queue = FakeAsyncQueue()
        client = AsyncClient(make_config(), store=store, queue=queue)
        await client.close()
        assert queue.closed
