"""Errors raised by this package.

Every message names the fix: a missing config field names the field, a missing
environment variable names the variable, an unsupported region lists the ones
that work.
"""

from __future__ import annotations

__all__ = [
    "ConfigError",
    "HandlerError",
    "HandlerNotFoundError",
    "LroError",
    "MissingDependencyError",
    "OperationNameError",
    "OperationNotFoundError",
    "StateCodecError",
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


class StateCodecError(LroError):
    """Private state could not be encoded, or no longer fits the type given."""


class OperationNotFoundError(LroError):
    """No operation with that name is stored."""


class HandlerNotFoundError(LroError):
    """No resumable handler is registered for that callback path."""


class HandlerError(LroError):
    """A resumable handler raised. Its operation has been marked failed."""
