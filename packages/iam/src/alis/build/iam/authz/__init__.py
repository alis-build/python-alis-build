"""Role and permission checks against IAM policies.

Requires the ``authz`` extra::

    pip install 'alis-build-iam[authz]'

Register what each role grants once at startup, then freeze the registry::

    def configure_authorization() -> None:
        viewer = add_role_permissions("roles/viewer", ["/example.v1.Books/Get"])
        add_role_permissions("roles/admin", ["/example.v1.Books/Delete"],
                             inherits=["roles/viewer"])
        default_registry().freeze()

Then, per request::

    authorizer = Authorizer(from_context())
    if not authorizer.has_permission("/example.v1.Books/Get", policy):
        raise PermissionDenied
"""

from __future__ import annotations

from .._optional import missing_dependency

try:
    from google.iam.v1 import policy_pb2 as _policy_pb2
except ImportError as exc:  # pragma: no cover - exercised by the bare-core CI job
    raise missing_dependency(__name__, "authz", "grpc-google-iam-v1") from exc

del _policy_pb2

from .._errors import (  # noqa: E402 - must follow the dependency check above
    AuthorizationError,
    ConditionalBindingError,
    InvalidPolicyError,
    MemberResolverConflictError,
    RegistryFrozenError,
    UnknownPermissionError,
)
from ._authorizer import (  # noqa: E402
    Authorizer,
    ConditionalBinding,
    decode_policy,
    reset_condition_warnings,
)
from ._members import (  # noqa: E402
    Member,
    MemberResolver,
    ServiceAccountMatch,
)
from ._registry import (  # noqa: E402
    AuditFinding,
    ConditionMode,
    Registry,
    add_member_resolver,
    add_open_role_permissions,
    add_role_permissions,
    default_registry,
    set_default_registry,
)

__all__ = [
    "AuditFinding",
    "AuthorizationError",
    "Authorizer",
    "ConditionMode",
    "ConditionalBinding",
    "ConditionalBindingError",
    "InvalidPolicyError",
    "Member",
    "MemberResolver",
    "MemberResolverConflictError",
    "Registry",
    "RegistryFrozenError",
    "ServiceAccountMatch",
    "UnknownPermissionError",
    "add_member_resolver",
    "add_open_role_permissions",
    "add_role_permissions",
    "decode_policy",
    "default_registry",
    "reset_condition_warnings",
    "set_default_registry",
]
