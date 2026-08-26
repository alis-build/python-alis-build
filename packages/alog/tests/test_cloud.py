"""The JSON line Cloud Logging parses.

Every expected string here is the exact line the format calls for, captured
character by character. These keys are what the platform's agents look for; a
change that breaks one of these silently downgrades a service's logs to
unparsed text.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

import pytest

from alis.build import alog
from alis.build.alog import CloudLoggingFormatter, HttpRequest, LogOperation

TIME = '"time":"2025-08-26T12:00:00.123456Z"'

Record = Callable[..., logging.LogRecord]


def line(record: logging.LogRecord) -> str:
    return CloudLoggingFormatter(source_location=False).format(record)


# --------------------------------------------------------------------------
# the basic shape
# --------------------------------------------------------------------------


def test_a_plain_line_carries_message_severity_and_time(record: Record) -> None:
    assert line(record("server started")) == (
        f'{{"message":"server started","severity":"INFO",{TIME}}}'
    )


def test_an_empty_message_is_still_written(record: Record) -> None:
    """``message`` is not optional: a line without one has no payload at all."""
    assert line(record("")) == f'{{"message":"","severity":"INFO",{TIME}}}'


def test_percent_style_arguments_are_interpolated(record: Record) -> None:
    assert line(record("processed %s in %dms", args=("abc", 12))) == (
        f'{{"message":"processed abc in 12ms","severity":"INFO",{TIME}}}'
    )


@pytest.mark.parametrize(
    ("level", "severity"),
    [
        pytest.param(alog.DEBUG, "DEBUG", id="debug"),
        pytest.param(alog.INFO, "INFO", id="info"),
        pytest.param(alog.NOTICE, "NOTICE", id="notice"),
        pytest.param(alog.WARNING, "WARNING", id="warning"),
        pytest.param(alog.ERROR, "ERROR", id="error"),
        pytest.param(alog.CRITICAL, "CRITICAL", id="critical"),
        pytest.param(alog.ALERT, "ALERT", id="alert"),
        pytest.param(alog.EMERGENCY, "EMERGENCY", id="emergency"),
        pytest.param(alog.NOTSET, "DEFAULT", id="notset_is_default"),
    ],
)
def test_each_level_writes_its_own_severity(record: Record, level: int, severity: str) -> None:
    assert line(record(level=level)) == f'{{"message":"m","severity":"{severity}",{TIME}}}'


def test_non_ascii_is_written_as_itself(record: Record) -> None:
    """The line is UTF-8 and the parser reads UTF-8, so escaping only costs bytes."""
    assert line(record('héllo wörld 🎉 <b> & "quotes" \\ back')) == (
        f'{{"message":"héllo wörld 🎉 <b> & \\"quotes\\" \\\\ back","severity":"INFO",{TIME}}}'
    )


def test_control_characters_are_escaped(record: Record) -> None:
    """A raw newline would split one entry into two lines, and two entries."""
    assert line(record("line1\nline2\ttab")) == (
        f'{{"message":"line1\\nline2\\ttab","severity":"INFO",{TIME}}}'
    )


def test_the_timestamp_keeps_a_fixed_width(record: Record) -> None:
    """Trailing zeros are kept so a column of timestamps lines up and sorts as text."""
    whole = record()
    whole.created = 1756209600.0
    assert '"time":"2025-08-26T12:00:00.000000Z"' in line(whole)


# --------------------------------------------------------------------------
# trace correlation
# --------------------------------------------------------------------------

TRACE = '"logging.googleapis.com/trace":"projects/my-project/traces/abc123"'


def test_a_full_trace_header_correlates_the_line(record: Record, project: str) -> None:
    with alog.bind(trace_context="abc123/456;o=1"):
        assert line(record()) == (
            f'{{"message":"m","severity":"INFO",{TRACE},{TIME},'
            '"logging.googleapis.com/spanId":"456",'
            '"logging.googleapis.com/trace_sampled":true}'
        )


def test_an_unsampled_request_omits_the_sampled_flag(record: Record, project: str) -> None:
    with alog.bind(trace_context="abc123/456;o=0"):
        assert line(record()) == (
            f'{{"message":"m","severity":"INFO",{TRACE},{TIME},'
            '"logging.googleapis.com/spanId":"456"}'
        )


def test_a_header_with_no_span_still_correlates(record: Record, project: str) -> None:
    with alog.bind(trace_context="abc123"):
        assert line(record()) == f'{{"message":"m","severity":"INFO",{TRACE},{TIME}}}'


def test_without_a_project_no_trace_keys_are_written(record: Record) -> None:
    """A span with no trace to hang off correlates nothing, so all three go together."""
    with alog.bind(trace_context="abc123/456;o=1"):
        assert line(record()) == f'{{"message":"m","severity":"INFO",{TIME}}}'


def test_a_header_naming_no_trace_binds_nothing(record: Record, project: str) -> None:
    """An inbound header is not something the service controls, so it does not raise."""
    with alog.bind(trace_context="/456;o=1"):
        assert line(record()) == f'{{"message":"m","severity":"INFO",{TIME}}}'


def test_the_platform_project_wins_over_the_google_one(
    record: Record, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alis.build.alog._trace import reset_project_id

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "google-project")
    monkeypatch.setenv("ALIS_OS_PROJECT", "alis-project")
    reset_project_id()

    with alog.bind(trace_context="abc123"):
        assert '"projects/alis-project/traces/abc123"' in line(record())


# --------------------------------------------------------------------------
# the optional entry fields
# --------------------------------------------------------------------------


def test_labels_are_written_under_their_own_key(record: Record) -> None:
    with alog.bind(labels={"route": "/v1/users", "az": "eu"}):
        assert line(record()) == (
            f'{{"message":"m","severity":"INFO",{TIME},'
            '"logging.googleapis.com/labels":{"route":"/v1/users","az":"eu"}}'
        )


def test_an_operation_is_written_without_its_empty_parts(record: Record) -> None:
    with alog.bind(operation=LogOperation(id="op-1", producer="svc", first=True)):
        assert line(record()) == (
            f'{{"message":"m","severity":"INFO",{TIME},'
            '"logging.googleapis.com/operation":{"id":"op-1","producer":"svc","first":true}}'
        )


def test_an_entirely_empty_operation_is_left_off(record: Record) -> None:
    """An object with nothing in it says nothing, so its key is not worth the bytes."""
    with alog.bind(operation=LogOperation()):
        assert line(record()) == f'{{"message":"m","severity":"INFO",{TIME}}}'


def test_an_insert_id_is_written(record: Record) -> None:
    with alog.bind(insert_id="abc-123"):
        assert line(record()) == (
            f'{{"message":"m","severity":"INFO",{TIME},'
            '"logging.googleapis.com/insertId":"abc-123"}'
        )


def test_an_http_request_uses_the_formats_own_key_names(record: Record) -> None:
    request = HttpRequest(
        request_method="GET",
        request_url="http://example.com/a?color=red",
        status=200,
        response_size=1234,
        user_agent="curl/8",
        remote_ip="10.0.0.1:80",
        latency="3.5s",
        cache_hit=True,
        protocol="HTTP/1.1",
    )
    with alog.bind(http_request=request):
        assert line(record()) == (
            f'{{"message":"m","severity":"INFO",{TIME},'
            '"httpRequest":{"requestMethod":"GET",'
            '"requestUrl":"http://example.com/a?color=red","status":200,'
            '"responseSize":1234,"userAgent":"curl/8","remoteIp":"10.0.0.1:80",'
            '"latency":"3.5s","cacheHit":true,"protocol":"HTTP/1.1"}}'
        )


def test_an_entirely_empty_http_request_is_left_off(record: Record) -> None:
    with alog.bind(http_request=HttpRequest()):
        assert line(record()) == f'{{"message":"m","severity":"INFO",{TIME}}}'


def test_a_source_location_names_the_file_line_and_function(record: Record) -> None:
    formatted = CloudLoggingFormatter(source_location=True).format(record())
    assert formatted == (
        '{"message":"m","severity":"INFO",'
        '"logging.googleapis.com/sourceLocation":'
        '{"file":"/app/handler.py","line":42,"function":"handle"},'
        f"{TIME}}}"
    )


def test_a_traceback_is_written_where_error_reporting_looks_for_it(record: Record) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        entry = json.loads(line(record(level=alog.ERROR, exc_info=sys.exc_info())))

    assert entry["severity"] == "ERROR"
    assert "ValueError: boom" in entry["stack_trace"]
    assert entry["stack_trace"].startswith("Traceback")


# --------------------------------------------------------------------------
# custom fields
# --------------------------------------------------------------------------


def test_bound_fields_are_merged_into_the_line(record: Record) -> None:
    with alog.bind(request_id="req-7", count=42, ok=True):
        assert line(record()) == (
            f'{{"message":"m","severity":"INFO",{TIME},"request_id":"req-7","count":42,"ok":true}}'
        )


def test_extra_on_the_logging_call_becomes_a_field(record: Record) -> None:
    """``extra=`` is how the standard library carries structured data, so it works."""
    assert line(record(user_id="u-1")) == (
        f'{{"message":"m","severity":"INFO",{TIME},"user_id":"u-1"}}'
    )


def test_a_call_field_wins_over_a_bound_one(record: Record) -> None:
    with alog.bind(where="outer"):
        assert '"where":"inner"' in line(record(where="inner"))


def test_a_field_may_not_displace_a_key_the_format_defines(record: Record) -> None:
    """A line whose severity came from a stray field is worse than one missing it."""
    with alog.bind(message="nope", severity="nope"):
        assert line(record()) == f'{{"message":"m","severity":"INFO",{TIME}}}'


def test_key_order_does_not_change_when_fields_are_bound(record: Record, project: str) -> None:
    """Fixed order means two services' lines can be read side by side."""
    with alog.bind(trace_context="abc123", z=1, a=2):
        assert line(record()) == (f'{{"message":"m","severity":"INFO",{TRACE},{TIME},"z":1,"a":2}}')


def test_a_large_integer_keeps_every_digit(record: Record) -> None:
    """Ids arrive as 64-bit integers; a float would quietly round the last bits off."""
    with alog.bind(http_request=HttpRequest(response_size=9007199254740993), z=1):
        entry = json.loads(line(record()))
        assert entry["httpRequest"]["responseSize"] == 9007199254740993


def test_a_value_the_encoder_cannot_write_is_stringified(record: Record) -> None:
    """A logging call is the wrong place to raise: losing the line is worse."""
    with alog.bind(thing=object()):
        assert '"thing":"<object object at' in line(record())


def test_nested_field_values_survive(record: Record) -> None:
    with alog.bind(detail={"b": [1, 2]}):
        assert line(record()) == (
            f'{{"message":"m","severity":"INFO",{TIME},"detail":{{"b":[1,2]}}}}'
        )
