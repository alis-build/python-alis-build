"""The authorization registry: roles, permissions, and member resolvers.

Everything an :class:`~alis.build.iam.authz.Authorizer` consults lives on a
registry object rather than in module globals, so a test can build its own and
a service can hold more than one. A process-wide default exists for the common
case.

Register everything from an explicit setup function called at startup, then
call :meth:`Registry.freeze`. Registering as an import side effect works right
up until the module that does it stops being imported, at which point every
check silently denies.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Self

from .._errors import MemberResolverConflictError, RegistryFrozenError
from .._identity import Identity
from ._members import (
    Member,
    MemberResolver,
    ServiceAccountMatch,
    builtin_resolvers,
    service_account_resolver,
)

_logger: Final = logging.getLogger(__name__)


class ConditionMode(StrEnum):
    """What to do with a policy binding that carries a condition.

    Conditions are written in an expression language that is evaluated against
    the resource being accessed, and an authorization check here is not given
    the resource, so the condition cannot be evaluated correctly.
    """

    ALLOW = "allow"
    """Grant the binding as though it were unconditional, and warn. The default,
    because flipping straight to ``DENY`` revokes access that is currently
    working."""

    DENY = "deny"
    """Never grant a conditional binding. The recommended production setting
    once the ``ALLOW`` warnings have stopped appearing."""

    ERROR = "error"
    """Raise. Recommended in tests, so a conditional binding fails the build
    rather than being quietly mishandled in production."""


@dataclass(frozen=True, slots=True)
class AuditFinding:
    """Something about the registry's configuration worth a second look."""

    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.detail}"


