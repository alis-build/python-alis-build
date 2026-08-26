"""Reading the x-cloud-trace-context header."""

from __future__ import annotations

import pytest

from alis.build.alog import TRACE_HEADER, parse_trace_context
from alis.build.alog._trace import project_id, reset_project_id, trace_resource_name


def test_the_header_name_is_lowercase() -> None:
    """Both transports it arrives over look headers up case-insensitively in lower case."""
    assert TRACE_HEADER == "x-cloud-trace-context"


@pytest.mark.parametrize(
    ("header", "trace_id", "span_id", "sampled"),
    [
        pytest.param("abc123/456;o=1", "abc123", "456", True, id="full"),
        pytest.param("abc123/456;o=0", "abc123", "456", False, id="not_sampled"),
        pytest.param("abc123/456;o=2", "abc123", "456", False, id="only_o_1_counts"),
        pytest.param("abc123/456", "abc123", "456", False, id="no_flag"),
        pytest.param("abc123", "abc123", "", False, id="trace_only"),
        pytest.param("abc123/", "abc123", "", False, id="empty_span"),
        pytest.param("abc123/456;o=1;x=y", "abc123", "456", True, id="trailing_options"),
        pytest.param("abc/def/ghi;o=1", "abc", "def", False, id="a_span_never_holds_a_slash"),
    ],
)
def test_a_header_is_read_into_its_three_parts(
    header: str, trace_id: str, span_id: str, sampled: bool
) -> None:
    context = parse_trace_context(header)

    assert context is not None
    assert (context.trace_id, context.span_id, context.sampled) == (trace_id, span_id, sampled)


@pytest.mark.parametrize(
    "header",
    [
        pytest.param("", id="empty"),
        pytest.param("/456;o=1", id="no_trace_id"),
        pytest.param("/", id="only_a_slash"),
    ],
)
def test_a_header_naming_no_trace_reads_as_nothing(header: str) -> None:
    """A trace id is the only part that correlates anything, so without one there is no context."""
    assert parse_trace_context(header) is None


@pytest.mark.parametrize(
    "variable",
    [
        pytest.param("ALIS_OS_PROJECT", id="platform"),
        pytest.param("GOOGLE_CLOUD_PROJECT", id="google"),
        pytest.param("GCLOUD_PROJECT", id="legacy_google"),
    ],
)
def test_any_of_the_three_variables_names_the_project(
    variable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(variable, "p")
    reset_project_id()

    assert project_id() == "p"


def test_the_first_non_empty_variable_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALIS_OS_PROJECT", "")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "second")
    monkeypatch.setenv("GCLOUD_PROJECT", "third")
    reset_project_id()

    assert project_id() == "second"


def test_the_project_is_read_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """It comes from the environment, which does not change under a running service."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "first")
    reset_project_id()
    assert project_id() == "first"

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "second")
    assert project_id() == "first"

    reset_project_id()
    assert project_id() == "second"


def test_a_resource_name_needs_a_project(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_project_id()
    assert trace_resource_name("abc") is None

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    reset_project_id()
    assert trace_resource_name("abc") == "projects/p/traces/abc"
