"""Reading trace correlation out of the ``x-cloud-trace-context`` header.

Load balancers and the platform's own runtimes attach this header to every
inbound request. Carrying its trace id onto each log line is what lets Logs
Explorer show a request's logs against its trace, and what puts the log lines
under the matching span in Trace.

The header looks like ``TRACE_ID/SPAN_ID;o=TRACE_TRUE``. Both the span and the
sampling flag are optional, and a header carrying only a trace id is normal.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

TRACE_HEADER: Final = "x-cloud-trace-context"
"""The header carrying trace correlation, lowercased for case-insensitive lookup."""

# First non-empty wins. The platform sets the first; the other two are what
# Google's own client libraries look for, so a service running outside the
# platform still gets correlated logs.
_PROJECT_VARS: Final = ("ALIS_OS_PROJECT", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT")

_SAMPLED: Final = "o=1"


@dataclass(frozen=True, slots=True)
class TraceContext:
    """The correlation a request carries."""

    trace_id: str
    """The trace this request belongs to. Never empty."""

    span_id: str = ""
    """The span within that trace, or empty if the header named none."""

    sampled: bool = False
    """Whether the request was selected for trace collection."""


def parse_trace_context(header: str, /) -> TraceContext | None:
    """Reads a trace context out of a header value.

    Args:
        header: The raw ``x-cloud-trace-context`` value.

    Returns:
        The trace context, or ``None`` if the header is absent, empty, or names
        no trace id. A header with no trace id correlates nothing, so there is
        nothing useful to return.
    """
    if not header:
        return None

    trace_id, _, remainder = header.partition("/")
    if not trace_id:
        return None

    # The span segment ends at the next slash: a span id never contains one, so
    # anything after it is a malformed sender's problem rather than part of the id.
    span_segment = remainder.partition("/")[0]
    options = span_segment.split(";")

    # Only the exact flag counts as sampled. Anything else -- o=0, a value the
    # sender invented, no flag at all -- means the request was not selected.
    return TraceContext(
        trace_id=trace_id,
        span_id=options[0],
        sampled=len(options) > 1 and options[1] == _SAMPLED,
    )


# Resolved once: the value comes from the process environment, which does not
# change under a running service. Two threads racing here both read the same
# environment and store the same answer, so no lock is needed.
_project_id: str | None = None
_project_id_resolved = False


def project_id() -> str | None:
    """Returns the project the service runs in, or ``None`` if nothing names it.

    The first non-empty of ``ALIS_OS_PROJECT``, ``GOOGLE_CLOUD_PROJECT`` and
    ``GCLOUD_PROJECT`` wins. The answer is cached; call :func:`reset_project_id`
    after changing the environment.
    """
    global _project_id, _project_id_resolved

    if not _project_id_resolved:
        for name in _PROJECT_VARS:
            if value := os.environ.get(name):
                _project_id = value
                break
        _project_id_resolved = True
    return _project_id


def reset_project_id() -> None:
    """Forgets the cached project id, so the next read consults the environment."""
    global _project_id, _project_id_resolved

    _project_id = None
    _project_id_resolved = False


def trace_resource_name(trace_id: str, /) -> str | None:
    """Builds the full resource name a trace is correlated by.

    Args:
        trace_id: The trace id from the request header.

    Returns:
        ``projects/{project}/traces/{trace_id}``, or ``None`` when no
        environment variable names a project. A bare trace id correlates
        nothing on its own, so without a project there is no name to build.
    """
    project = project_id()
    return f"projects/{project}/traces/{trace_id}" if project else None
