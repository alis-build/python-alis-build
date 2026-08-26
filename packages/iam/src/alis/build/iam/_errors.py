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


class PolicyFetchError(AuthorizationError):
    """Fetching a resource's policy failed.

    The underlying failure is kept as the exception's cause.
    """

    def __init__(self, resource: str, /) -> None:
        super().__init__(f"fetching the policy for {resource!r} failed")
        self.resource = resource


class PoolExhaustedError(AuthorizationError):
    """A policy pool was gathered more than once."""


class AuthenticationError(IamError):
    """Base class for authentication problems."""


class InvalidTokenError(AuthenticationError, ValueError):
    """A token is malformed, or its claims do not hold up."""


class TokenExpiredError(InvalidTokenError):
    """A token's expiry has passed."""


class InvalidAudienceError(InvalidTokenError):
    """A token was issued for a different audience."""


class SignatureVerificationError(InvalidTokenError):
    """A token's signature does not verify against the issuer's key."""


class UnknownSigningKeyError(SignatureVerificationError):
    """The issuer has no published key matching the token's key id."""


class TokenEndpointError(AuthenticationError):
    """The authorization server refused a token request."""

    def __init__(self, status: int, body: str, /) -> None:
        super().__init__(f"the token endpoint returned {status}: {body}")
        self.status = status
        self.body = body


class LoginError(AuthenticationError):
    """A login could not be completed."""


class LoginStateMismatchError(LoginError):
    """The state returned by the authorization server does not match the request.

    This is the cross-site request forgery check, so a mismatch means the
    callback should be refused outright rather than retried.
    """


class LoginExpiredError(LoginError):
    """The login took longer than the transaction was valid for."""


class NonceMismatchError(LoginError):
    """An identity token's nonce does not match the one the login sent."""
