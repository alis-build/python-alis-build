"""Structured logging for services on the Alis Build platform.

Cloud Logging's agents read a container's stdout and stderr and parse each line.
A line that happens to be a JSON object with the right keys becomes a structured
entry -- with a severity, a link to its trace, labels, and request metadata -- and
a line that is not becomes flat text with none of that. This package's job is to
make sure the lines are the first kind.

Nothing here calls the Logging API. The package only decides what a line looks
like; the platform does the ingesting.

Two output formats:

* **cloud** -- one JSON object per line, in the shape Cloud Logging parses.
* **local** -- ANSI-coloured, human-readable lines for development.

Which one is used is detected from the environment and can be overridden.

The core is dependency-free. The rest lives behind an extra:

======================== ============ =========================================
Module                   Extra        Purpose
======================== ============ =========================================
``alis.build.alog.asgi`` none         trace correlation for ASGI applications
``alis.build.alog.grpc`` ``grpc``     trace correlation for gRPC servers
======================== ============ =========================================
"""

from __future__ import annotations

from ._errors import (
    AlogError,
    ConfigurationError,
    MissingDependencyError,
)
from ._levels import (
    ALERT,
    CRITICAL,
    DEBUG,
    EMERGENCY,
    ERROR,
    INFO,
    NOTICE,
    NOTSET,
    WARNING,
    Severity,
    level_for,
    severity_for,
)

__version__ = "0.1.0"

__all__ = [
    "ALERT",
    "CRITICAL",
    "DEBUG",
    "EMERGENCY",
    "ERROR",
    "INFO",
    "NOTICE",
    "NOTSET",
    "WARNING",
    "AlogError",
    "ConfigurationError",
    "MissingDependencyError",
    "Severity",
    "__version__",
    "level_for",
    "severity_for",
]
