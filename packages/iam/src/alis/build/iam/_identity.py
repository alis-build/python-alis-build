"""The identity model shared by authentication and authorization.

Every type here is a frozen dataclass: an identity is a value that gets parked
in a context variable and read from many tasks, so it must not be mutable.
Decoding never mutates an identity either; it returns a new one (see
:func:`alis.build.iam.loads`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final

from ._registry import SystemConfig, system_config

MEMBER_CREDIT_LIMIT_REACHED: Final = "MEMBER_CREDIT_LIMIT_REACHED"
"""The :class:`CreditStanding` reason for a user blocked by their own spend limit.

As opposed to the account's limit, whose reasons come from the accounts service.
"""

# Keep in step with the text the accounts service returns from AllowTransaction:
# a user should read the same words whichever way the verdict reached them.
_CREDIT_MESSAGES: Final[Mapping[str, str]] = {
    "NO_BILLING_DETAILS": "Please add your billing details to continue.",
    "TRIAL_EXPIRED": "Your account trial is expired. Please upgrade your plan to continue.",
    "ACCOUNT_ARCHIVED": "This account is archived.",
    MEMBER_CREDIT_LIMIT_REACHED: (
        "You have reached your personal credit limit for this billing period. "
        "Please contact your account administrator to adjust your limit."
    ),
}
# ACCOUNT_CREDIT_LIMIT_REACHED, and any reason added later.
_DEFAULT_CREDIT_MESSAGE: Final = (
    "Your account has reached its credit limit. "
    "Please purchase new credits or upgrade your plan to continue."
)


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

    def as_datetime(self) -> datetime:
        """Returns the claim as an aware UTC datetime.

        A datetime holds microseconds, so any finer precision is truncated.

        Raises:
            ValueError: If the claim lies outside the years a datetime can
                represent (1 to 9999). Issuers never mint such values.
        """
        try:
            return datetime.fromtimestamp(self.seconds, tz=UTC) + timedelta(
                microseconds=self.nanos // 1000
            )
        except (OverflowError, OSError) as exc:
            # The platform's time_t limit surfaces as these rather than as
            # ValueError; normalise so callers have one exception to handle.
            raise ValueError(f"claim time out of range: {self.seconds}s") from exc


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
class CreditStanding:
    """What an identity's token says about charging credit-costing work to an account."""

    known: bool = False
    """False when the token cannot answer: no standing claim, or the account is
    not the active one. Callers decide what unknown means, typically by asking
    the accounts service."""

    allowed: bool = False
    reason: str = ""
    """An ``AllowTransactionResponse.Reason`` name, or
    :data:`MEMBER_CREDIT_LIMIT_REACHED`. Empty when allowed."""

    message: str = ""
    """Safe to show the end user. Empty when allowed."""

    blocked_until: datetime | None = None
    """When a member's own block lapses. ``None`` unless ``reason`` is
    :data:`MEMBER_CREDIT_LIMIT_REACHED`."""


@dataclass(frozen=True, slots=True)
class Validity:
    """Whether the user may do credit-costing work on their active account.

    This is the accounts service's verdict on the member, made when the token
    was minted.
    """

    valid: bool = False
    """Decides the verdict. An unrecognised ``reason`` still means not valid."""

    reason: str = ""
    """Why not, as a ``Member.Validity.Reason`` name such as
    ``"NO_BILLING_DETAILS"``: use it to choose what to offer the user. Empty
    when valid."""

    comments: str = ""
    """Why not in words, safe to show the user as it is. Empty when valid."""


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

    def is_eap(self) -> bool:
        """Reports whether the user opted into the Early Access Program.

        It enables previews, never extra permissions: callers still apply their
        usual authentication and authorization.
        """
        return self.eap

    def active_account_id(self) -> str:
        """Returns the bare id of the active account, or ``""`` when none is set.

        Reads ``active_account`` and falls back to the deprecated
        ``active_build_account`` then ``active_ideate_account``, so tokens minted
        before the issuer consolidated them still resolve. The result never
        carries the ``accounts/`` prefix.
        """
        accounts = (self.active_account, self.active_build_account, self.active_ideate_account)
        for account in accounts:
            if account is not None and account.account_id:
                return account.account_id.removeprefix("accounts/")
        return ""

    def credit_standing(self, account: str, now: datetime | None = None) -> CreditStanding:
        """Reports whether the token allows credit-costing work on ``account``.

        The token only describes the active account, so any other account is
        unknown. An account block outranks the user's own spend block.

        Args:
            account: ``accounts/{id}`` or the bare id.
            now: The time to judge a member block against. Must be
                timezone-aware. Defaults to the current UTC time.

        Raises:
            ValueError: If the member block lies outside the years a datetime
                can represent; see :meth:`ClaimTime.as_datetime`.
        """
        active = self.active_account
        if (
            active is None
            or active.account_standing is None
            or account.removeprefix("accounts/") != self.active_account_id()
        ):
            return CreditStanding()
        standing = active.account_standing
        if not standing.allow:
            return CreditStanding(
                known=True,
                reason=standing.reason,
                message=_CREDIT_MESSAGES.get(standing.reason, _DEFAULT_CREDIT_MESSAGE),
            )
        if active.member_blocked_until is not None:
            until = active.member_blocked_until.as_datetime()
            if (now or datetime.now(UTC)) < until:
                return CreditStanding(
                    known=True,
                    reason=MEMBER_CREDIT_LIMIT_REACHED,
                    message=_CREDIT_MESSAGES[MEMBER_CREDIT_LIMIT_REACHED],
                    blocked_until=until,
                )
        return CreditStanding(known=True, allowed=True)

    def credit_validity(self) -> Validity | None:
        """Reports the member's credit verdict on their active account.

        Credit-costing work is always charged to the active account, so that is
        the account the verdict covers.

        Returns:
            The verdict, or ``None`` when the token has none. Callers then ask
            the accounts service's ``AllowTransaction`` instead. A standing on
            its own is not a verdict.
        """
        active = self.active_account
        if active is None or active.valid is None:
            return None
        return Validity(valid=active.valid, reason=active.reason, comments=active.comments)

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
    "MEMBER_CREDIT_LIMIT_REACHED",
    "SYSTEM_IDENTITY",
    "Account",
    "AccountStanding",
    "ActiveAccount",
    "BuildAccount",
    "ClaimTime",
    "CreditStanding",
    "IdeateAccount",
    "Identity",
    "IdentityType",
    "Seat",
    "Validity",
]
