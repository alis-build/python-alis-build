"""Carrying trace correlation on gRPC metadata."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from .._trace import TRACE_HEADER

METADATA_KEY: Final = TRACE_HEADER
"""Carries the trace context. Same key as the HTTP header."""

Metadata = Iterable[tuple[str, str | bytes]]


def trace_context_from_metadata(metadata: Metadata, /) -> str:
    """Returns the trace header a call carries, or empty if it carries none.

    Args:
        metadata: The call's invocation metadata as key/value pairs.

    Returns:
        The raw header value. gRPC lowercases metadata keys, but the lookup
        folds case anyway so hand-built metadata works too.
    """
    found = ""
    for name, value in metadata:
        if name.lower() != METADATA_KEY:
            continue
        # The last value wins: it is the one the most recent caller appended.
        found = value.decode("latin-1") if isinstance(value, bytes) else value
    return found


def trace_metadata(header: str, /) -> list[tuple[str, str]]:
    """Builds the metadata that forwards a trace context to another service.

    Args:
        header: The raw header value, usually taken straight from the inbound
            call so the whole chain shares one trace.

    Returns:
        Key/value pairs to append to an outgoing call's metadata, or nothing at
        all when there is no trace to forward.
    """
    return [(METADATA_KEY, header)] if header else []
