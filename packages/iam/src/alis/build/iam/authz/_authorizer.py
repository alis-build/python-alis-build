"""Deciding whether an identity holds a role or a permission."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Final, Self

from google.iam.v1.policy_pb2 import Policy
from google.protobuf.message import DecodeError

from .._errors import (
    ConditionalBindingError,
    IdentityDecodeError,
    InvalidPolicyError,
    UnknownPermissionError,
)
from .._identity import Identity
from .._wire import b64_std_decode
from ._members import Member
from ._registry import ConditionMode, Registry, default_registry

_logger: Final = logging.getLogger(__name__)

# Warnings about conditional bindings are deduplicated process-wide, so a
# condition on a hot path logs once rather than once per request.
_warned_conditions: set[tuple[str, str]] = set()
_warned_lock: Final = threading.Lock()


@dataclass(frozen=True, slots=True)
class ConditionalBinding:
    """A policy binding whose grant depends on a condition."""

    role: str
    expression: str
    title: str = ""


def decode_policy(encoded: str, /) -> Policy | None:
    """Decodes the policy an identity carries.

    Args:
        encoded: Standard padded base64 of a serialised ``google.iam.v1.Policy``.

    Returns:
        The policy, or ``None`` if the identity carries none.

    Raises:
        InvalidPolicyError: If the value is not valid base64 or not a policy.
    """
    if not encoded:
        return None
    try:
        raw = b64_std_decode(encoded)
    except IdentityDecodeError as exc:
        raise InvalidPolicyError(f"decoding identity policy: {exc}") from exc
    if not raw:
        return None
    policy = Policy()
    try:
        policy.ParseFromString(raw)
    except DecodeError as exc:
        raise InvalidPolicyError(f"parsing identity policy: {exc}") from exc
    return policy


class Authorizer:
    """Answers role and permission questions for one identity.

    Build one per request. Reads never mutate its state, so a single authorizer
    is safe to use from several tasks handling the same request.

    Args:
        identity: The caller to authorize. Roles are seeded from the policy the
            identity carries.
        registry: Where roles, permissions and member resolvers come from.
            Defaults to the process-wide registry.

    Raises:
        InvalidPolicyError: If the identity's policy cannot be decoded.
    """

    __slots__ = ("_conditional", "_identity", "_registry", "_roles")

    def __init__(self, identity: Identity, /, *, registry: Registry | None = None) -> None:
        self._identity = identity
        self._registry = registry or default_registry()
        self._conditional: list[ConditionalBinding] = []
        self._roles: frozenset[str] = frozenset()
        policy = decode_policy(identity.policy)
        if policy is not None:
            self._roles = frozenset(self._roles_in(policy))

    @classmethod
    def from_policies(
        cls, identity: Identity, /, *policies: Policy | None, registry: Registry | None = None
    ) -> Self:
        """Builds an authorizer and persists the roles these policies grant."""
        authorizer = cls(identity, registry=registry)
        authorizer.add_roles_from_policies(*policies)
        return authorizer

    @property
    def identity(self) -> Identity:
        """The caller this authorizer answers for."""
        return self._identity

    @property
    def registry(self) -> Registry:
        """The registry this authorizer consults."""
        return self._registry

    @property
    def roles(self) -> frozenset[str]:
        """Immutable snapshot of the roles held across every check."""
        return self._roles

    @property
    def conditional_bindings_seen(self) -> tuple[ConditionalBinding, ...]:
        """Conditional bindings encountered so far.

        Assert this is empty in your own tests to prove no policy in play
        depends on a condition this package cannot evaluate.
        """
        return tuple(self._conditional)

    def has_role(self, roles: Iterable[str], /, *policies: Policy | None) -> bool:
        """Reports whether the caller holds any of these roles.

        Args:
            roles: Roles to check. Holding **any** of them is enough, and an
                empty collection is always a refusal.
            policies: Extra policies to consider for this check only. Roles they
                grant are not remembered.

        Returns:
            Whether the caller is authorized.
        """
        if self._identity.is_privileged():
            return True

        requested = tuple(roles)
        for role in requested:
            if self._registry.is_open(role):
                return True
        if not requested:
            return False

        # A fresh set every time: a check must never mutate what the authorizer
        # holds, or two concurrent checks would see each other's policies.
        available = self._roles | self._roles_from(policies)
        return any(role in available for role in requested)

    def has_permission(self, permission: str, /, *policies: Policy | None) -> bool:
        """Reports whether the caller holds a role granting this permission.

        A permission no role grants is refused. In a registry with
        ``strict_permissions`` set it raises instead, which is how a
        never-called setup function or a typo gets noticed. That check runs
        before the privilege bypass, so a system caller does not mask it.

        Raises:
            UnknownPermissionError: If no role grants the permission and the
                registry is strict.
        """
        roles = self._registry.roles_for_permission(permission)
        if not roles and self._registry.strict_permissions:
            raise UnknownPermissionError(
                f"no role grants {permission!r}; register it before checking it"
            )
        return self.has_role(roles, *policies)

    def add_roles(self, *roles: str) -> None:
        """Grants roles directly, for every subsequent check."""
        self._roles |= frozenset(roles)

    def add_roles_from_policies(self, *policies: Policy | None) -> None:
        """Persists the roles these policies grant, for every subsequent check.

        Prefer :meth:`with_roles_from_policies`, or pass policies straight to
        :meth:`has_role`, when checking several resources in one request:
        persisted roles carry over from one resource to the next, which is
        rarely what a list handler wants.
        """
        self._roles |= self._roles_from(policies)

    def with_roles_from_policies(self, *policies: Policy | None) -> Self:
        """Returns a copy that also holds the roles these policies grant."""
        clone = object.__new__(type(self))
        clone._identity = self._identity
        clone._registry = self._registry
        clone._conditional = list(self._conditional)
        clone._roles = self._roles | self._roles_from(policies)
        return clone

    def _roles_from(self, policies: Sequence[Policy | None]) -> frozenset[str]:
        """Collects the roles a set of policies grants this identity."""
        found: set[str] = set()
        for policy in policies:
            if policy is None:
                continue
            found |= self._roles_in(policy)
        return frozenset(found)

    def _roles_in(self, policy: Policy) -> set[str]:
        """Collects the roles one policy grants this identity."""
        found: set[str] = set()
        for binding in policy.bindings:
            if binding is None:
                continue
            if binding.HasField("condition") and not self._accept_condition(binding):
                continue
            if any(
                self._registry.is_member(self._identity, Member.parse(member))
                for member in binding.members
            ):
                found.add(binding.role)
        return found

    def _accept_condition(self, binding: Any) -> bool:
        """Decides what to do with a conditional binding.

        Returns:
            Whether the binding should be treated as granting.

        Raises:
            ConditionalBindingError: If the registry is configured to refuse
                conditional bindings outright.
        """
        condition = ConditionalBinding(
            role=binding.role,
            expression=binding.condition.expression,
            title=binding.condition.title,
        )
        self._conditional.append(condition)
        mode = self._registry.condition_mode

        if mode is ConditionMode.ERROR:
            raise ConditionalBindingError(
                f"binding for {condition.role!r} carries the condition "
                f"{condition.expression!r}, which cannot be evaluated here"
            )
        if mode is ConditionMode.DENY:
            return False

        key = (condition.role, condition.expression)
        with _warned_lock:
            unseen = key not in _warned_conditions
            _warned_conditions.add(key)
        if unseen:
            _logger.warning(
                "granting %r unconditionally: its condition %r is not evaluated",
                condition.role,
                condition.expression,
            )
        return True


def reset_condition_warnings() -> None:
    """Clears the process-wide record of conditions already warned about."""
    with _warned_lock:
        _warned_conditions.clear()
