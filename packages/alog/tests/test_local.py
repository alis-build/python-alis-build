"""The human-readable local format.

The layout itself is not a contract and is free to change. What is pinned here
is that colour is suppressed everywhere it should be, since a stray escape
sequence in a redirected stream or a CI log is a real problem.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable

import pytest

from alis.build import alog
from alis.build.alog._local import LocalFormatter, colour_wanted

Record = Callable[..., logging.LogRecord]


class FakeTty(io.StringIO):
    """A stream that claims to be a terminal."""

    def isatty(self) -> bool:
        return True


def line(record: logging.LogRecord) -> str:
    return LocalFormatter(colour=True, source_location=False).format(record)


@pytest.mark.parametrize(
    ("level", "colour"),
    [
        pytest.param(alog.DEBUG, 90, id="debug_recedes"),
        pytest.param(alog.INFO, 32, id="info"),
        pytest.param(alog.NOTICE, 34, id="notice"),
        pytest.param(alog.WARNING, 33, id="warning"),
        pytest.param(alog.ERROR, 31, id="error"),
        pytest.param(alog.CRITICAL, 91, id="critical"),
        pytest.param(alog.ALERT, 41, id="alert_inverts"),
        pytest.param(alog.EMERGENCY, 101, id="emergency_inverts"),
    ],
)
def test_each_severity_gets_its_own_colour(record: Record, level: int, colour: int) -> None:
    severity = alog.severity_for(level)
    assert line(record("hello", level=level)) == f"\x1b[{colour}m{severity}\x1b[0m hello"


def test_without_colour_the_line_is_plain_text(record: Record) -> None:
    formatted = LocalFormatter(colour=False, source_location=False).format(record("hello"))
    assert formatted == "INFO hello"
    assert "\x1b" not in formatted


def test_a_source_location_is_shown_when_asked_for(record: Record) -> None:
    formatted = LocalFormatter(colour=False, source_location=True).format(record("hello"))
    assert formatted == "INFO /app/handler.py:42 hello"


def test_fields_trail_the_message(record: Record) -> None:
    with alog.bind(request_id="r-1"):
        formatted = LocalFormatter(colour=False, source_location=False).format(
            record("hello", user="u-1")
        )
    assert formatted == "INFO hello request_id='r-1' user='u-1'"


def test_entry_metadata_is_not_repeated_on_every_line(record: Record) -> None:
    """Labels exist to be queried, not read; repeating them would bury the message."""
    with alog.bind(labels={"route": "/v1"}, insert_id="i-1"):
        formatted = LocalFormatter(colour=False, source_location=False).format(record("hello"))
    assert formatted == "INFO hello"


def test_a_traceback_follows_on_its_own_lines(record: Record) -> None:
    import sys

    try:
        raise ValueError("boom")
    except ValueError:
        formatted = LocalFormatter(colour=False, source_location=False).format(
            record("failed", level=alog.ERROR, exc_info=sys.exc_info())
        )

    head, _, trailer = formatted.partition("\n")
    assert head == "ERROR failed"
    assert trailer.startswith("Traceback")
    assert "ValueError: boom" in trailer


# --------------------------------------------------------------------------
# when colour is suppressed
# --------------------------------------------------------------------------


def test_colour_is_on_for_a_terminal() -> None:
    assert colour_wanted(FakeTty()) is True


def test_colour_is_off_for_a_redirected_stream() -> None:
    """Escape sequences in a piped stream or a CI log are noise at best."""
    assert colour_wanted(io.StringIO()) is False


def test_colour_is_off_when_there_is_no_stream_to_judge() -> None:
    assert colour_wanted(None) is False


@pytest.mark.parametrize(
    "value",
    [pytest.param("1", id="one"), pytest.param("", id="empty_still_counts")],
)
def test_no_color_switches_colour_off_whatever_it_is_set_to(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The convention is that the variable being present at all is the signal."""
    monkeypatch.setenv("NO_COLOR", value)
    assert colour_wanted(FakeTty()) is False


def test_a_dumb_terminal_gets_no_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "dumb")
    assert colour_wanted(FakeTty()) is False


@pytest.mark.parametrize(
    ("value", "wanted"),
    [
        pytest.param("always", True, id="always"),
        pytest.param("never", False, id="never"),
        pytest.param("1", True, id="one"),
        pytest.param("0", False, id="zero"),
        pytest.param("ALWAYS", True, id="case_insensitive"),
    ],
)
def test_alog_color_overrides_everything(
    value: str, wanted: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("ALOG_COLOR", value)

    assert colour_wanted(io.StringIO()) is wanted


def test_an_unrecognised_alog_color_falls_through_to_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALOG_COLOR", "maybe")
    assert colour_wanted(FakeTty()) is True
