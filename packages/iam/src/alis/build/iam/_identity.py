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
class AccountStanding:
    """The account's credit verdict, as the accounts service decided it at minting.

    The issuer leaves empty values out, so a blocked standing arrives without
    ``allow`` at all: only the absence of the whole object means "unknown".
    """

    allow: bool = False
    reason: str = ""
    """An ``AllowTransactionResponse.Reason`` name, e.g. ``"NO_BILLING_DETAILS"``."""


@dataclass(frozen=True, slots=True)
class ClaimTime:
    """A protobuf Timestamp as the issuer mints it into a claim."""

    seconds: int = 0
    nanos: int = 0


@dataclass(frozen=True, slots=True)
class ActiveAccount:
    """The user's single active account, shared by every product.

    The optional fields are ``None`` when the issuer left them out, which is
    different from a false or empty value: an absent ``valid`` means "no
    verdict", while ``valid=False`` is a verdict.
    """

    account_id: str = ""
    """The bare account id, without the ``accounts/`` prefix."""

    account_credit_limit: float = 0.0
    user_credit_limit: float = 0.0
    account_standing: AccountStanding | None = None
    """Whether the account may spend credits. ``None`` when no standing was received."""

    member_blocked_until: ClaimTime | None = None
    """Set while the user's own spend limit on the account blocks them."""

    valid: bool | None = None
    """Whether this user may do credit-costing work on the account.

    ``None`` when the issuer has no verdict for the member.
    """

    reason: str = ""
    """Why ``valid`` is false, as a ``Member.Validity.Reason`` name. Empty when valid."""

    comments: str = ""
    """Why ``valid`` is false in words, safe to show the user. Empty when valid."""


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
    eap: bool = False
    """The user's Early Access Program preference when the token was issued.

    Absent means ``False``. It enables previews, never additional permissions.
    """

    active_account: ActiveAccount | None = None
    """The user's single active account, shared by every product.

    Prefer :meth:`active_account_id` over reading this or the deprecated
    per-product fields directly.
    """

    active_ideate_account: IdeateAccount | None = None
    """Deprecated: mirrored from ``active_account`` by the issuer while consumers
    migrate. Use :meth:`active_account_id`."""

    active_build_account: BuildAccount | None = None
    """Deprecated: mirrored from ``active_account`` by the issuer while consumers
    migrate. Use :meth:`active_account_id`."""

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
    "AccountStanding",
    "ActiveAccount",
    "BuildAccount",
    "ClaimTime",
    "IdeateAccount",
    "Identity",
    "IdentityType",
    "Seat",
]
