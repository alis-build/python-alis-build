"""Exception hierarchy for the whole package."""

from __future__ import annotations


class AlogError(Exception):
    """Base class for every error raised by ``alis.build.alog``."""


class ConfigurationError(AlogError, ValueError):
    """The package was asked to configure itself in a way it cannot.

    Raised for an unreadable ``ALOG_LEVEL`` or ``ALOG_MODE``, and for a level or
    mode argument that names nothing. Also subclasses :class:`ValueError` so a
    startup path doing broad input validation catches it naturally.

    Configuration problems raise rather than falling back to a default, because
    a service that silently logs at the wrong level looks healthy right up until
    the incident where its debug lines were needed.
    """


class MissingDependencyError(AlogError, ImportError):
    """An optional subpackage was imported without its extra installed.

    Subclasses :class:`ImportError` so ``except ImportError`` and
    ``pytest.importorskip`` behave as callers expect.
    """
