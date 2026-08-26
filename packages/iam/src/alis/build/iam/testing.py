"""Helpers for testing code that uses this package.

Importing this module pulls in nothing beyond the core, so it is safe to use
from a test suite that has none of the extras installed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from ._registry import SystemConfig, set_system_config, system_config

__all__ = ["fresh_system_config"]


@contextmanager
def fresh_system_config() -> Iterator[SystemConfig]:
    """Swaps in an empty system/admin email registry for the duration of a test.

    The registries are process-wide and append-only by design, so without this
    a test that registers an admin email silently privileges that address for
    every test that runs after it. Use it as an autouse fixture::

        @pytest.fixture(autouse=True)
        def _iam_registry():
            with fresh_system_config() as config:
                yield config

    Yields:
        The empty config that is in effect inside the block.
    """
    previous = system_config()
    config = SystemConfig()
    set_system_config(config)
    try:
        yield config
    finally:
        set_system_config(previous)
