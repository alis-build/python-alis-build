"""Deciding how logging is set up, and the shortcuts that use it.

Importing this package changes nothing about logging. A library that installs
handlers on import fights every other way of configuring logging -- a
``dictConfig`` call, a server's own setup, a test runner's capture -- and makes
import order matter. :func:`configure` is the explicit opt-in instead.

What :func:`configure` does is take over the root logger, which is what makes
*every* library's log lines come out in the right format rather than only the
ones this package's own shortcuts write. That is the whole reason to prefer it
over a private logger.

The shortcuts -- :func:`info` and friends -- work without it. They attach a
handler to this package's own logger the first time one is needed, so a script
that only wants ``alog.info(...)`` gets correct output with no setup, and a
service that calls :func:`configure` gets correct output everywhere.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from enum import StrEnum
from typing import Any, Final, TextIO

from ._cloud import CloudLoggingFormatter
from ._errors import ConfigurationError
from ._levels import (
    ALERT,
    CRITICAL,
    DEBUG,
    EMERGENCY,
    ERROR,
    INFO,
    NOTICE,
    WARNING,
    level_for,
)
from ._local import LocalFormatter
from ._trace import reset_project_id

LOGGER_NAME: Final = "alis.build.alog"

# Set by the runtimes the platform deploys onto: the first two by Cloud Run for
# a service and a job, the third inside a Kubernetes pod. Any of them means
# something is collecting stdout and parsing it.
_RUNTIME_VARS: Final = ("K_SERVICE", "CLOUD_RUN_JOB", "KUBERNETES_SERVICE_HOST")

_LEVEL_VAR: Final = "ALOG_LEVEL"
_MODE_VAR: Final = "ALOG_MODE"

_lock = threading.Lock()

# The handler the shortcuts install for themselves, kept so configure() can take
# it back out. Leaving it in place would write every line twice: once here, and
# once again on the root handler this logger propagates to.
_shortcut_handler: logging.Handler | None = None


class Mode(StrEnum):
    """Which of the two output formats to write."""

    CLOUD = "cloud"
    """One JSON object per line, for a runtime that parses stdout."""

    LOCAL = "local"
    """Coloured, human-readable lines, for a developer's terminal."""


def detect_mode() -> Mode:
    """Returns the format the current environment calls for.

    ``ALOG_MODE`` decides if it is set. Otherwise any of ``K_SERVICE``,
    ``CLOUD_RUN_JOB`` or ``KUBERNETES_SERVICE_HOST`` means a runtime is
    collecting and parsing stdout, and anything else is taken to be a terminal.

    Raises:
        ConfigurationError: If ``ALOG_MODE`` names neither format.
    """
    if override := os.environ.get(_MODE_VAR, "").strip().lower():
        try:
            return Mode(override)
        except ValueError:
            raise ConfigurationError(
                f"{_MODE_VAR}={override!r} names no mode; expected 'cloud' or 'local'"
            ) from None

    return Mode.CLOUD if any(os.environ.get(name) for name in _RUNTIME_VARS) else Mode.LOCAL


def _mode_for(mode: Mode | str, /) -> Mode:
    """Resolves a mode name in any casing, raising the package error if it names none."""
    try:
        return Mode(str(mode).strip().lower())
    except ValueError:
        raise ConfigurationError(f"{mode!r} names no mode; expected 'cloud' or 'local'") from None


def detect_level(mode: Mode, /) -> int:
    """Returns the minimum level the current environment calls for.

    ``ALOG_LEVEL`` decides if it is set. Otherwise local output starts at
    ``DEBUG``, because on a developer's machine the volume is not a problem and
    the detail is the point, and cloud output starts at ``INFO``, because there
    it is the other way around.

    Args:
        mode: The format in use, which sets the default.

    Raises:
        ConfigurationError: If ``ALOG_LEVEL`` names no level.
    """
    if override := os.environ.get(_LEVEL_VAR, "").strip():
        return level_for(override)
    return DEBUG if mode is Mode.LOCAL else INFO


def formatter_for(mode: Mode, /, stream: TextIO | None = None) -> logging.Formatter:
    """Builds the formatter a mode calls for.

    Args:
        mode: The format to write.
        stream: The stream the output is headed for, which local mode consults
            to decide whether colour is wanted.
    """
    if mode is Mode.LOCAL:
        return LocalFormatter(stream=stream)
    return CloudLoggingFormatter()


def configure(
    *,
    level: int | str | None = None,
    mode: Mode | str | None = None,
    stream: TextIO | None = None,
    logger: logging.Logger | None = None,
) -> logging.Handler:
    """Sets logging up for the whole process, and returns the handler installed.

    Replaces the target logger's handlers rather than adding to them, so calling
    this twice does not double every line. Everything that logs through the
    standard library -- this package's shortcuts, the service's own loggers, and
    every third-party library it depends on -- comes out in the chosen format.

    Args:
        level: The minimum level, as a name or a number. Defaults to whatever
            the environment calls for.
        mode: The output format. Defaults to whatever the environment calls for.
        stream: Where to write. Defaults to standard error, which is where a
            container runtime collects from and where log output belongs so that
            a program's actual output stays clean on standard out.
        logger: The logger to configure. Defaults to the root logger, which is
            what makes the format apply to every library rather than only to
            this one.

    Returns:
        The handler that was installed, so a caller can add a filter to it.

    Raises:
        ConfigurationError: If ``level`` or ``mode`` names nothing, or if the
            environment variables do.
    """
    global _shortcut_handler

    target = logging.getLogger() if logger is None else logger
    destination = sys.stderr if stream is None else stream

    # The environment is read fresh, so a test or a startup path that changed a
    # variable gets the change rather than an answer cached before it ran.
    reset_project_id()
    resolved_mode = detect_mode() if mode is None else _mode_for(mode)
    resolved_level = detect_level(resolved_mode) if level is None else level_for(level)

    handler = logging.StreamHandler(destination)
    handler.setFormatter(formatter_for(resolved_mode, destination))

    with _lock:
        if _shortcut_handler is not None:
            shortcut = logging.getLogger(LOGGER_NAME)
            shortcut.removeHandler(_shortcut_handler)
            shortcut.setLevel(logging.NOTSET)
            _shortcut_handler = None

        for existing in list(target.handlers):
            target.removeHandler(existing)
        target.addHandler(handler)
        target.setLevel(resolved_level)

    return handler


