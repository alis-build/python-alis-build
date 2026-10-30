"""Authentication and authorization for services on the Alis Build platform.

This package keeps the caller identity in one consistent shape as it moves
through an application:

* decode a JWT into an :class:`Identity` with :func:`from_unverified_jwt`
* bind it to the current context with :func:`identity_context`
* forward it to downstream services over gRPC metadata or HTTP headers
* restore it in the receiving service with the matching accessor

An identity may represent a user, a service account, or a system actor. System
identities bypass authorization checks, which is what makes trusted internal
service calls work; use :func:`add_system_email` to mark known service account
emails as system, or :data:`SYSTEM_IDENTITY` when a service acts as itself. Use
:func:`add_admin_email` to give a human the same bypass while keeping their
identity type as user, so resource names and audit trails are unaffected.

The core is dependency-free and does no I/O. The rest lives behind extras:

=========================== ============= =======================================
Module                      Extra         Purpose
=========================== ============= =======================================
``alis.build.iam.headers``  none          HTTP header primitives
``alis.build.iam.asgi``     none          ASGI middleware
``alis.build.iam.grpc``     ``grpc``      metadata helpers and interceptors
``alis.build.iam.authz``    ``authz``     role and permission checks
``alis.build.iam.authn``    ``authn``     OAuth2/OIDC client
=========================== ============= =======================================

Example:
    >>> from alis.build import iam
    >>> identity = iam.Identity(
    ...     type=iam.IdentityType.USER, id="1934872948", email="john@example.com"
    ... )
    >>> with iam.identity_context(identity):
    ...     iam.from_context().policy_member()
    'user:1934872948'
"""

from __future__ import annotations

from ._context import (
    from_context,
    from_context_or_none,
    identity_context,
    reset_identity,
    set_identity,
)
from ._errors import (
    IamError,
    IdentityDecodeError,
    IdentityError,
    IdentityNotFoundError,
    MissingDependencyError,
)
from ._identity import (
    MEMBER_CREDIT_LIMIT_REACHED,
    SYSTEM_IDENTITY,
    Account,
    AccountStanding,
    ActiveAccount,
    BuildAccount,
    ClaimTime,
    CreditStanding,
    IdeateAccount,
    Identity,
    IdentityType,
    Seat,
)
from ._registry import (
    SystemConfig,
    add_admin_email,
    add_system_email,
    set_system_config,
    system_config,
)
from ._wire import (
    b64_std_decode,
    b64url_decode,
    b64url_encode,
    dumps,
    from_unverified_jwt,
    loads,
    promote_system,
    unsigned_jwt,
)

__version__ = "0.1.0"

__all__ = [
    "MEMBER_CREDIT_LIMIT_REACHED",
    "SYSTEM_IDENTITY",
    "Account",
    "AccountStanding",
    "ActiveAccount",
    "BuildAccount",
    "ClaimTime",
    "CreditStanding",
    "IamError",
    "IdeateAccount",
    "Identity",
    "IdentityDecodeError",
    "IdentityError",
    "IdentityNotFoundError",
    "IdentityType",
    "MissingDependencyError",
    "Seat",
    "SystemConfig",
    "__version__",
    "add_admin_email",
    "add_system_email",
    "b64_std_decode",
    "b64url_decode",
    "b64url_encode",
    "dumps",
    "from_context",
    "from_context_or_none",
    "from_unverified_jwt",
    "identity_context",
    "loads",
    "promote_system",
    "reset_identity",
    "set_identity",
    "set_system_config",
    "system_config",
    "unsigned_jwt",
]
