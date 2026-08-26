"""Writing a log record as the JSON object Cloud Logging parses."""

from __future__ import annotations

import json
import logging
from typing import Any

from ._context import current
from ._entry import build_entry
from ._levels import DEBUG


def _stringify(value: object) -> str:
    """Renders a value the encoder does not know how to write.

    A logging call is the wrong place to raise. Losing the line -- and getting a
    handler error in its place -- is worse than recording the value's
    ``repr``-ish form and moving on.
    """
    return str(value)


class CloudLoggingFormatter(logging.Formatter):
    """Formats a record as one JSON object, in the shape Cloud Logging parses.

    Install it on whatever handler writes to stdout or stderr and the platform
    does the rest; nothing here talks to the Logging API.

    Args:
        source_location: Whether to name the file, line and function that
            logged. ``None``, the default, includes it whenever the record's own
            logger is enabled for ``DEBUG``, so source locations appear exactly
            when someone has turned debugging on. ``True`` and ``False`` force
            it either way. It costs nothing to gather -- the record already
            carries all three -- only space on the line.
    """

    def __init__(self, *, source_location: bool | None = None) -> None:
        super().__init__()
        self._source_location = source_location

    def format(self, record: logging.LogRecord) -> str:
        """Returns the record as a single line of JSON.

        Non-ASCII characters are written as themselves rather than escaped: the
        line is UTF-8 and the parser on the other end reads UTF-8, so escaping
        would only make it longer and harder to read.
        """
        return json.dumps(
            build_entry(
                record,
                current(),
                source_location=self._wants_source_location(record),
                stack_trace=self._stack_trace(record),
            ),
            separators=(",", ":"),
            ensure_ascii=False,
            default=_stringify,
        )

    def _wants_source_location(self, record: logging.LogRecord) -> bool:
        if self._source_location is not None:
            return self._source_location
        return logging.getLogger(record.name).isEnabledFor(DEBUG)

    def _stack_trace(self, record: logging.LogRecord) -> str:
        """Renders whatever traceback the record carries.

        Error Reporting groups errors by this key, so an exception logged
        without one is an error that never gets counted.
        """
        parts: list[Any] = []
        if record.exc_info:
            parts.append(record.exc_text or self.formatException(record.exc_info))
        if record.stack_info:
            parts.append(self.formatStack(record.stack_info))
        return "\n".join(parts)
