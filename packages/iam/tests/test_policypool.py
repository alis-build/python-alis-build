"""Fetching several resources' policies at once."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import pytest
from google.iam.v1.iam_policy_pb2 import GetIamPolicyRequest
from google.iam.v1.policy_pb2 import Policy

from alis.build.iam import Identity, IdentityType
from alis.build.iam.authz import Authorizer, Registry
from alis.build.iam.authz.testing import binding, policy
from alis.build.iam.policypool import (
    PolicyFetchError,
    PolicyPool,
    PolicySet,
    PoolExhaustedError,
    SyncPolicyPool,
    policies_from,
)

USER = Identity(type=IdentityType.USER, id="1934872948")


def fetcher(
    delay: float = 0.0, fails: bool = False
) -> Callable[[GetIamPolicyRequest], Awaitable[Policy]]:
    """Builds a fetcher that records which resources it was asked for."""
    seen: list[str] = []

    async def fetch(request: GetIamPolicyRequest) -> Policy:
        seen.append(request.resource)
        if delay:
            await asyncio.sleep(delay)
        if fails:
            raise RuntimeError(f"backend down for {request.resource}")
        return policy(binding(f"roles/{request.resource}", "user:1934872948"))

    fetch.seen = seen  # type: ignore[attr-defined]
    return fetch


async def test_empty_pool() -> None:
    collected = await PolicyPool().gather()
    assert collected.policies == ()
    assert collected.ok
    assert len(collected) == 0


async def test_the_resource_is_the_only_request_field_set() -> None:
    requests: list[GetIamPolicyRequest] = []

    async def fetch(request: GetIamPolicyRequest) -> Policy:
        requests.append(request)
        return Policy()

    pool = PolicyPool()
    pool.add("resources/a", fetch)
    await pool.gather()

    assert requests[0].resource == "resources/a"
    assert not requests[0].HasField("options")


async def test_results_follow_submission_order_not_completion_order() -> None:
    """So a run is reproducible and tests do not race."""

    async def fetch(request: GetIamPolicyRequest) -> Policy:
        # Later resources finish first.
        await asyncio.sleep(0.02 - 0.004 * int(request.resource[-1]))
        return Policy()

    pool = PolicyPool()
    for index in range(5):
        pool.add(f"resources/{index}", fetch)
    collected = await pool.gather()

    assert [result.resource for result in collected] == [f"resources/{i}" for i in range(5)]


async def test_fetches_actually_overlap() -> None:
    pool = PolicyPool()
    fetch = fetcher(delay=0.05)
    for index in range(5):
        pool.add(f"resources/{index}", fetch)

    started = time.perf_counter()
    await pool.gather()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.2, "five 50ms fetches run one after another would take 250ms"


async def test_a_failure_does_not_discard_the_successes() -> None:
    """Treating one transient failure as "no policies" turns a partial outage
    into a blanket refusal."""
    pool = PolicyPool()
    pool.add("resources/ok", fetcher())
    pool.add("resources/bad", fetcher(fails=True))
    pool.add("resources/also-ok", fetcher())

    collected = await pool.gather()

    assert len(collected.policies) == 2
    assert len(collected.errors) == 1
    assert collected.errors[0].resource == "resources/bad"
    assert not collected.ok


async def test_errors_name_the_resource_and_keep_the_cause() -> None:
    pool = PolicyPool()
    pool.add("resources/bad", fetcher(fails=True))
    collected = await pool.gather()

    error = collected.errors[0].error
    assert isinstance(error, PolicyFetchError)
    assert error.resource == "resources/bad"
    assert isinstance(error.__cause__, RuntimeError)
    assert "backend down" in str(error.__cause__)


async def test_raise_for_errors_reports_every_failure() -> None:
    pool = PolicyPool()
    pool.add("resources/bad", fetcher(fails=True))
    pool.add("resources/worse", fetcher(fails=True))
    collected = await pool.gather()

    with pytest.raises(ExceptionGroup) as excinfo:
        collected.raise_for_errors()
    assert len(excinfo.value.exceptions) == 2


async def test_raise_for_errors_is_quiet_when_everything_worked() -> None:
    pool = PolicyPool()
    pool.add("resources/a", fetcher())
    (await pool.gather()).raise_for_errors()


async def test_concurrency_limit_is_observed() -> None:
    in_flight = 0
    peak = 0

    async def fetch(request: GetIamPolicyRequest) -> Policy:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return Policy()

    pool = PolicyPool(limit=3)
    for index in range(12):
        pool.add(f"resources/{index}", fetch)
    await pool.gather()

    assert peak <= 3


async def test_no_limit_runs_everything_at_once() -> None:
    in_flight = 0
    peak = 0

    async def fetch(request: GetIamPolicyRequest) -> Policy:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return Policy()

    pool = PolicyPool(limit=None)
    for index in range(8):
        pool.add(f"resources/{index}", fetch)
    await pool.gather()

    assert peak == 8


async def test_a_timeout_becomes_a_failed_result_not_a_hang() -> None:
    pool = PolicyPool(timeout=0.02)
    pool.add("resources/slow", fetcher(delay=1.0))
    collected = await pool.gather()

    assert not collected.ok
    assert isinstance(collected.errors[0].error, PolicyFetchError)


async def test_cancellation_propagates_and_is_not_captured() -> None:
    """A cancellation is the caller withdrawing, not a failed fetch."""
    started = asyncio.Event()

    async def fetch(request: GetIamPolicyRequest) -> Policy:
        started.set()
        await asyncio.sleep(10)
        return Policy()

    pool = PolicyPool()
    pool.add("resources/slow", fetch)
    task = asyncio.create_task(pool.gather())
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


async def test_add_policies_collects_alongside_fetches() -> None:
    known = policy(binding("roles/cached", "user:1934872948"))
    pool = PolicyPool()
    pool.add_policies(known)
    pool.add("resources/a", fetcher())
    collected = await pool.gather()

    assert len(collected.policies) == 2


async def test_add_task_takes_arbitrary_work() -> None:
    async def from_cache() -> Policy:
        return policy(binding("roles/cached", "user:1934872948"))

    pool = PolicyPool()
    pool.add_task("cache", from_cache)
    collected = await pool.gather()

    assert collected.results[0].resource == "cache"
    assert len(collected.policies) == 1


async def test_a_pool_is_single_use() -> None:
    pool = PolicyPool()
    pool.add("resources/a", fetcher())
    await pool.gather()

    with pytest.raises(PoolExhaustedError):
        await pool.gather()


async def test_collected_policies_feed_an_authorizer(registry: Registry) -> None:
    """The whole point of the pool."""
    pool = PolicyPool()
    pool.add("resources/admin", fetcher())
    collected = await pool.gather()

    authorizer = Authorizer(USER, registry=registry)
    assert authorizer.has_role(["roles/resources/admin"], *collected.policies) is True


def test_policies_from_helper() -> None:
    collected = PolicySet(())
    assert policies_from(collected) == ()


# -- the blocking flavour --------------------------------------------------


def sync_fetcher(fails: bool = False) -> Callable[[GetIamPolicyRequest], Policy]:
    def fetch(request: GetIamPolicyRequest) -> Policy:
        time.sleep(0.02)
        if fails:
            raise RuntimeError(f"backend down for {request.resource}")
        return policy(binding(f"roles/{request.resource}", "user:1934872948"))

    return fetch


def test_sync_pool_collects_in_submission_order() -> None:
    pool = SyncPolicyPool()
    for index in range(4):
        pool.add(f"resources/{index}", sync_fetcher())
    collected = pool.gather()

    assert [result.resource for result in collected] == [f"resources/{i}" for i in range(4)]
    assert len(collected.policies) == 4


def test_sync_pool_overlaps_fetches() -> None:
    pool = SyncPolicyPool()
    for index in range(6):
        pool.add(f"resources/{index}", sync_fetcher())

    started = time.perf_counter()
    pool.gather()
    assert time.perf_counter() - started < 0.1


def test_sync_pool_keeps_partial_results() -> None:
    pool = SyncPolicyPool()
    pool.add("resources/ok", sync_fetcher())
    pool.add("resources/bad", sync_fetcher(fails=True))
    collected = pool.gather()

    assert len(collected.policies) == 1
    assert collected.errors[0].resource == "resources/bad"


def test_sync_pool_is_single_use() -> None:
    pool = SyncPolicyPool()
    pool.add("resources/a", sync_fetcher())
    pool.gather()
    with pytest.raises(PoolExhaustedError):
        pool.gather()


def test_sync_empty_pool() -> None:
    assert SyncPolicyPool().gather().policies == ()
