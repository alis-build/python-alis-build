"""Deciding whether an identity is named by a policy binding member."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Self

from .._identity import Identity, IdentityType

_logger: Final = logging.getLogger(__name__)

MemberResolver = Callable[["Identity", "Member"], bool]
"""Decides whether an identity is the member a binding names."""


class ServiceAccountMatch(StrEnum):
    """Which identifier a ``serviceAccount:`` member is compared against.

    A service account's policy member is written with its email, but bindings
    written by hand or by older tooling may name its numeric id instead, so
    both forms have to be readable.
    """

    ID_ONLY = "id"
    EMAIL_ONLY = "email"
    ID_OR_EMAIL = "id_or_email"


@dataclass(frozen=True, slots=True)
class Member:
    """One entry from a policy binding's member list.

    A member is a type and an identifier separated by the first colon, so
    ``"user:1934872948"`` is type ``user`` and id ``1934872948``, and an
    identifier that itself contains colons keeps them.
    """

    type: str
    id: str

    @classmethod
    def parse(cls, text: str, /) -> Self:
        """Splits a member string on its first colon.

        A string with no colon at all yields an empty id, which never matches.
        """
        head, _, tail = text.partition(":")
        return cls(type=head, id=tail)

    def __str__(self) -> str:
        return f"{self.type}:{self.id}" if self.id else self.type


def _resolve_user(identity: Identity, member: Member) -> bool:
    return identity.type == IdentityType.USER and identity.id == member.id


def _resolve_domain(identity: Identity, member: Member) -> bool:
    return identity.email.endswith(f"@{member.id}")


def _resolve_group(identity: Identity, member: Member) -> bool:
    return member.id in (identity.group_ids or ())


def _resolve_email(identity: Identity, member: Member) -> bool:
    return identity.email == member.id


def _resolve_system(identity: Identity, member: Member) -> bool:
    """Matches any system identity.

    A system member carries no identifier, because a system actor is defined by
    its role in the deployment rather than by an account. Note this can never
    grant anything a system identity does not already have, since system
    identities bypass authorization outright.
    """
    return identity.type == IdentityType.SYSTEM


def service_account_resolver(match: ServiceAccountMatch) -> MemberResolver:
    """Builds the ``serviceAccount:`` resolver for a matching strategy."""

    def resolve(identity: Identity, member: Member) -> bool:
        if identity.type != IdentityType.SERVICE_ACCOUNT:
            return False
        if match is not ServiceAccountMatch.EMAIL_ONLY and member.id == identity.id:
            return True
        if match is not ServiceAccountMatch.ID_ONLY and member.id == identity.email:
            _logger.warning("service account matched a binding by email rather than id: %s", member)
            return True
        return False

    return resolve


def builtin_resolvers(match: ServiceAccountMatch) -> dict[str, MemberResolver]:
    """Returns the member resolvers every registry starts with.

    Note ``domain`` and ``email`` deliberately do not check the identity type:
    a binding on a domain names everyone with an address in it, whatever kind
    of account they hold.
    """
    return {
        "user": _resolve_user,
        "serviceAccount": service_account_resolver(match),
        "domain": _resolve_domain,
        "group": _resolve_group,
        "email": _resolve_email,
        "system": _resolve_system,
    }
