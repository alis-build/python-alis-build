"""Encoding for an operation's private workflow state.

The Go package uses ``encoding/gob``, which has no Python equivalent. Pickle is
the closest ergonomic match and the wrong choice here: state is written now and
read back minutes or hours later, possibly by an instance running redeployed
code, so a release that renames or moves a state class would fail to unpickle
every operation still in flight. JSON survives that as long as field names hold,
and it can be read straight from the Spanner console at the moment an operation
is stuck — which is exactly when someone is looking.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, TypeVar

from ._errors import StateCodecError

__all__ = ["decode_state", "encode_state"]

T = TypeVar("T")


def encode_state(state: object) -> bytes:
    """Encodes private state to the bytes stored alongside an operation.

    Args:
        state: A dataclass instance, dict, list, or any other JSON-encodable
            value.

    Returns:
        UTF-8 JSON. Keys are sorted so that re-saving an unchanged value
        produces identical bytes and does not churn the stored row.

    Raises:
        StateCodecError: The value holds something JSON cannot represent.
    """
    if dataclasses.is_dataclass(state) and not isinstance(state, type):
        state = dataclasses.asdict(state)
    try:
        return json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    except (TypeError, ValueError) as exc:
        raise StateCodecError(f"private state is not JSON-encodable: {exc}") from exc


def _build_dataclass(parsed: Any, into: type[T]) -> T:
    """Constructs a dataclass from parsed JSON, naming whatever does not fit."""
    if not isinstance(parsed, dict):
        raise StateCodecError(
            f"cannot decode {type(parsed).__name__} into {into.__name__}, expected an object"
        )

    fields = {field.name for field in dataclasses.fields(into)}  # type: ignore[arg-type]
    unknown = sorted(set(parsed) - fields)
    if unknown:
        # Almost always a deploy that removed a field while operations holding
        # it were still in flight. Name it rather than raising a bare TypeError.
        raise StateCodecError(
            f"stored state has fields {into.__name__} does not define: {', '.join(unknown)}"
        )
    try:
        return into(**parsed)
    except TypeError as exc:
        raise StateCodecError(f"stored state does not fit {into.__name__}: {exc}") from exc


def decode_state(raw: bytes, into: type[T] | None = None) -> Any:
    """Decodes private state.

    Args:
        raw: The bytes stored with the operation.
        into: A dataclass type to reconstruct. Omit it to get parsed JSON.

    Returns:
        An instance of ``into``, or the parsed JSON when ``into`` is omitted.

    Raises:
        StateCodecError: The bytes are not JSON, or do not fit ``into``. The
            message names the field that does not fit.
    """
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise StateCodecError(f"stored state is not valid JSON: {exc}") from exc
    if into is None:
        return parsed
    return _build_dataclass(parsed, into)
