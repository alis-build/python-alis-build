"""Shared fixtures.

The system/admin email registries are process-wide and append-only, so without
an autouse reset a test that registers an admin email would silently privilege
that address for every test that runs afterwards.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from alis.build.iam import Identity, IdentityType, SystemConfig
from alis.build.iam.testing import fresh_system_config


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
