"""Fetching several resources' policies at once."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Final, Protocol

from google.iam.v1.iam_policy_pb2 import GetIamPolicyRequest
from google.iam.v1.policy_pb2 import Policy

from .._errors import PolicyFetchError, PoolExhaustedError

_logger: Final = logging.getLogger(__name__)

DEFAULT_LIMIT: Final = 16
"""How many fetches run at once unless told otherwise.

A handler resolving the parent policy for every row of a long list would
otherwise open one connection per row, which is a good way to overwhelm the
service you are asking.
"""


class PolicyFetcher(Protocol):
    """Anything that can fetch one resource's policy.

    A generated client's ``get_iam_policy`` method satisfies this as-is. Use
    ``functools.partial`` to pin per-call options such as a deadline.
    """

    # Declared as returning an awaitable rather than as `async def`, so both an
    # async function and a plain one returning a coroutine satisfy it.
    def __call__(self, request: GetIamPolicyRequest, /) -> Awaitable[Policy]: ...


class SyncPolicyFetcher(Protocol):
    """The blocking counterpart of :class:`PolicyFetcher`."""

    def __call__(self, request: GetIamPolicyRequest, /) -> Policy: ...


@dataclass(frozen=True, slots=True)
class PolicyResult:
    """What came back for one resource."""

    resource: str
    policy: Policy | None = None

    error: Exception | None = None
    """Why the fetch failed, if it did.

    Never a cancellation: withdrawing the request is the caller's doing, not a
    failed fetch, so a cancellation propagates instead of landing here.
    """

    @property
    def ok(self) -> bool:
        """Whether the fetch succeeded."""
        return self.error is None


@dataclass(frozen=True, slots=True)
class PolicySet:
    """Everything a pool collected, in the order the work was added.

    Ordering is by submission rather than completion so a run is reproducible
    and tests do not depend on which fetch happened to finish first.
    """

    results: tuple[PolicyResult, ...] = ()

    @property
    def policies(self) -> tuple[Policy, ...]:
        """The policies that were fetched successfully.

        Spread these straight into a check::

            authorizer.has_role(["roles/admin"], *collected.policies)
        """
        return tuple(result.policy for result in self.results if result.policy is not None)

    @property
    def errors(self) -> tuple[PolicyResult, ...]:
        """The fetches that failed."""
        return tuple(result for result in self.results if not result.ok)

    @property
    def ok(self) -> bool:
        """Whether every fetch succeeded."""
        return not self.errors

    def raise_for_errors(self) -> None:
        """Raises if any fetch failed.

        Raises:
            ExceptionGroup: Containing every failure, not only the first, so a
                partial outage is diagnosable from one traceback.
        """
        failures = [result.error for result in self.errors if result.error is not None]
        if failures:
            raise ExceptionGroup("fetching policies failed", failures)

    def __iter__(self) -> Iterator[PolicyResult]:
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)


@dataclass(slots=True)
class _Work:
    """One unit of queued work."""

    resource: str
    factory: Callable[[], Awaitable[Policy]] | None = None
    sync_factory: Callable[[], Policy] | None = None
    ready: Policy | None = None


class _BasePool:
    """State and result assembly shared by the two pool flavours."""

    __slots__ = ("_gathered", "_limit", "_timeout", "_work")

    def __init__(self, *, limit: int | None = DEFAULT_LIMIT, timeout: float | None = None) -> None:
        self._work: list[_Work] = []
        self._limit = limit
        self._timeout = timeout
        self._gathered = False

    def add_policies(self, *policies: Policy) -> None:
        """Adds policies you already hold, so they collect with the rest."""
        self._work.extend(_Work(resource="", ready=policy) for policy in policies)

    def _check_reusable(self) -> None:
        if self._gathered:
            raise PoolExhaustedError("this pool has already been gathered; build a new one")
        self._gathered = True

    def __len__(self) -> int:
        return len(self._work)


class PolicyPool(_BasePool):
    """Fetches several resources' policies concurrently.

    A pool is single-use: build one per request, add the work, gather once.

    Args:
        limit: How many fetches may be in flight at once. ``None`` removes the
            cap.
        timeout: Seconds to allow each fetch. Left unset by default, because a
            default deadline is a new way for a request to fail and the right
            value depends on what you are calling.

    Example:
        >>> pool = PolicyPool()
        >>> pool.add("resources/a", client.get_iam_policy)      # doctest: +SKIP
        >>> pool.add("resources/b", client.get_iam_policy)      # doctest: +SKIP
        >>> collected = await pool.gather()                     # doctest: +SKIP
        >>> authorizer.has_role(["roles/admin"], *collected.policies)  # doctest: +SKIP
    """

    __slots__ = ()

    def add(self, resource: str, fetcher: PolicyFetcher, /) -> None:
        """Queues a fetch of one resource's policy."""

        async def call() -> Policy:
            return await fetcher(GetIamPolicyRequest(resource=resource))

        self._work.append(_Work(resource=resource, factory=call))

    def add_task(self, name: str, factory: Callable[[], Awaitable[Policy]], /) -> None:
        """Queues arbitrary work that produces a policy.

        Use this when the policy does not come from a plain ``GetIamPolicy``
        call -- a cache lookup, or a call that needs extra arguments.
        """
        self._work.append(_Work(resource=name, factory=factory))

    async def gather(self) -> PolicySet:
        """Runs every queued fetch and collects the results.

        A failed fetch does not discard the successful ones: it becomes an
        entry in the returned set, so a caller can decide whether a partial
        answer is usable. Use :meth:`PolicySet.raise_for_errors` when it is not.

        Raises:
            PoolExhaustedError: If the pool has already been gathered.
        """
        self._check_reusable()
        results: list[PolicyResult | None] = [None] * len(self._work)
        semaphore = asyncio.Semaphore(self._limit) if self._limit else None

        async def run(index: int, work: _Work) -> None:
            if work.ready is not None:
                results[index] = PolicyResult(resource=work.resource, policy=work.ready)
                return
            assert work.factory is not None
            try:
                if semaphore is None:
                    policy = await self._call(work.factory)
                else:
                    async with semaphore:
                        policy = await self._call(work.factory)
            # Deliberately not BaseException: a cancellation is the caller
            # withdrawing the request, not a failed fetch, so it must propagate.
            except Exception as exc:
                _logger.warning("fetching the policy for %r failed: %s", work.resource, exc)
                error = PolicyFetchError(work.resource)
                error.__cause__ = exc
                results[index] = PolicyResult(resource=work.resource, error=error)
            else:
                results[index] = PolicyResult(resource=work.resource, policy=policy)

        if self._work:
            async with asyncio.TaskGroup() as group:
                for index, work in enumerate(self._work):
                    group.create_task(run(index, work))

        return PolicySet(tuple(result for result in results if result is not None))

    async def _call(self, factory: Callable[[], Awaitable[Policy]]) -> Policy:
        if self._timeout is None:
            return await factory()
        async with asyncio.timeout(self._timeout):
            return await factory()


