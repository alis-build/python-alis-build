"""The error raised when an optional subpackage is missing its extra."""

from __future__ import annotations

from alis.build.alog import MissingDependencyError
from alis.build.alog._optional import missing_dependency


def test_the_message_names_the_extra_and_the_install_command() -> None:
    """The bare-core check asserts all three of these, and so does a confused user."""
    error = missing_dependency("alis.build.alog.grpc", "grpc", "grpcio")

    assert isinstance(error, MissingDependencyError)
    assert "grpc" in str(error)
    assert "grpcio" in str(error)
    assert "pip install 'alis-build-alog[grpc]'" in str(error)
