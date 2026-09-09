"""The Cloud Tasks queue that carries resumable callbacks."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from google.api_core import exceptions as api_exceptions
from google.api_core import retry as retries
from google.api_core import retry_async as retries_async
from google.cloud import tasks_v2

from .._config import Config
from .._store import TASK_DEADLINE

__all__ = ["AsyncCloudTasksQueue", "CloudTasksQueue"]

# Ported from tasks.go. Anything not listed is permanent and propagates.
_TRANSIENT: Final = (
    api_exceptions.Aborted,
    api_exceptions.DeadlineExceeded,
    api_exceptions.InternalServerError,
    api_exceptions.ResourceExhausted,
    api_exceptions.ServiceUnavailable,
)

_INITIAL_BACKOFF: Final = 0.1
_MAXIMUM_BACKOFF: Final = 5.0
_BACKOFF_MULTIPLIER: Final = 2.0


def _queue_path(config: Config) -> str:
    return (
        f"projects/{config.cloud_tasks_project}"
        f"/locations/{config.cloud_tasks_location}"
        f"/queues/{config.cloud_tasks_queue}"
    )


class _QueueBase:
    """Shared configuration and request building for both queue flavours."""

    __slots__ = ("_client", "_initial_backoff", "_maximum_backoff", "_name", "_service_account")

    def __init__(
        self,
        *,
        client: Any,
        name: str,
        service_account_email: str,
        initial_backoff: float = _INITIAL_BACKOFF,
        maximum_backoff: float = _MAXIMUM_BACKOFF,
    ) -> None:
        self._client = client
        self._name = name
        self._service_account = service_account_email
        self._initial_backoff = initial_backoff
        self._maximum_backoff = maximum_backoff

    @property
    def name(self) -> str:
        """The fully qualified queue path."""
        return self._name

    @property
    def service_account_email(self) -> str:
        """The service account whose OIDC token authorises the callback."""
        return self._service_account

    def _request(self, url: str, when: datetime) -> dict[str, Any]:
        """Builds one CreateTask request.

        The task carries an explicit name containing a fresh UUID, which is what
        makes CreateTask idempotent. Generate it once per logical call and reuse
        it across retries: a name regenerated per attempt would let a lost
        response become a second callback, and the operation would resume twice.
        """
        return {
            "parent": self._name,
            "task": {
                "name": f"{self._name}/tasks/{uuid.uuid4()}",
                "http_request": {
                    "url": url,
                    "http_method": tasks_v2.HttpMethod.PUT,
                    "oidc_token": {"service_account_email": self._service_account},
                },
                "schedule_time": when,
                "dispatch_deadline": TASK_DEADLINE,
            },
        }


class CloudTasksQueue(_QueueBase):
    """Schedules resumable callbacks through Cloud Tasks.

    Args:
        client: A ``CloudTasksClient``, or anything with ``create_task``.
        name: The fully qualified queue path.
        service_account_email: Authorises the callback via an OIDC token.
        initial_backoff: First retry gap, in seconds.
        maximum_backoff: Ceiling the gap grows to, in seconds.
    """

    @classmethod
    def from_config(cls, config: Config, *, client: Any | None = None) -> CloudTasksQueue:
        """Builds a queue from configuration, connecting unless given a client."""
        return cls(
            client=client if client is not None else tasks_v2.CloudTasksClient(),
            name=_queue_path(config),
            service_account_email=config.cloud_tasks_service_account,
        )

    def schedule(self, url: str, *, when: datetime) -> None:
        """Schedules a callback to that URL at that time.

        ``ALREADY_EXISTS`` is success, not failure: it means an earlier attempt
        reached Cloud Tasks and only its response was lost.

        Raises:
            GoogleAPICallError: Anything not in the transient set.
        """
        request = self._request(url, when)
        retry = retries.Retry(
            predicate=retries.if_exception_type(*_TRANSIENT),
            initial=self._initial_backoff,
            maximum=self._maximum_backoff,
            multiplier=_BACKOFF_MULTIPLIER,
        )
        try:
            retry(self._client.create_task)(request=request)
        except api_exceptions.AlreadyExists:
            return

    def close(self) -> None:
        """Releases the Cloud Tasks client."""
        closer = getattr(self._client, "close", None)
        if callable(closer):
            closer()


class AsyncCloudTasksQueue(_QueueBase):
    """The async queue, over ``CloudTasksAsyncClient``.

    Unlike Spanner, Cloud Tasks does ship a real async client, so this path is
    genuinely non-blocking rather than a thread offload.
    """

    @classmethod
    def from_config(cls, config: Config, *, client: Any | None = None) -> AsyncCloudTasksQueue:
        """Builds a queue from configuration, connecting unless given a client."""
        return cls(
            client=client if client is not None else tasks_v2.CloudTasksAsyncClient(),
            name=_queue_path(config),
            service_account_email=config.cloud_tasks_service_account,
        )

    async def schedule(self, url: str, *, when: datetime) -> None:
        """Schedules a callback to that URL at that time."""
        request = self._request(url, when)
        retry = retries_async.AsyncRetry(
            predicate=retries.if_exception_type(*_TRANSIENT),
            initial=self._initial_backoff,
            maximum=self._maximum_backoff,
            multiplier=_BACKOFF_MULTIPLIER,
        )
        try:
            await retry(self._client.create_task)(request=request)
        except api_exceptions.AlreadyExists:
            return

    async def close(self) -> None:
        """Releases the Cloud Tasks client."""
        closer = getattr(self._client, "close", None)
        if callable(closer):
            await closer()