class SyncPolicyPool(_BasePool):
    """The blocking counterpart of :class:`PolicyPool`, backed by threads.

    Use it from a synchronous server, where there is no event loop to run the
    fetches on.
    """

    __slots__ = ()

    def add(self, resource: str, fetcher: SyncPolicyFetcher, /) -> None:
        """Queues a fetch of one resource's policy."""
        self._work.append(
            _Work(
                resource=resource,
                sync_factory=lambda: fetcher(GetIamPolicyRequest(resource=resource)),
            )
        )

    def add_task(self, name: str, factory: Callable[[], Policy], /) -> None:
        """Queues arbitrary work that produces a policy."""
        self._work.append(_Work(resource=name, sync_factory=factory))

    def gather(self) -> PolicySet:
        """Runs every queued fetch and collects the results.

        Raises:
            PoolExhaustedError: If the pool has already been gathered.
        """
        self._check_reusable()
        if not self._work:
            return PolicySet()

        results: list[PolicyResult] = []
        workers = self._limit or len(self._work)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                None if work.ready is not None else executor.submit(_run_sync, work)
                for work in self._work
            ]
            for work, future in zip(self._work, futures, strict=True):
                if future is None:
                    results.append(PolicyResult(resource=work.resource, policy=work.ready))
                    continue
                results.append(future.result(timeout=self._timeout))
        return PolicySet(tuple(results))


def _run_sync(work: _Work) -> PolicyResult:
    """Runs one blocking fetch, turning a failure into a result."""
    assert work.sync_factory is not None
    try:
        policy = work.sync_factory()
    except Exception as exc:
        _logger.warning("fetching the policy for %r failed: %s", work.resource, exc)
        error = PolicyFetchError(work.resource)
        error.__cause__ = exc
        return PolicyResult(resource=work.resource, error=error)
    return PolicyResult(resource=work.resource, policy=policy)


def policies_from(results: Iterable[PolicyResult], /) -> tuple[Policy, ...]:
    """Extracts the successful policies from any iterable of results."""
    return tuple(result.policy for result in results if result.policy is not None)


__all__ = [
    "DEFAULT_LIMIT",
    "PolicyFetcher",
    "PolicyPool",
    "PolicyResult",
    "PolicySet",
    "SyncPolicyFetcher",
    "SyncPolicyPool",
    "policies_from",
]
