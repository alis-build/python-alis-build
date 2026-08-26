"""Environment detection, configuration, and the shortcut functions."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest

from alis.build import alog
from alis.build.alog import Mode
from alis.build.alog._config import detect_level, detect_mode


@pytest.fixture(autouse=True)
def fresh_logging() -> Iterator[None]:
    """Logging configuration is process-wide, so every test gets it back as it was."""
    from alis.build.alog.testing import fresh_logging_config

    with fresh_logging_config():
        yield


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------


def test_a_bare_environment_is_local() -> None:
    assert detect_mode() is Mode.LOCAL


@pytest.mark.parametrize(
    "variable",
    [
        pytest.param("K_SERVICE", id="cloud_run_service"),
        pytest.param("CLOUD_RUN_JOB", id="cloud_run_job"),
        pytest.param("KUBERNETES_SERVICE_HOST", id="kubernetes"),
    ],
)
def test_a_runtime_that_collects_stdout_means_cloud(
    variable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(variable, "set")
    assert detect_mode() is Mode.CLOUD


@pytest.mark.parametrize(
    ("value", "mode"),
    [
        pytest.param("cloud", Mode.CLOUD, id="cloud"),
        pytest.param("LOCAL", Mode.LOCAL, id="any_casing"),
        pytest.param("  local  ", Mode.LOCAL, id="surrounding_space"),
    ],
)
def test_alog_mode_overrides_detection(
    value: str, mode: Mode, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("K_SERVICE", "set")
    monkeypatch.setenv("ALOG_MODE", value)
    assert detect_mode() is mode


def test_an_unreadable_mode_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALOG_MODE", "json")
    with pytest.raises(alog.ConfigurationError, match="names no mode"):
        detect_mode()


def test_local_starts_at_debug_and_cloud_at_info() -> None:
    """Locally the detail is the point; in production the volume is the problem."""
    assert detect_level(Mode.LOCAL) == alog.DEBUG
    assert detect_level(Mode.CLOUD) == alog.INFO


@pytest.mark.parametrize(
    ("value", "level"),
    [
        pytest.param("WARNING", alog.WARNING, id="name"),
        pytest.param("notice", alog.NOTICE, id="lowercase"),
        pytest.param("40", alog.ERROR, id="number"),
    ],
)
def test_alog_level_overrides_the_default(
    value: str, level: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALOG_LEVEL", value)
    assert detect_level(Mode.CLOUD) == level


def test_an_unreadable_alog_level_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALOG_LEVEL", "chatty")
    with pytest.raises(alog.ConfigurationError):
        detect_level(Mode.CLOUD)


# --------------------------------------------------------------------------
# configure
# --------------------------------------------------------------------------


def test_importing_the_package_changes_no_logging_state() -> None:
    """A library that installs handlers on import fights every other way of doing it."""
    import importlib

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    importlib.reload(importlib.import_module("alis.build.alog"))

    assert root.handlers == []


def test_configure_formats_every_library_not_just_this_one() -> None:
    """Taking over the root logger is the whole reason to prefer it to a private one."""
    stream = io.StringIO()
    alog.configure(mode=Mode.CLOUD, level=alog.INFO, stream=stream)

    logging.getLogger("httpx").info("sent a request")

    assert json.loads(stream.getvalue())["message"] == "sent a request"


def test_configuring_twice_does_not_double_every_line() -> None:
    stream = io.StringIO()
    alog.configure(mode=Mode.CLOUD, stream=stream)
    alog.configure(mode=Mode.CLOUD, stream=stream)

    logging.getLogger("svc").warning("once")

    assert len(stream.getvalue().strip().splitlines()) == 1


def test_configure_takes_the_shortcuts_handler_back_out() -> None:
    """Otherwise a shortcut used before configure writes its line twice afterwards."""
    alog.info("before")

    stream = io.StringIO()
    alog.configure(mode=Mode.CLOUD, stream=stream)
    alog.info("after")

    assert len(stream.getvalue().strip().splitlines()) == 1


def test_configure_returns_the_handler_it_installed() -> None:
    handler = alog.configure(mode=Mode.CLOUD, stream=io.StringIO())
    assert handler in logging.getLogger().handlers


def test_a_named_logger_can_be_configured_instead_of_the_root() -> None:
    stream = io.StringIO()
    target = logging.getLogger("just.this.one")
    target.propagate = False
    alog.configure(mode=Mode.CLOUD, stream=stream, logger=target)

    logging.getLogger("someone.else").warning("elsewhere")
    target.warning("here")

    assert len(stream.getvalue().strip().splitlines()) == 1
    assert json.loads(stream.getvalue())["message"] == "here"


def test_configure_picks_the_format_up_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("K_SERVICE", "users")
    stream = io.StringIO()
    alog.configure(stream=stream)

    logging.getLogger("svc").info("hello")

    assert json.loads(stream.getvalue())["severity"] == "INFO"


def test_configure_rereads_the_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """A startup path that sets the variable then configures must get the new value."""
    from alis.build.alog._trace import project_id

    assert project_id() is None

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "late")
    alog.configure(mode=Mode.CLOUD, stream=io.StringIO())

    assert project_id() == "late"


def test_set_level_changes_the_minimum_without_touching_anything_else() -> None:
    stream = io.StringIO()
    alog.configure(mode=Mode.CLOUD, level=alog.DEBUG, stream=stream)
    handler = logging.getLogger().handlers[0]

    alog.set_level("WARNING")

    assert logging.getLogger().level == alog.WARNING
    assert logging.getLogger().handlers == [handler]


# --------------------------------------------------------------------------
# the shortcuts
# --------------------------------------------------------------------------


def test_the_shortcuts_work_with_no_setup_at_all() -> None:
    """A script that only wants alog.info should not have to call configure first."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    alog.info("hello")

    assert alog.logger().handlers != []


