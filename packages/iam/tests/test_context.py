"""Context-variable propagation of the caller identity."""

from __future__ import annotations

import asyncio
import threading

import pytest

from alis.build.iam import (
    Identity,
    IdentityNotFoundError,
    from_context,
    from_context_or_none,
    identity_context,
    reset_identity,
    set_identity,
)


def test_missing_identity_raises() -> None:
    with pytest.raises(IdentityNotFoundError):
        from_context()
    assert from_context_or_none() is None


def test_context_manager_binds_and_restores(user: Identity) -> None:
    with identity_context(user):
        assert from_context() == user
    assert from_context_or_none() is None


def test_nested_contexts_restore_the_outer_identity(user: Identity) -> None:
    inner = Identity(id="inner")
    with identity_context(user):
        with identity_context(inner):
            assert from_context() == inner
        assert from_context() == user


def test_token_api(user: Identity) -> None:
    token = set_identity(user)
    try:
        assert from_context() == user
    finally:
        reset_identity(token)
    assert from_context_or_none() is None


async def test_tasks_inherit_but_do_not_leak_back(user: Identity) -> None:
    """asyncio snapshots the context per task, which is what we want."""
    child = Identity(id="child")
    seen: list[Identity | None] = []

    async def work() -> None:
        seen.append(from_context_or_none())
        with identity_context(child):
            seen.append(from_context())

    with identity_context(user):
        async with asyncio.TaskGroup() as group:
            group.create_task(work())
        assert from_context() == user

    assert seen == [user, child]


async def test_concurrent_tasks_do_not_see_each_others_identity() -> None:
    """The property every server interceptor depends on."""

    async def handler(name: str) -> str:
        with identity_context(Identity(id=name)):
            await asyncio.sleep(0)
            return from_context().id

    results = await asyncio.gather(*(handler(f"id-{n}") for n in range(20)))
    assert results == [f"id-{n}" for n in range(20)]


def test_threads_start_with_an_empty_context(user: Identity) -> None:
    """Documented, and the reason server wrappers must reset in a finally."""
    seen: list[Identity | None] = []

    with identity_context(user):
        thread = threading.Thread(target=lambda: seen.append(from_context_or_none()))
        thread.start()
        thread.join()

    assert seen == [None]


async def test_to_thread_carries_the_context(user: Identity) -> None:
    with identity_context(user):
        assert await asyncio.to_thread(from_context_or_none) == user
