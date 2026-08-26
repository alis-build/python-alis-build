"""Helpers for testing code that uses this package.

Importing this module pulls in nothing beyond the core, so it is safe to use
from a test suite that has none of the extras installed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from ._registry import SystemConfig, set_system_config, system_config

__all__ = ["fresh_authz_registry", "fresh_system_config"]


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


@contextmanager
def fresh_authz_registry(**overrides: object) -> Iterator[object]:
    """Swaps in an empty authorization registry for the duration of a test.

    Requires the ``authz`` extra. The registry defaults to stricter settings
    than production: an unregistered permission raises rather than denying, and
    a conditional policy binding raises rather than being granted, so either
    shows up as a failing test rather than as a surprise in production.

    Args:
        **overrides: Passed to the new ``Registry``, to override those defaults.

    Yields:
        The empty registry that is in effect inside the block.
    """
    from .authz import (
        ConditionMode,
        Registry,
        default_registry,
        reset_condition_warnings,
        set_default_registry,
    )

    settings: dict[str, object] = {
        "strict_permissions": True,
        "condition_mode": ConditionMode.ERROR,
    }
    settings.update(overrides)

    previous = default_registry()
    registry = Registry(**settings)  # type: ignore[arg-type]
    set_default_registry(registry)
    reset_condition_warnings()
    try:
        yield registry
    finally:
        set_default_registry(previous)
        reset_condition_warnings()
