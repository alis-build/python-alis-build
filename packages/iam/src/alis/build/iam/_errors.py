"""Exception hierarchy for the whole package."""

from __future__ import annotations


class IamError(Exception):
    """Base class for every error raised by ``alis.build.iam``."""


class IdentityError(IamError):
    """Base class for identity problems."""


class IdentityNotFoundError(IdentityError):
    """No identity was present where one was expected.

    Raised by the ``from_*`` accessors when a request carries no identity at
    all, as opposed to carrying one that cannot be decoded.
    """


class IdentityDecodeError(IdentityError, ValueError):
    """An identity was present but could not be decoded.

    Also subclasses :class:`ValueError` so callers doing broad input validation
    catch it naturally.
    """


class MissingDependencyError(IamError, ImportError):
    """An optional subpackage was imported without its extra installed.

    Subclasses :class:`ImportError` so ``except ImportError`` and
    ``pytest.importorskip`` behave as callers expect.
    """
