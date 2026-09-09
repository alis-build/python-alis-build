"""Errors raised by this package.

Every message names the fix: a missing config field names the field, a missing
environment variable names the variable, an unsupported region lists the ones
that work.
"""

from __future__ import annotations

__all__ = [
    "ConfigError",
    "LroError",
    "MissingDependencyError",
    "OperationNameError",
]


class LroError(Exception):
    """Base class for every error this package raises."""


class ConfigError(LroError):
    """Configuration is missing a value, or holds one that cannot work."""


class MissingDependencyError(LroError, ImportError):
    """A subpackage was imported without the extra that provides its dependency.

    Also an :class:`ImportError`, so ``except ImportError`` at a call site that
    probes for optional support keeps working.
    """


class OperationNameError(LroError):
    """An operation resource name is missing or malformed."""
