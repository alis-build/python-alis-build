"""The Cloud Tasks queue that carries resumable callbacks.

The three upstream cases from tasks_test.go are ported directly: a transient
error is retried under one task name, ALREADY_EXISTS after a retry counts as
success, and a permanent error is not retried. The rest pin the request shape,
which the Go tests leave implicit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from alis.build.lro.tasks import AsyncCloudTasksQueue, CloudTasksQueue
from google.api_core import exceptions as api_exceptions
from google.cloud.tasks_v2 import HttpMethod

from alis.build.lro import Config

WHEN = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


class FakeTasksClient:
    """Replays a scripted list of errors, recording every request it is given."""

    def __init__(self, errors: list[Exception | None] | None = None) -> None:
        self.errors = list(errors or [])
        self.requests: list[Any] = []
        self.closed = False

    def create_task(self, request: Any = None, **kwargs: Any) -> Any:
        self.requests.append(request)
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        return request["task"] if isinstance(request, dict) else request.task

    def close(self) -> None:
        self.closed = True


class FakeAsyncTasksClient(FakeTasksClient):
    async def create_task(self, request: Any = None, **kwargs: Any) -> Any:  # type: ignore[override]
        return FakeTasksClient.create_task(self, request, **kwargs)

    async def close(self) -> None:  # type: ignore[override]
        self.closed = True


def make_queue(client: FakeTasksClient) -> CloudTasksQueue:
    return CloudTasksQueue(
        client=client,
        name="projects/p/locations/europe-west1/queues/launchpad-v1-operations",
        service_account_email="alis-build@p.iam.gserviceaccount.com",
        # Nanosecond backoff, as tasks_test.go does, so no test sleeps.
        initial_backoff=1e-9,
        maximum_backoff=1e-9,
    )


class TestIdempotency:
    def test_transient_error_is_retried_with_the_same_task_name(self) -> None:
        # The task name is what makes create_task idempotent. Regenerating it
        # per attempt would silently create a second callback for one logical
        # call, and the operation would be resumed twice.
        client = FakeTasksClient([api_exceptions.ServiceUnavailable("try again"), None])
        queue = make_queue(client)

        queue.schedule("https://example.test/resume", when=WHEN)

        assert len(client.requests) == 2
        names = {request["task"]["name"] for request in client.requests}
        assert len(names) == 1
        assert next(iter(names)).startswith(
            "projects/p/locations/europe-west1/queues/launchpad-v1-operations/tasks/"
        )

    def test_already_exists_after_a_retry_is_success(self) -> None:
        # The first call reached Cloud Tasks; only its response was lost.
        client = FakeTasksClient(
            [
                api_exceptions.ServiceUnavailable("response lost"),
                api_exceptions.AlreadyExists("task was created"),
            ]
        )
        queue = make_queue(client)

        queue.schedule("https://example.test/resume", when=WHEN)

        assert len(client.requests) == 2

    def test_already_exists_on_the_first_call_is_success(self) -> None:
        client = FakeTasksClient([api_exceptions.AlreadyExists("already there")])
        make_queue(client).schedule("https://example.test/resume", when=WHEN)
        assert len(client.requests) == 1

    def test_two_schedules_use_different_task_names(self) -> None:
        client = FakeTasksClient()
        queue = make_queue(client)

        queue.schedule("https://example.test/resume", when=WHEN)
        queue.schedule("https://example.test/resume", when=WHEN)

        assert client.requests[0]["task"]["name"] != client.requests[1]["task"]["name"]


class TestRetryPolicy:
    @pytest.mark.parametrize(
        "error",
        [
            api_exceptions.Aborted("aborted"),
            api_exceptions.DeadlineExceeded("slow"),
            api_exceptions.InternalServerError("internal"),
            api_exceptions.ResourceExhausted("quota"),
            api_exceptions.ServiceUnavailable("unavailable"),
        ],
    )
    def test_transient_errors_are_retried(self, error: Exception) -> None:
        client = FakeTasksClient([error, None])
        make_queue(client).schedule("https://example.test/resume", when=WHEN)
        assert len(client.requests) == 2

    @pytest.mark.parametrize(
        "error",
        [
            api_exceptions.InvalidArgument("invalid task"),
            api_exceptions.PermissionDenied("nope"),
            api_exceptions.NotFound("no such queue"),
        ],
    )
    def test_permanent_errors_are_not_retried(self, error: Exception) -> None:
        client = FakeTasksClient([error])
        with pytest.raises(type(error)):
            make_queue(client).schedule("https://example.test/resume", when=WHEN)
        assert len(client.requests) == 1


class TestRequestShape:
    @pytest.fixture
    def request_task(self) -> Any:
        client = FakeTasksClient()
        make_queue(client).schedule(
            "https://example.test/resume?operation=operations/abc", when=WHEN
        )
        return client.requests[0]["task"]

    def test_parent_is_the_queue(self) -> None:
        client = FakeTasksClient()
        make_queue(client).schedule("https://example.test/resume", when=WHEN)
        assert (
            client.requests[0]["parent"]
            == "projects/p/locations/europe-west1/queues/launchpad-v1-operations"
        )

    def test_method_is_put(self, request_task: Any) -> None:
        assert request_task["http_request"]["http_method"] == HttpMethod.PUT

    def test_url_is_carried_through(self, request_task: Any) -> None:
        assert request_task["http_request"]["url"] == (
            "https://example.test/resume?operation=operations/abc"
        )

    def test_oidc_token_names_the_service_account(self, request_task: Any) -> None:
        token = request_task["http_request"]["oidc_token"]
        assert token["service_account_email"] == "alis-build@p.iam.gserviceaccount.com"

    def test_schedule_time_is_the_requested_time(self, request_task: Any) -> None:
        assert request_task["schedule_time"] == WHEN

    def test_dispatch_deadline_is_the_task_deadline(self, request_task: Any) -> None:
        from alis.build.lro import TASK_DEADLINE

        assert request_task["dispatch_deadline"] == TASK_DEADLINE


class TestFromConfig:
    def test_builds_the_queue_path(self, make_config: Any) -> None:
        config: Config = make_config()
        client = FakeTasksClient()
        queue = CloudTasksQueue.from_config(config, client=client)
        assert queue.name == ("projects/p/locations/europe-west1/queues/launchpad-v1-operations")

    def test_carries_the_service_account(self, make_config: Any) -> None:
        queue = CloudTasksQueue.from_config(make_config(), client=FakeTasksClient())
        assert queue.service_account_email == "alis-build@p.iam.gserviceaccount.com"


class TestLifecycle:
    def test_close_closes_the_client(self) -> None:
        client = FakeTasksClient()
        make_queue(client).close()
        assert client.closed


class TestAsyncQueue:
    def make(self, client: FakeAsyncTasksClient) -> AsyncCloudTasksQueue:
        return AsyncCloudTasksQueue(
            client=client,
            name="projects/p/locations/europe-west1/queues/launchpad-v1-operations",
            service_account_email="alis-build@p.iam.gserviceaccount.com",
            initial_backoff=1e-9,
            maximum_backoff=1e-9,
        )

    async def test_schedules_a_task(self) -> None:
        client = FakeAsyncTasksClient()
        await self.make(client).schedule("https://example.test/resume", when=WHEN)
        assert len(client.requests) == 1

    async def test_retries_with_the_same_task_name(self) -> None:
        client = FakeAsyncTasksClient([api_exceptions.ServiceUnavailable("try again"), None])
        await self.make(client).schedule("https://example.test/resume", when=WHEN)
        names = {request["task"]["name"] for request in client.requests}
        assert len(client.requests) == 2
        assert len(names) == 1

    async def test_already_exists_is_success(self) -> None:
        client = FakeAsyncTasksClient([api_exceptions.AlreadyExists("created")])
        await self.make(client).schedule("https://example.test/resume", when=WHEN)
        assert len(client.requests) == 1

    async def test_permanent_error_propagates(self) -> None:
        client = FakeAsyncTasksClient([api_exceptions.InvalidArgument("bad")])
        with pytest.raises(api_exceptions.InvalidArgument):
            await self.make(client).schedule("https://example.test/resume", when=WHEN)

    async def test_request_shape_matches_the_sync_queue(self) -> None:
        # The two build the same request; only the transport differs.
        sync_client = FakeTasksClient()
        make_queue(sync_client).schedule("https://example.test/resume", when=WHEN)
        async_client = FakeAsyncTasksClient()
        await self.make(async_client).schedule("https://example.test/resume", when=WHEN)

        def without_name(request: Any) -> Any:
            task = {k: v for k, v in request["task"].items() if k != "name"}
            return {"parent": request["parent"], "task": task}

        assert without_name(sync_client.requests[0]) == without_name(async_client.requests[0])

    async def test_close_closes_the_client(self) -> None:
        client = FakeAsyncTasksClient()
        await self.make(client).close()
        assert client.closed