@dataclass(slots=True)
class Registry:
    """Roles, permissions and member resolvers for authorization checks.

    Args:
        condition_mode: How to treat conditional policy bindings.
        service_account_match: Which identifier a ``serviceAccount:`` member is
            compared against.
        strict_permissions: Raise on a permission no role grants, instead of
            denying. Turns "the setup function was never called" from a puzzling
            403 into an obvious error, and is worth enabling in tests.
    """

    condition_mode: ConditionMode = ConditionMode.ALLOW
    service_account_match: ServiceAccountMatch = ServiceAccountMatch.ID_OR_EMAIL
    strict_permissions: bool = False

    _role_permissions: dict[str, frozenset[str]] = field(default_factory=dict, repr=False)
    _permission_roles: dict[str, frozenset[str]] = field(default_factory=dict, repr=False)
    _open_roles: frozenset[str] = field(default=frozenset(), repr=False)
    _resolvers: dict[str, MemberResolver] = field(default_factory=dict, repr=False)
    _frozen: bool = field(default=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if not self._resolvers:
            self._resolvers = builtin_resolvers(self.service_account_match)

    # -- registration ------------------------------------------------------

    def _check_mutable(self) -> None:
        if self._frozen:
            raise RegistryFrozenError("the registry is frozen and cannot be modified")

    def add_role_permissions(
        self, role: str, permissions: Iterable[str], /, *, inherits: Iterable[str] = ()
    ) -> tuple[str, ...]:
        """Registers the permissions a role grants.

        Role ids are unnamespaced and scoped to the registering process. The map
        lives in this registry, so an id registered here and the same id
        registered in another service are unrelated strings that happen to
        match, and each may grant entirely different permissions. That is fine,
        and needs no guard, so long as no id is carried across a service
        boundary as authorization data.

        Do not do that. Nothing that leaves this process, a token claim
        especially, can be read elsewhere as naming the permissions registered
        here. See :attr:`~alis.build.iam.Identity.authz_roles`, which carries
        such ids and is meaningful only to the service that minted them.

        Qualifying ids by service, so they could be reasoned about across a
        boundary, would be the fix if cross-service role comparison were ever
        wanted. It is not done because every policy binding already stored names
        a bare string, making it a data migration rather than a change here.

        Args:
            role: The role name, such as ``"roles/viewer"``.
            permissions: Permissions the role grants directly.
            inherits: Roles whose permissions this role also grants. Resolved
                from the registry, so the order of registration does not matter.

        Returns:
            Every permission the role now grants, so it can be composed into
            another registration.

        Raises:
            RegistryFrozenError: If the registry has been frozen.
        """
        self._check_mutable()
        with self._lock:
            granted = set(permissions)
            for parent in inherits:
                granted |= self._role_permissions.get(parent, frozenset())
            self._role_permissions[role] = self._role_permissions.get(
                role, frozenset()
            ) | frozenset(granted)
            for permission in granted:
                self._permission_roles[permission] = self._permission_roles.get(
                    permission, frozenset()
                ) | {role}
            return tuple(sorted(self._role_permissions[role]))

    def add_open_role_permissions(
        self, role: str, permissions: Iterable[str], /, *, inherits: Iterable[str] = ()
    ) -> tuple[str, ...]:
        """Registers a role that grants to **every** caller.

        An open role is an allow-list of role names, not a grant to anyone in
        particular: a check for an open role succeeds for any caller at all,
        authenticated or not. Every permission the role grants becomes
        world-reachable along with it.

        The exception is a credential marked
        :attr:`~alis.build.iam.Identity.restricted`, which is never granted an
        open role.

        The id is process-local in the way :meth:`add_role_permissions`
        describes, and open roles are where that bites hardest. An open role
        usually takes a conventional name, so many services register the same id
        while each grants an entirely different set of permissions behind it,
        and being open it is reached without any grant to the caller. Never
        treat an open role id from one service as naming the same authority in
        another.

        Raises:
            RegistryFrozenError: If the registry has been frozen.
        """
        self._check_mutable()
        result = self.add_role_permissions(role, permissions, inherits=inherits)
        with self._lock:
            self._open_roles |= {role}
        return result

    def add_member_resolver(
        self, member_types: Iterable[str], resolver: MemberResolver, /, *, replace: bool = False
    ) -> None:
        """Registers a resolver for one or more member types.

        Every type is validated before any is written, so a conflict leaves the
        registry exactly as it was.

        Raises:
            MemberResolverConflictError: If a type already has a resolver and
                ``replace`` is not set.
            RegistryFrozenError: If the registry has been frozen.
        """
        self._check_mutable()
        types = tuple(member_types)
        with self._lock:
            if not replace:
                clashes = [name for name in types if name in self._resolvers]
                if clashes:
                    raise MemberResolverConflictError(
                        f"resolver already registered for: {', '.join(sorted(clashes))}"
                    )
            for name in types:
                self._resolvers[name] = resolver

    # -- lookups -----------------------------------------------------------

    def roles_for_permission(self, permission: str, /) -> frozenset[str]:
        """Returns the roles that grant a permission, empty if none do."""
        return self._permission_roles.get(permission, frozenset())

    def is_open(self, role: str, /) -> bool:
        """Reports whether a check for this role succeeds for any caller."""
        return role in self._open_roles

    def is_member(self, identity: Identity, member: Member, /) -> bool:
        """Reports whether a binding member names this identity.

        A member with no identifier never matches. Without that guard a
        malformed entry such as ``"email:"`` would match any identity that
        happens to have no email, turning a typo into a broad grant.
        """
        if not member.id and member.type != "system":
            return False
        resolver = self._resolvers.get(member.type)
        if resolver is None:
            _logger.debug("no resolver for member type %r, denying", member.type)
            return False
        return resolver(identity, member)

    @property
    def open_roles(self) -> frozenset[str]:
        """Immutable snapshot of the roles that grant to every caller."""
        return self._open_roles

    @property
    def member_types(self) -> frozenset[str]:
        """Immutable snapshot of the member types that have a resolver."""
        return frozenset(self._resolvers)

    # -- lifecycle ---------------------------------------------------------

    def freeze(self) -> None:
        """Closes the registry to further changes and logs any audit findings.

        Call this at the end of startup configuration. It makes the "configure
        once, then serve" rule enforceable rather than merely documented.
        """
        for finding in self.audit():
            _logger.warning("authorization registry: %s", finding)
        self._frozen = True

    @property
    def frozen(self) -> bool:
        """Whether the registry has been closed to changes."""
        return self._frozen

    def audit(self) -> list[AuditFinding]:
        """Reports configuration that is legal but probably not intended."""
        findings: list[AuditFinding] = []
        for permission, roles in sorted(self._permission_roles.items()):
            open_roles = sorted(role for role in roles if role in self._open_roles)
            closed_roles = sorted(role for role in roles if role not in self._open_roles)
            if open_roles and closed_roles:
                findings.append(
                    AuditFinding(
                        "open-role-reach",
                        f"{permission!r} is granted by open role(s) {open_roles} as well as "
                        f"{closed_roles}, so it is reachable by any caller",
                    )
                )
        for role, permissions in sorted(self._role_permissions.items()):
            if not permissions:
                findings.append(AuditFinding("empty-role", f"{role!r} grants no permissions"))
        return findings

    def snapshot(self) -> Self:
        """Returns an independent copy, for saving and restoring in tests."""
        return type(self)(
            condition_mode=self.condition_mode,
            service_account_match=self.service_account_match,
            strict_permissions=self.strict_permissions,
            _role_permissions=dict(self._role_permissions),
            _permission_roles=dict(self._permission_roles),
            _open_roles=self._open_roles,
            _resolvers=dict(self._resolvers),
            _frozen=self._frozen,
        )

    def reset(self) -> None:
        """Clears every registration and restores the built-in resolvers."""
        with self._lock:
            self._role_permissions = {}
            self._permission_roles = {}
            self._open_roles = frozenset()
            self._resolvers = builtin_resolvers(self.service_account_match)
            self._frozen = False

    def use_service_account_match(self, match: ServiceAccountMatch, /) -> None:
        """Changes how ``serviceAccount:`` members are matched.

        Rebuilds the built-in resolver, leaving any custom one alone.

        Raises:
            RegistryFrozenError: If the registry has been frozen.
        """
        self._check_mutable()
        with self._lock:
            self.service_account_match = match
            self._resolvers["serviceAccount"] = service_account_resolver(match)


_default = Registry()


def default_registry() -> Registry:
    """Returns the process-wide registry used when none is injected."""
    return _default


def set_default_registry(registry: Registry, /) -> None:
    """Replaces the process-wide registry. Intended for tests and app setup."""
    # Module-level singleton, deliberately swapped by tests.
    global _default
    _default = registry


def add_role_permissions(
    role: str, permissions: Iterable[str], /, *, inherits: Iterable[str] = ()
) -> tuple[str, ...]:
    """Registers role permissions on the default registry."""
    return _default.add_role_permissions(role, permissions, inherits=inherits)


def add_open_role_permissions(
    role: str, permissions: Iterable[str], /, *, inherits: Iterable[str] = ()
) -> tuple[str, ...]:
    """Registers an open role on the default registry."""
    return _default.add_open_role_permissions(role, permissions, inherits=inherits)


def add_member_resolver(
    member_types: Iterable[str], resolver: MemberResolver, /, *, replace: bool = False
) -> None:
    """Registers a member resolver on the default registry."""
    _default.add_member_resolver(member_types, resolver, replace=replace)
