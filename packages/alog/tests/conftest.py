"""Shared fixtures.

Logging configuration and the resolved project id are both process-wide, so
without an autouse reset a test that sets either one silently changes the
result of every test that runs afterwards.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator

import pytest

from alis.build.alog._trace import reset_project_id

_PROJECT_VARS = ("ALIS_OS_PROJECT", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT")
_RUNTIME_VARS = ("K_SERVICE", "CLOUD_RUN_JOB", "KUBERNETES_SERVICE_HOST", "ALOG_LEVEL", "ALOG_MODE")

FIXED_TIME = 1756209600.123456
"""Chosen so every expected line can spell its timestamp out as a literal."""


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Runs every test with no project, no runtime markers, and a cold cache."""
    for name in (*_PROJECT_VARS, *_RUNTIME_VARS):
        monkeypatch.delenv(name, raising=False)
    reset_project_id()
    try:
        yield
    finally:
        reset_project_id()


@pytest.fixture
def project(monkeypatch: pytest.MonkeyPatch) -> str:
    """Names a project, so trace correlation has a resource name to build."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-project")
    reset_project_id()
    return "my-project"


@pytest.fixture
def record() -> Callable[..., logging.LogRecord]:
    """Builds a record whose creation time is fixed, so ``time`` is a literal."""

    def build(
        message: str = "m",
        *,
        level: int = logging.INFO,
        name: str = "svc",
        args: object = None,
        exc_info: object = None,
        **attributes: object,
    ) -> logging.LogRecord:
        built = logging.LogRecord(
            name,
            level,
            "/app/handler.py",
            42,
            message,
            args,  # type: ignore[arg-type]
            exc_info,  # type: ignore[arg-type]
            func="handle",
        )
        built.created = FIXED_TIME
        built.__dict__.update(attributes)
        return built

    return build
