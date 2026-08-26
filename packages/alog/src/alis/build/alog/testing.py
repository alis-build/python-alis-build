"""Helpers for testing code that logs through this package.

Importing this module pulls in nothing beyond the core, so it is safe to use
from a test suite with none of the extras installed.

Logging configuration is process-wide, and almost all of it is reached through
module-level singletons: the root logger's handlers and level, every named
logger's settings, and the global disable threshold. A test that changes any of
them without putting it back silently changes the result of every test that runs
afterwards, usually as a failure somewhere unrelated.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from ._config import LOGGER_NAME, Mode, formatter_for
from ._config import reset as _reset_shortcut
from ._levels import DEBUG

__all__ = ["Captured", "capture", "fresh_logging_config"]


@dataclass(slots=True)
class Captured:
    """The lines written while a :func:`capture` block was open."""

    lines: list[str] = field(default_factory=list)
    """Each line as it was formatted, in the order it was written."""

    @property
    def entries(self) -> list[dict[str, Any]]:
        """The lines parsed back into objects.

        Only meaningful for a capture in :attr:`Mode.CLOUD`, which is the
        default -- local lines are not JSON and are not meant to be parsed.

        Raises:
            json.JSONDecodeError: If a captured line is not JSON.
        """
        return [json.loads(line) for line in self.lines]

    @property
    def messages(self) -> list[str]:
        """Just the messages, for a test that does not care about the rest."""
        return [entry["message"] for entry in self.entries]


# Handlers and filters are both wider than their obvious types -- a filter may
# be a plain callable -- so they are carried through as-is rather than narrowed.
_Settings = tuple[int, bool, list[Any], list[Any]]


class _ListHandler(logging.Handler):
    __slots__ = ("_captured",)

    def __init__(self, captured: Captured) -> None:
        super().__init__()
        self._captured = captured

    def emit(self, record: logging.LogRecord) -> None:
        self._captured.lines.append(self.format(record))


def _snapshot() -> dict[str, _Settings]:
    manager = logging.Logger.manager
    return {
        name: (item.level, item.propagate, list(item.handlers), list(item.filters))
        for name, item in manager.loggerDict.items()
        if isinstance(item, logging.Logger)
    }


@contextmanager
def fresh_logging_config() -> Iterator[None]:
    """Puts logging configuration back the way it was when the block ends.

    Restores the root logger's handlers, level and filters; every named logger's
    settings; the global disable threshold; and this package's own record of
    whether its shortcuts have set themselves up. Loggers created inside the
    block are dropped.

    A logger that existed before the block and is still around after it is
    restored by value, so a test may reconfigure anything it likes. Use it as an
    autouse fixture::

        @pytest.fixture(autouse=True)
        def _logging():
            with fresh_logging_config():
                yield

    Yields:
        Nothing. The restoration happens on the way out.
    """
    root = logging.getLogger()
    previous_root = (root.level, list(root.handlers), list(root.filters))
    previous_loggers = _snapshot()
    previous_disable = logging.Logger.manager.disable
    previous_keys = set(logging.Logger.manager.loggerDict)

    try:
        yield
    finally:
        manager = logging.Logger.manager
        for name in set(manager.loggerDict) - previous_keys:
            del manager.loggerDict[name]

        for name, (level, propagate, handlers, filters) in previous_loggers.items():
            item = manager.loggerDict.get(name)
            if isinstance(item, logging.Logger):
                item.level = level
                item.propagate = propagate
                item.handlers = handlers
                item.filters = filters

        root.level, root.handlers, root.filters = previous_root
        manager.disable = previous_disable
        _reset_shortcut()


@contextmanager
def capture(
    *,
    mode: Mode = Mode.CLOUD,
    level: int = DEBUG,
    logger: logging.Logger | None = None,
) -> Iterator[Captured]:
    """Collects the lines written inside the block instead of printing them.

    Nothing else about logging is disturbed, and everything is put back on the
    way out. Prefer this to pytest's own capture when the assertion is about the
    formatted line rather than the message, since the format is the part worth
    testing.

    Args:
        mode: The format to capture in. Cloud is the default, because a test
            asserting on structure wants the JSON.
        level: The minimum level to capture. Everything, by default.
        logger: The logger to capture from. Defaults to the root logger, which
            catches every library too.

    Yields:
        The lines, filling as they are written.

    Example:
        >>> from alis.build import alog
        >>> from alis.build.alog.testing import capture
        >>> with capture() as captured:
        ...     alog.info("hello")
        >>> captured.messages
        ['hello']
    """
    target = logging.getLogger() if logger is None else logger
    captured = Captured()

    handler = _ListHandler(captured)
    handler.setFormatter(formatter_for(mode))

    previous_handlers = list(target.handlers)
    previous_level = target.level
    shortcut = logging.getLogger(LOGGER_NAME)
    previous_shortcut_handlers = list(shortcut.handlers)

    # The shortcuts install a handler of their own on first use. Left in place
    # it would print the lines as well as capturing them.
    shortcut.handlers = []
    target.handlers = [handler]
    target.setLevel(level)
    try:
        yield captured
    finally:
        target.handlers = previous_handlers
        target.level = previous_level
        shortcut.handlers = previous_shortcut_handlers
