"""Operation resource names."""

from __future__ import annotations

from typing import Final

from ._errors import OperationNameError

__all__ = ["validate_operation_name"]

_PREFIX: Final = "operations/"


def validate_operation_name(name: str) -> None:
    """Checks that a name is a well-formed operation resource name.

    Args:
        name: The candidate name, expected to look like ``operations/{id}``.

    Raises:
        OperationNameError: The name is empty, does not start with
            ``operations/``, or carries no id after the prefix.
    """
    if not name:
        raise OperationNameError("operation name is required")
    if not name.startswith(_PREFIX) or len(name) <= len(_PREFIX):
        raise OperationNameError(f"invalid operation name {name!r}, expected 'operations/{{id}}'")
