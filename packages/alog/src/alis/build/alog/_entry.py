"""Assembling the object that Cloud Logging parses out of a log line.

The key names here are the contract. Cloud Logging's agents look for a specific
set of them -- ``message``, ``severity``, ``time``, and the
``logging.googleapis.com/`` family -- and promote each into the corresponding
part of the structured entry it builds. A key spelled differently is not an
error; it just stays an ordinary field in the payload and loses its meaning.

Key order carries no meaning to a JSON parser, but a fixed order means two
services' lines can be read side by side, so the order below is deliberate and
does not change with what happens to be bound.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Final

from ._context import BoundContext
from ._levels import severity_for
from ._trace import trace_resource_name

TRACE_KEY: Final = "logging.googleapis.com/trace"
SPAN_KEY: Final = "logging.googleapis.com/spanId"
SAMPLED_KEY: Final = "logging.googleapis.com/trace_sampled"
SOURCE_LOCATION_KEY: Final = "logging.googleapis.com/sourceLocation"
LABELS_KEY: Final = "logging.googleapis.com/labels"
OPERATION_KEY: Final = "logging.googleapis.com/operation"
INSERT_ID_KEY: Final = "logging.googleapis.com/insertId"

RESERVED_KEYS: Final = frozenset(
    {
        "message",
        "severity",
        "time",
        "httpRequest",
        "stack_trace",
        TRACE_KEY,
        SPAN_KEY,
        SAMPLED_KEY,
        SOURCE_LOCATION_KEY,
        LABELS_KEY,
        OPERATION_KEY,
        INSERT_ID_KEY,
    }
)
"""Keys the format defines. A bound field may not overwrite one of these."""

# Timestamps are written to a fixed width -- always six fractional digits, even
# when they are zeros -- so that a column of them lines up and sorts as text in
# the order the events happened.
_TIME_FORMAT: Final = "%Y-%m-%dT%H:%M:%S"

_OPERATION_KEYS: Final = (
    ("id", "id"),
    ("producer", "producer"),
    ("first", "first"),
    ("last", "last"),
)

_HTTP_REQUEST_KEYS: Final = (
    ("request_method", "requestMethod"),
    ("request_url", "requestUrl"),
    ("request_size", "requestSize"),
    ("status", "status"),
    ("response_size", "responseSize"),
    ("user_agent", "userAgent"),
    ("remote_ip", "remoteIp"),
    ("server_ip", "serverIp"),
    ("referer", "referer"),
    ("latency", "latency"),
    ("cache_lookup", "cacheLookup"),
    ("cache_hit", "cacheHit"),
    ("cache_validated_with_origin_server", "cacheValidatedWithOriginServer"),
    ("cache_fill_bytes", "cacheFillBytes"),
    ("protocol", "protocol"),
)

# Everything a LogRecord carries of its own accord. Whatever a caller passed as
# extra= is whatever is left over. Read off a real record rather than written
# out by hand, so a new attribute in a future Python does not become a stray
# field on every log line.
_RECORD_ATTRIBUTES: Final = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
}


def format_time(created: float, /) -> str:
    """Renders a record's creation time the way the format expects.

    Args:
        created: Seconds since the epoch, as :attr:`logging.LogRecord.created`
            carries it.

    Returns:
        RFC 3339 in UTC, to a fixed six fractional digits, with a ``Z`` suffix.
    """
    moment = datetime.fromtimestamp(created, UTC)
    return f"{moment.strftime(_TIME_FORMAT)}.{moment.microsecond:06d}Z"


def _compact(source: object, keys: tuple[tuple[str, str], ...]) -> dict[str, Any] | None:
    """Renders a bound object, dropping empty values.

    Returns:
        The populated keys, or ``None`` if every one was empty. An object with
        nothing in it says nothing, so its key is left off the line entirely.
    """
    rendered = {wire: value for attribute, wire in keys if (value := getattr(source, attribute))}
    return rendered or None


def record_fields(record: logging.LogRecord, /) -> dict[str, Any]:
    """Returns whatever a caller passed to a logging call as ``extra=``.

    Args:
        record: The record to read.

    Returns:
        The record's non-standard attributes, which is exactly what ``extra=``
        put there.
    """
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _RECORD_ATTRIBUTES and not key.startswith("_")
    }


# One branch per optional entry key, each a single-line guard that either sets
# the key or leaves it out. The score counts how many keys the format has, not
# how hard the function is to follow; nothing here nests past two.
def build_entry(  # noqa: C901, PLR0912
    record: logging.LogRecord,
    context: BoundContext,
    /,
    *,
    source_location: bool = False,
    stack_trace: str = "",
) -> dict[str, Any]:
    """Assembles the object a log line carries.

    Args:
        record: The record being written.
        context: What is bound in the context the record was created in.
        source_location: Whether to name the file, line and function that
            logged. The record already carries them, so this costs nothing but
            the space on the line.
        stack_trace: A rendered traceback, or empty. Error Reporting groups
            errors on this key, so it is worth filling when there is one.

    Returns:
        The entry, in the format's key order, with empty values left out.
    """
    entry: dict[str, Any] = {
        "message": record.getMessage(),
        "severity": severity_for(record.levelno),
    }

    # A span with no trace to hang off correlates nothing, so all three keys
    # stand or fall together with the project id that names the trace.
    trace_name = span_id = ""
    sampled = False
    if context.trace is not None:
        resolved = trace_resource_name(context.trace.trace_id)
        if resolved is not None:
            trace_name = resolved
            span_id = context.trace.span_id
            sampled = context.trace.sampled

    if trace_name:
        entry[TRACE_KEY] = trace_name

    if source_location:
        entry[SOURCE_LOCATION_KEY] = {
            "file": record.pathname,
            "line": record.lineno,
            "function": record.funcName,
        }

    entry["time"] = format_time(record.created)

    if span_id:
        entry[SPAN_KEY] = span_id
    if sampled:
        entry[SAMPLED_KEY] = True

    if context.labels:
        entry[LABELS_KEY] = dict(context.labels)
    if context.operation is not None and (op := _compact(context.operation, _OPERATION_KEYS)):
        entry[OPERATION_KEY] = op
    if context.insert_id:
        entry[INSERT_ID_KEY] = context.insert_id
    if context.http_request is not None and (
        req := _compact(context.http_request, _HTTP_REQUEST_KEYS)
    ):
        entry["httpRequest"] = req
    if stack_trace:
        entry["stack_trace"] = stack_trace

    # Bound first, then the call's own extra=, which is the more specific of the
    # two. Neither may displace a key the format defines: a line whose severity
    # came from a stray field would be worse than one missing the field.
    for key, value in (*context.fields.items(), *record_fields(record).items()):
        if key not in RESERVED_KEYS:
            entry[key] = value

    return entry
