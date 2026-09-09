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
    """The issuer's role allowlist. Carried, never enforced here."""

    restricted: bool = False
    """Suppresses the privilege and open-role bypasses."""

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
        """Reports whether this credential bypasses role and permission checks."""
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
