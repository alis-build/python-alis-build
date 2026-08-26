"""The human-readable format used when nothing is going to parse the output.

On a developer's machine there is no agent reading the stream, so JSON is all
cost and no benefit: it is the severity and the message that matter, and a
colour carries the severity faster than a word does.

This layout is deliberately **not** a contract. It exists to be read by a
person, and it is free to change whenever a change makes it easier to read.
Nothing should parse it; parse the JSON format instead.
"""

from __future__ import annotations

import logging
import os
from typing import Final, TextIO

from ._context import current
from ._entry import record_fields
from ._levels import DEBUG, severity_for
from ._levels import Severity as _Severity

_RESET: Final = "\x1b[0m"
_LOCATION_COLOUR: Final = 34

# Chosen so severity reads at a glance without the eye stopping to parse a word:
# grey recedes, green and blue are calm, yellow and red escalate, and the two
# most severe invert to a filled block that is impossible to scroll past.
_COLOURS: Final = {
    _Severity.DEFAULT: 90,
    _Severity.DEBUG: 90,
    _Severity.INFO: 32,
    _Severity.NOTICE: 34,
    _Severity.WARNING: 33,
    _Severity.ERROR: 31,
    _Severity.CRITICAL: 91,
    _Severity.ALERT: 41,
    _Severity.EMERGENCY: 101,
}


def colour_wanted(stream: TextIO | None = None) -> bool:
    """Reports whether ANSI colour should be written to ``stream``.

    Colour is on for a terminal and off for anything else, so a redirected
    stream does not fill up with escape sequences. Three overrides apply, in
    order: ``ALOG_COLOR`` set to ``always`` or ``never`` decides outright,
    ``NO_COLOR`` set to anything turns it off, and ``TERM=dumb`` turns it off.

    Args:
        stream: The stream the output is headed for. ``None`` answers as though
            it were not a terminal, which is the safe assumption.

    Returns:
        Whether to colour the output.
    """
    override = os.environ.get("ALOG_COLOR", "").strip().lower()
    if override in {"always", "1", "true", "yes"}:
        return True
    if override in {"never", "0", "false", "no"}:
        return False

    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False

    return bool(stream is not None and getattr(stream, "isatty", None) and stream.isatty())


class LocalFormatter(logging.Formatter):
    """Formats a record as a coloured line for a person to read.

    Args:
        colour: Whether to write ANSI escapes. ``None``, the default, decides
            per :func:`colour_wanted` against the stream passed as ``stream``.
        stream: The stream the output is headed for, consulted only when
            ``colour`` is ``None``.
        source_location: Whether to name the file and line that logged.
            ``None``, the default, shows it whenever the record's own logger is
            enabled for ``DEBUG``, which is when someone is looking for it.
    """

    def __init__(
        self,
        *,
        colour: bool | None = None,
        stream: TextIO | None = None,
        source_location: bool | None = None,
    ) -> None:
        super().__init__()
        self._colour = colour_wanted(stream) if colour is None else colour
        self._source_location = source_location

    def format(self, record: logging.LogRecord) -> str:
        """Returns the record as one readable line, with any traceback beneath it."""
        severity = severity_for(record.levelno)
        parts = [self._paint(severity, _COLOURS[severity])]

        if self._wants_source_location(record):
            parts.append(self._paint(f"{record.pathname}:{record.lineno}", _LOCATION_COLOUR))

        parts.append(record.getMessage())

        if fields := self._fields(record):
            parts.append(fields)

        line = " ".join(parts)
        if trailer := self._trailer(record):
            line = f"{line}\n{trailer}"
        return line

    def _paint(self, text: str, colour: int) -> str:
        return f"\x1b[{colour}m{text}{_RESET}" if self._colour else text

    def _wants_source_location(self, record: logging.LogRecord) -> bool:
        if self._source_location is not None:
            return self._source_location
        return logging.getLogger(record.name).isEnabledFor(DEBUG)

    def _fields(self, record: logging.LogRecord) -> str:
        """Renders bound and per-call fields as trailing ``key=value`` pairs.

        Labels and the rest of the entry metadata are left out: they exist to be
        queried in Logs Explorer, and repeating them on every local line would
        bury the message they are attached to.
        """
        fields = {**current().fields, **record_fields(record)}
        return " ".join(f"{key}={value!r}" for key, value in fields.items())

    def _trailer(self, record: logging.LogRecord) -> str:
        parts = []
        if record.exc_info:
            parts.append(record.exc_text or self.formatException(record.exc_info))
        if record.stack_info:
            parts.append(self.formatStack(record.stack_info))
        return "\n".join(parts)