def test_the_shortcuts_leave_an_existing_setup_alone() -> None:
    """Handlers already on the root mean someone configured logging; do not fight it."""
    stream = io.StringIO()
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(logging.StreamHandler(stream))
    root.setLevel(alog.INFO)

    alog.info("hello")

    assert alog.logger().handlers == []
    assert stream.getvalue().strip() == "hello"


@pytest.mark.parametrize(
    ("shortcut", "severity"),
    [
        pytest.param(alog.debug, "DEBUG", id="debug"),
        pytest.param(alog.info, "INFO", id="info"),
        pytest.param(alog.notice, "NOTICE", id="notice"),
        pytest.param(alog.warning, "WARNING", id="warning"),
        pytest.param(alog.error, "ERROR", id="error"),
        pytest.param(alog.critical, "CRITICAL", id="critical"),
        pytest.param(alog.alert, "ALERT", id="alert"),
        pytest.param(alog.emergency, "EMERGENCY", id="emergency"),
    ],
)
def test_each_shortcut_writes_its_own_severity(shortcut: object, severity: str) -> None:
    stream = io.StringIO()
    alog.configure(mode=Mode.CLOUD, level=alog.DEBUG, stream=stream)

    shortcut("hello")  # type: ignore[operator]

    assert json.loads(stream.getvalue())["severity"] == severity


def test_arguments_are_interpolated_by_the_logger() -> None:
    stream = io.StringIO()
    alog.configure(mode=Mode.CLOUD, stream=stream)

    alog.info("processed %s in %dms", "abc", 12)

    assert json.loads(stream.getvalue())["message"] == "processed abc in 12ms"


def test_a_discarded_line_never_interpolates_its_arguments() -> None:
    """The reason to pass arguments rather than an f-string: this work never happens."""
    stream = io.StringIO()
    alog.configure(mode=Mode.CLOUD, level=alog.ERROR, stream=stream)

    class Expensive:
        rendered = False

        def __str__(self) -> str:
            Expensive.rendered = True
            return "expensive"

    alog.info("value is %s", Expensive())

    assert Expensive.rendered is False
    assert stream.getvalue() == ""


def test_a_source_location_names_the_caller_not_this_package() -> None:
    """What stacklevel is for: a wrapper's own frame is never the interesting one."""
    stream = io.StringIO()
    alog.configure(mode=Mode.CLOUD, level=alog.DEBUG, stream=stream)

    alog.info("hello")

    location = json.loads(stream.getvalue())["logging.googleapis.com/sourceLocation"]
    assert location["file"].endswith("test_config.py")


def test_a_shortcut_accepts_its_own_stacklevel() -> None:
    """A service wrapping these needs to point past its own frame too."""
    stream = io.StringIO()
    alog.configure(mode=Mode.CLOUD, level=alog.DEBUG, stream=stream)

    def wrapper(message: str) -> None:
        alog.info(message, stacklevel=4)

    wrapper("hello")

    location = json.loads(stream.getvalue())["logging.googleapis.com/sourceLocation"]
    assert location["function"] == "test_a_shortcut_accepts_its_own_stacklevel"


def test_extra_reaches_the_line() -> None:
    stream = io.StringIO()
    alog.configure(mode=Mode.CLOUD, stream=stream)

    alog.info("hello", extra={"user_id": "u-1"})

    assert json.loads(stream.getvalue())["user_id"] == "u-1"


def test_exception_carries_the_traceback() -> None:
    stream = io.StringIO()
    alog.configure(mode=Mode.CLOUD, stream=stream)

    try:
        raise ValueError("boom")
    except ValueError:
        alog.exception("handler failed")

    entry = json.loads(stream.getvalue())
    assert entry["severity"] == "ERROR"
    assert "ValueError: boom" in entry["stack_trace"]


def test_log_writes_at_any_level() -> None:
    stream = io.StringIO()
    alog.configure(mode=Mode.CLOUD, level=alog.DEBUG, stream=stream)

    alog.log(35, "between two of them")

    assert json.loads(stream.getvalue())["severity"] == "WARNING"


def test_fatal_writes_a_line_and_then_ends_the_program() -> None:
    stream = io.StringIO()
    alog.configure(mode=Mode.CLOUD, stream=stream)

    with pytest.raises(SystemExit) as raised:
        alog.fatal("cannot continue")

    assert raised.value.code == 1
    assert json.loads(stream.getvalue())["severity"] == "CRITICAL"


def test_fatal_lets_cleanup_run() -> None:
    """Raising rather than exiting outright is what keeps finally blocks working."""
    alog.configure(mode=Mode.CLOUD, stream=io.StringIO())
    cleaned = False

    try:
        try:
            alog.fatal("cannot continue")
        finally:
            cleaned = True
    except SystemExit:
        pass

    assert cleaned is True
