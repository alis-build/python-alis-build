"""Severity levels, and how they line up with the standard library's.

Cloud Logging recognises nine severities. The standard library ships five, so
NOTICE, ALERT and EMERGENCY are registered here as real levels rather than
folded into their neighbours: registering them keeps ``setLevel`` and the
ordinary level comparisons working, and keeps all nine reachable for log-based
alerting.

The numbers are chosen to stay monotonic with the built-in ones::

    NOTSET 0 · DEBUG 10 · INFO 20 · NOTICE 25 · WARNING 30
    ERROR 40 · CRITICAL 50 · ALERT 60 · EMERGENCY 70

Importing this module calls :func:`logging.addLevelName`, which writes to a
process-global registry. That is unavoidable -- it is the only way a level
renders as ``NOTICE`` rather than ``Level 25`` -- but it is idempotent and adds
names rather than replacing any.

Severity is then derived from whatever number a record carries, so a
third-party library logging at plain ``INFO`` gets severity ``INFO`` without
knowing this package exists.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Final

from ._errors import ConfigurationError

NOTSET: Final = logging.NOTSET
DEBUG: Final = logging.DEBUG
INFO: Final = logging.INFO
NOTICE: Final = 25
WARNING: Final = logging.WARNING
ERROR: Final = logging.ERROR
CRITICAL: Final = logging.CRITICAL
ALERT: Final = 60
EMERGENCY: Final = 70

logging.addLevelName(NOTICE, "NOTICE")
logging.addLevelName(ALERT, "ALERT")
logging.addLevelName(EMERGENCY, "EMERGENCY")


class Severity(StrEnum):
    """A Cloud Logging severity, as it appears in the ``severity`` key."""

    DEFAULT = "DEFAULT"
    """No severity was assigned. Anything below ``DEBUG`` lands here."""

    DEBUG = "DEBUG"
    """Debug or trace information."""

    INFO = "INFO"
    """Routine information, such as ongoing status or performance."""

    NOTICE = "NOTICE"
    """Normal but significant events: start up, shut down, a config change."""

    WARNING = "WARNING"
    """Events that might cause problems."""

    ERROR = "ERROR"
    """Events likely to cause problems."""

    CRITICAL = "CRITICAL"
    """Events causing more severe problems or outages."""

    ALERT = "ALERT"
    """A person must take action immediately."""

    EMERGENCY = "EMERGENCY"
    """One or more systems are unusable."""


# Descending, so an unregistered level lands on the nearest severity at or below
# it rather than falling through to DEFAULT. A library logging at 35 gets
# WARNING, which is what someone choosing 35 meant.
_THRESHOLDS: Final = (
    (EMERGENCY, Severity.EMERGENCY),
    (ALERT, Severity.ALERT),
    (CRITICAL, Severity.CRITICAL),
    (ERROR, Severity.ERROR),
    (WARNING, Severity.WARNING),
    (NOTICE, Severity.NOTICE),
    (INFO, Severity.INFO),
    (DEBUG, Severity.DEBUG),
)


def severity_for(level: int, /) -> Severity:
    """Returns the severity a record at ``level`` is reported under.

    Args:
        level: Any level number, registered or not.

    Returns:
        The severity at or below ``level``, or :attr:`Severity.DEFAULT` for
        anything below ``DEBUG``.
    """
    for threshold, severity in _THRESHOLDS:
        if level >= threshold:
            return severity
    return Severity.DEFAULT


def level_for(value: int | str, /) -> int:
    """Resolves a level name or number to a level number.

    Args:
        value: A level number, or a level name in any case (``"debug"``,
            ``"NOTICE"``). A string of digits is read as a number.

    Returns:
        The level number.

    Raises:
        ConfigurationError: If the string names no level, or the number is
            negative. Levels start at ``0``, so a negative one is almost always
            a number meant for a different scale.
    """
    if isinstance(value, int):
        number = value
    else:
        text = value.strip()
        try:
            number = int(text)
        except ValueError:
            named = logging.getLevelNamesMapping().get(text.upper())
            if named is None:
                raise ConfigurationError(
                    f"{value!r} names no level; expected one of "
                    f"{', '.join(s.name for s in Severity)}, or a number"
                ) from None
            number = named

    if number < 0:
        raise ConfigurationError(f"{value!r} is not a level; levels start at 0 (NOTSET)")
    return number
