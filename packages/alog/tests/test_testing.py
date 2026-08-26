"""The helpers this package ships for testing code that logs."""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator

import pytest

from alis.build import alog
from alis.build.alog import Mode
from alis.build.alog.testing import capture, fresh_logging_config


@pytest.fixture(autouse=True)
def fresh() -> Iterator[None]:
    with fresh_logging_config():
        yield


def test_a_reconfigured_root_is_put_back() -> None:
    root = logging.getLogger()
    before = (root.level, list(root.handlers))

    with fresh_logging_config():
        alog.configure(mode=Mode.CLOUD, level=alog.EMERGENCY, stream=io.StringIO())

    assert (root.level, list(root.handlers)) == before


def test_a_logger_created_inside_the_block_is_dropped() -> None:
    """Otherwise its settings outlive the test that made them."""
    with fresh_logging_config():
        logging.getLogger("made.up.inside").propagate = False

    assert "made.up.inside" not in logging.Logger.manager.loggerDict


def test_a_pre_existing_logger_is_restored_by_value() -> None:
    existing = logging.getLogger("already.here")
    existing.setLevel(alog.WARNING)

    with fresh_logging_config():
        existing.setLevel(alog.DEBUG)
        existing.propagate = False

    assert existing.level == alog.WARNING
    assert existing.propagate is True


def test_the_global_disable_threshold_is_restored() -> None:
    with fresh_logging_config():
        logging.disable(alog.CRITICAL)

    assert logging.Logger.manager.disable == 0


def test_capture_collects_the_formatted_lines() -> None:
    with capture() as captured:
        alog.info("hello")
        alog.warning("careful")

    assert captured.messages == ["hello", "careful"]
    assert [entry["severity"] for entry in captured.entries] == ["INFO", "WARNING"]


def test_capture_sees_other_libraries_too() -> None:
    with capture() as captured:
        logging.getLogger("httpx").info("sent a request")

    assert captured.messages == ["sent a request"]


def test_capture_asserts_on_the_format_not_just_the_message() -> None:
    """What pytest's own caplog cannot do: the formatted line is the thing under test."""
    with alog.bind(labels={"route": "/v1"}), capture() as captured:
        alog.info("hello")

    assert captured.entries[0]["logging.googleapis.com/labels"] == {"route": "/v1"}


def test_capture_can_take_the_local_format() -> None:
    with capture(mode=Mode.LOCAL, level=alog.INFO) as captured:
        alog.info("hello")

    assert captured.lines == ["INFO hello"]


def test_capturing_everything_turns_source_locations_on() -> None:
    """Capture defaults to DEBUG, and a source location is what DEBUG buys you."""
    with capture(mode=Mode.LOCAL) as captured:
        alog.info("hello")

    assert captured.lines[0].startswith("INFO ")
    assert __file__ in captured.lines[0]


def test_capture_respects_its_own_level() -> None:
    with capture(level=alog.ERROR) as captured:
        alog.info("quiet")
        alog.error("loud")

    assert captured.messages == ["loud"]


def test_capture_does_not_print_as_well_as_collect() -> None:
    """The shortcut handler, if it were left in place, would do exactly that."""
    stream = io.StringIO()
    alog.configure(mode=Mode.CLOUD, stream=stream)

    with capture() as captured:
        alog.info("hello")

    assert captured.messages == ["hello"]
    assert stream.getvalue() == ""


def test_capture_puts_everything_back() -> None:
    stream = io.StringIO()
    alog.configure(mode=Mode.CLOUD, stream=stream)
    before = list(logging.getLogger().handlers)

    with capture():
        alog.info("inside")

    alog.info("after")
    assert list(logging.getLogger().handlers) == before
    assert "after" in stream.getvalue()
