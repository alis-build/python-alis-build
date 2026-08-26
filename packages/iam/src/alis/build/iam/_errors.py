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


class AuthorizationError(IamError):
    """Base class for authorization problems."""


class InvalidPolicyError(AuthorizationError, ValueError):
    """An identity's embedded policy could not be decoded."""


class UnknownPermissionError(AuthorizationError, KeyError):
    """A permission was checked that no role grants.

    Only raised when the registry is in strict mode; otherwise an unknown
    permission is simply denied.
    """


class ConditionalBindingError(AuthorizationError):
    """A policy binding carries a condition the registry refuses to ignore."""


class MemberResolverConflictError(AuthorizationError):
    """A resolver is already registered for a member type."""


class RegistryFrozenError(AuthorizationError):
    """The registry was modified after being frozen for serving."""
