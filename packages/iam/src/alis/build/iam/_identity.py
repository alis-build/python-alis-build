"""The identity model shared by authentication and authorization.

Every type here is a frozen dataclass: an identity is a value that gets parked
in a context variable and read from many tasks, so it must not be mutable.
Decoding never mutates an identity either; it returns a new one (see
:func:`alis.build.iam.loads`).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ._registry import SystemConfig, system_config


class IdentityType(StrEnum):
    """What kind of actor an identity represents."""

    USER = "user"
    SERVICE_ACCOUNT = "serviceAccount"
    SYSTEM = "system"
    """Bypasses every role and permission check."""


@dataclass(frozen=True, slots=True)
class Seat:
    """A seat a user holds under a plan."""

    plan: int = 0
    seat: int = 0


@dataclass(frozen=True, slots=True)
class Account:
    """A user's seats and credit limits within one account."""

    seats: dict[str, Seat] | None = None
    """Keyed by the plan number as a string, matching the wire format."""

    ideate_account_credit_limit: float = 0.0
    ideate_user_credit_limit: float = 0.0


@dataclass(frozen=True, slots=True)
class IdeateAccount:
    """The Ideate account an identity is currently acting under."""

    account_id: str = ""
    ideate_account_credit_limit: float = 0.0
    ideate_user_credit_limit: float = 0.0


@dataclass(frozen=True, slots=True)
class BuildAccount:
    """The Build account an identity is currently acting under."""

    account_id: str = ""


@dataclass(frozen=True, slots=True)
class Identity:
    """An authenticated user, service account, or system actor.

    The container fields are ``| None`` rather than defaulting to an empty
    collection because the wire format distinguishes them: an absent collection
    serialises as ``null`` and an empty one as ``{}``. Defaulting to ``{}`` here
    would silently change the bytes every service emits.

    Note the constructor default for ``type`` is ``USER``, while the wire
    format's own zero value is the empty string. That affects hand-constructed
    identities only, never anything decoded off the wire.
    """

    type: IdentityType | str = IdentityType.USER
    id: str = ""
    email: str = ""
    accounts: dict[str, Account] | None = None
    group_ids: list[str] | None = None
    policy: str = ""
    """Standard padded base64 of a serialised ``google.iam.v1.Policy``."""

    exp: int = 0
    app: str = ""
    scopes: list[str] | None = None
    active_ideate_account: IdeateAccount | None = None
    active_build_account: BuildAccount | None = None
    authz_roles: list[str] | None = None
    """The roles a scoped credential is limited to.

    This package only carries the value. Only the service that issued the
    credential may interpret or enforce it.

    The values are that issuer's own role vocabulary and nothing more. Role ids
    are unnamespaced strings registered into a process-local map by
    :meth:`~alis.build.iam.authz.Registry.add_role_permissions`, so the same
    string routinely means different permissions in different services, and no
    two services need agree. A service that trims its own roles against an
    allowlist minted elsewhere is comparing strings that were never in the same
    vocabulary: it denies most of what it should allow and grants whatever
    happens to collide.

    Conventional names collide the most. A name obvious enough for one service
    to reach for is obvious to every other, so the same id ends up registered
    widely while each service attaches its own permissions to it. The more
    familiar the name, the less its presence in an allowlist tells you.

    So carriage is all this field can safely be. Treat an allowlist minted by
    another service as opaque: forward it untouched, or ignore it. A credential
    that must be scoped across a service boundary has to be expressed in
    something both ends define, such as a policy, not in these ids.

    ``None`` (the claim absent or null) means unrestricted. An empty list (the
    claim present as ``[]``) means restricted to no roles at all, which is the
    most restricted credential there is. Never normalise empty to ``None`` and
    never omit it when encoding: collapsing the two turns the most restricted
    credential into the least restricted one.
    """

    restricted: bool = False
    """Marks a credential that may never exceed the roles it explicitly carries.

    The system and admin bypass in :meth:`is_privileged` and the open-role
    bypass in :mod:`alis.build.iam.authz` do not apply to it. Absent means
    ``False``, so existing callers see no change.

    :meth:`is_system` and :meth:`is_admin` still report who the principal is;
    only :meth:`is_privileged` is affected. This is also distinct from any
    issuer claim describing how the holder authenticated: authorization must key
    off ``restricted``, not off the credential kind.

    Restriction is fail-open across versions. A consumer that predates this
    field ignores the claim and grants the credential its principal's full
    authority, so issuers have to keep their own guards until every consumer has
    upgraded.
    """

    def policy_member(self) -> str:
        """Returns the member string to use in IAM policy bindings.

        For example ``"user:1934872948"`` or
        ``"serviceAccount:alis-build@my-project.iam.gserviceaccount.com"``.
        System identities return the bare string ``"system"``.
        """
        if self.type == IdentityType.SYSTEM:
            return "system"
        if self.type == IdentityType.SERVICE_ACCOUNT:
            return f"{self.type}:{self.email}"
        return f"{self.type}:{self.id}"

    def user(self) -> str:
        """Returns the ``users/{id}`` resource name, or ``""`` for non-users."""
        if self.type == IdentityType.USER:
            return f"users/{self.id}"
        return ""

    def is_system(self) -> bool:
        """Reports whether this identity bypasses authorization as a system actor.

        This reads the ``type`` field only. An identity whose email is
        registered as a system email is promoted during decoding, not here, so a
        hand-constructed identity is never silently privileged.
        """
        return self.type == IdentityType.SYSTEM

    def is_admin(self, config: SystemConfig | None = None) -> bool:
        """Reports whether this identity's email is a registered admin."""
        return (config or system_config()).is_admin_email(self.email)

    def is_privileged(self, config: SystemConfig | None = None) -> bool:
        """Reports whether this credential bypasses role and permission checks.

        True for system and admin identities unless the credential is
        :attr:`restricted`.

        Bypass decisions must use this rather than :meth:`is_system` or
        :meth:`is_admin`, which describe the principal only. Keying off those
        instead lets a restricted credential exceed the roles it carries.
        """
        return (self.is_system() or self.is_admin(config)) and not self.restricted


SYSTEM_IDENTITY = Identity(type=IdentityType.SYSTEM, id="system")
"""A service acting as itself. Frozen, so it cannot be mutated process-wide."""

__all__ = [
    "SYSTEM_IDENTITY",
    "Account",
    "BuildAccount",
    "IdeateAccount",
    "Identity",
    "IdentityType",
    "Seat",
]
