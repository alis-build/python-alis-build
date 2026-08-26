"""Shared fixtures.

The system/admin email registries are process-wide and append-only, so without
an autouse reset a test that registers an admin email would silently privilege
that address for every test that runs afterwards.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from alis.build.iam import Identity, IdentityType, SystemConfig
from alis.build.iam.testing import fresh_authz_registry, fresh_system_config


@pytest.fixture(autouse=True)
def system_config(monkeypatch: pytest.MonkeyPatch) -> Iterator[SystemConfig]:
    """Gives every test an empty registry and no ALIS_OS_PROJECT."""
    monkeypatch.delenv("ALIS_OS_PROJECT", raising=False)
    with fresh_system_config() as config:
        yield config


@pytest.fixture
def user() -> Identity:
    """The reference identity the wire-format expectations are built around."""
    return Identity(
        type=IdentityType.USER,
        id="1934872948",
        email="john@example.com",
        group_ids=["df913r888"],
    )


@pytest.fixture
def registry() -> Iterator[Any]:
    """An empty authorization registry, stricter than the production default."""
    with fresh_authz_registry() as value:
        yield value


@pytest.fixture
def lenient_registry() -> Iterator[Any]:
    """An authorization registry configured the way production is."""
    from alis.build.iam.authz import ConditionMode

    with fresh_authz_registry(
        strict_permissions=False, condition_mode=ConditionMode.ALLOW
    ) as value:
        yield value
