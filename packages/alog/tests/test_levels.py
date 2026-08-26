"""Severity levels and the mapping from level numbers to severities."""

from __future__ import annotations

import logging

import pytest

from alis.build import alog
from alis.build.alog import Severity, level_for, severity_for


def test_the_extra_levels_are_registered_by_name() -> None:
    assert logging.getLevelName(alog.NOTICE) == "NOTICE"
    assert logging.getLevelName(alog.ALERT) == "ALERT"
    assert logging.getLevelName(alog.EMERGENCY) == "EMERGENCY"


def test_levels_stay_monotonic_with_the_built_in_ones() -> None:
    """The whole point of the chosen numbers: ordinary level comparisons work."""
    assert (
        alog.NOTSET
        < alog.DEBUG
        < alog.INFO
        < alog.NOTICE
        < alog.WARNING
        < alog.ERROR
        < alog.CRITICAL
        < alog.ALERT
        < alog.EMERGENCY
    )


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        pytest.param(alog.EMERGENCY, Severity.EMERGENCY, id="emergency"),
        pytest.param(alog.ALERT, Severity.ALERT, id="alert"),
        pytest.param(alog.CRITICAL, Severity.CRITICAL, id="critical"),
        pytest.param(alog.ERROR, Severity.ERROR, id="error"),
        pytest.param(alog.WARNING, Severity.WARNING, id="warning"),
        pytest.param(alog.NOTICE, Severity.NOTICE, id="notice"),
        pytest.param(alog.INFO, Severity.INFO, id="info"),
        pytest.param(alog.DEBUG, Severity.DEBUG, id="debug"),
        pytest.param(alog.NOTSET, Severity.DEFAULT, id="notset_is_default"),
    ],
)
def test_each_level_maps_to_its_own_severity(level: int, expected: Severity) -> None:
    assert severity_for(level) == expected


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        pytest.param(35, Severity.WARNING, id="between_warning_and_error"),
        pytest.param(21, Severity.INFO, id="just_above_info"),
        pytest.param(5, Severity.DEFAULT, id="below_debug"),
        pytest.param(1000, Severity.EMERGENCY, id="above_everything"),
    ],
)
def test_an_unregistered_level_lands_on_the_severity_below_it(
    level: int, expected: Severity
) -> None:
    assert severity_for(level) == expected


def test_a_third_party_libraries_plain_info_is_reported_as_info() -> None:
    """Nothing about a foreign record says 'alog', so the number has to carry it."""
    record = logging.LogRecord("httpx", logging.INFO, "x.py", 1, "sent", None, None)
    assert severity_for(record.levelno) == Severity.INFO


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("DEBUG", alog.DEBUG, id="name"),
        pytest.param("notice", alog.NOTICE, id="lowercase_name"),
        pytest.param("  Warning  ", alog.WARNING, id="surrounding_space"),
        pytest.param("20", alog.INFO, id="number_as_string"),
        pytest.param(alog.ERROR, alog.ERROR, id="number"),
        pytest.param(0, alog.NOTSET, id="zero"),
    ],
)
def test_level_for_accepts_names_and_numbers(value: int | str, expected: int) -> None:
    assert level_for(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("chatty", id="not_a_level"),
        pytest.param("", id="empty"),
        pytest.param("-4", id="negative_string"),
        pytest.param(-4, id="negative_number"),
    ],
)
def test_an_unreadable_level_raises_rather_than_falling_back(value: int | str) -> None:
    """A service that silently logs at the wrong level looks healthy until it matters."""
    with pytest.raises(alog.ConfigurationError):
        level_for(value)


def test_configuration_error_is_also_a_value_error() -> None:
    assert issubclass(alog.ConfigurationError, ValueError)


def test_missing_dependency_error_is_also_an_import_error() -> None:
    assert issubclass(alog.MissingDependencyError, ImportError)


def test_every_error_shares_one_root() -> None:
    assert issubclass(alog.ConfigurationError, alog.AlogError)
    assert issubclass(alog.MissingDependencyError, alog.AlogError)