def set_level(level: int | str, /, logger: logging.Logger | None = None) -> None:
    """Changes the minimum level without touching anything else.

    Args:
        level: The new minimum, as a name or a number.
        logger: The logger to change. Defaults to the root logger.

    Raises:
        ConfigurationError: If ``level`` names nothing.
    """
    (logging.getLogger() if logger is None else logger).setLevel(level_for(level))


def logger() -> logging.Logger:
    """Returns the logger the shortcuts write through.

    The first call attaches a handler to it, so the shortcuts produce correct
    output in a process that never called :func:`configure`. Once
    :func:`configure` has run, the root handler it installed does the work and
    this one steps aside.
    """
    global _shortcut_handler

    target = logging.getLogger(LOGGER_NAME)
    # Fast path once either side has a handler: no lock on the logging hot path.
    if _shortcut_handler is None and not logging.getLogger().handlers:
        with _lock:
            if _shortcut_handler is None and not logging.getLogger().handlers:
                mode = detect_mode()
                handler = logging.StreamHandler(sys.stderr)
                handler.setFormatter(formatter_for(mode, sys.stderr))
                target.addHandler(handler)
                target.setLevel(detect_level(mode))
                _shortcut_handler = handler
    return target


def log(level: int, message: str, /, *args: Any, **kwargs: Any) -> None:
    """Writes a line at any level, including one this package does not name.

    Args:
        level: The level to write at.
        message: A ``%``-style format string. Leave the arguments to be
            interpolated rather than doing it yourself: for a line the level
            discards, the interpolation never has to happen at all.
        *args: Arguments for the format string.
        **kwargs: Passed through to the standard library, so ``exc_info``,
            ``stack_info`` and ``extra`` all work as usual.
    """
    target = logger()
    if not target.isEnabledFor(level):
        return
    kwargs.setdefault("stacklevel", 2)
    target.log(level, message, *args, **kwargs)


def debug(message: str, /, *args: Any, **kwargs: Any) -> None:
    """Writes debug or trace information."""
    kwargs.setdefault("stacklevel", 3)
    log(DEBUG, message, *args, **kwargs)


def info(message: str, /, *args: Any, **kwargs: Any) -> None:
    """Writes routine information, such as ongoing status or performance."""
    kwargs.setdefault("stacklevel", 3)
    log(INFO, message, *args, **kwargs)


def notice(message: str, /, *args: Any, **kwargs: Any) -> None:
    """Writes a normal but significant event: start up, shut down, a config change."""
    kwargs.setdefault("stacklevel", 3)
    log(NOTICE, message, *args, **kwargs)


def warning(message: str, /, *args: Any, **kwargs: Any) -> None:
    """Writes something that might cause problems."""
    kwargs.setdefault("stacklevel", 3)
    log(WARNING, message, *args, **kwargs)


def error(message: str, /, *args: Any, **kwargs: Any) -> None:
    """Writes something likely to cause problems."""
    kwargs.setdefault("stacklevel", 3)
    log(ERROR, message, *args, **kwargs)


def critical(message: str, /, *args: Any, **kwargs: Any) -> None:
    """Writes something causing a more severe problem or an outage."""
    kwargs.setdefault("stacklevel", 3)
    log(CRITICAL, message, *args, **kwargs)


def alert(message: str, /, *args: Any, **kwargs: Any) -> None:
    """Writes something a person must act on immediately."""
    kwargs.setdefault("stacklevel", 3)
    log(ALERT, message, *args, **kwargs)


def emergency(message: str, /, *args: Any, **kwargs: Any) -> None:
    """Writes that one or more systems are unusable."""
    kwargs.setdefault("stacklevel", 3)
    log(EMERGENCY, message, *args, **kwargs)


def exception(message: str, /, *args: Any, **kwargs: Any) -> None:
    """Writes an error with the traceback of the exception being handled.

    Call it from inside an ``except`` block. The traceback lands on the key
    Error Reporting groups by, so an exception logged any other way is one that
    never gets counted.
    """
    kwargs.setdefault("exc_info", True)
    kwargs.setdefault("stacklevel", 3)
    log(ERROR, message, *args, **kwargs)


def fatal(message: str, /, *args: Any, **kwargs: Any) -> None:
    """Writes a critical line and then ends the program.

    Raises :class:`SystemExit` rather than exiting outright, so ``finally``
    blocks, context managers and ``atexit`` handlers still get to run. Catching
    it to carry on is possible but almost certainly a mistake.

    Raises:
        SystemExit: Always, with status ``1``.
    """
    kwargs.setdefault("stacklevel", 3)
    log(CRITICAL, message, *args, **kwargs)
    raise SystemExit(1)


def reset() -> None:
    """Forgets the handler the shortcuts installed for themselves.

    Only useful to a test that has already torn the handler off the logger by
    hand; without this the shortcuts would believe they are still set up.
    """
    global _shortcut_handler

    with _lock:
        _shortcut_handler = None
