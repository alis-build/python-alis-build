"""Helpers for subpackages that live behind an optional extra."""

from __future__ import annotations

from ._errors import MissingDependencyError

_DISTRIBUTION = "alis-build-alog"


def missing_dependency(subpackage: str, extra: str, missing: str) -> MissingDependencyError:
    """Builds the error raised when an optional subpackage cannot import its deps.

    Args:
        subpackage: Dotted name of the subpackage being imported.
        extra: Name of the extra that provides the dependency.
        missing: Name of the third-party module that failed to import.

    Returns:
        An error whose message names both the missing module and the exact
        install command that fixes it.
    """
    return MissingDependencyError(
        f"{subpackage} requires the {extra!r} extra (missing: {missing}). "
        f"Install it with: pip install '{_DISTRIBUTION}[{extra}]'"
    )
