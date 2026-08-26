"""Builders for writing authorization tests.

Requires the ``authz`` extra. These exist so a service's own tests can express
policies readably::

    granting = policy(binding("roles/viewer", "user:1934872948"))
    assert authorizer.has_role(["roles/viewer"], granting)
"""

from __future__ import annotations

import base64

from google.iam.v1.policy_pb2 import Binding, Policy
from google.type.expr_pb2 import Expr

__all__ = ["binding", "encode_policy", "policy"]


def binding(role: str, /, *members: str, condition: str = "", title: str = "") -> Binding:
    """Builds one policy binding.

    Args:
        role: The role the binding grants.
        members: Member strings the binding names, such as ``"user:1234"``.
        condition: An optional condition expression. A binding that carries one
            is handled according to the registry's condition mode.
        title: A human-readable name for the condition.

    Returns:
        The binding.
    """
    value = Binding(role=role, members=list(members))
    if condition:
        value.condition.CopyFrom(Expr(expression=condition, title=title or "condition"))
    return value


def policy(*bindings: Binding) -> Policy:
    """Builds a policy from bindings."""
    return Policy(bindings=list(bindings))


def encode_policy(value: Policy, /) -> str:
    """Encodes a policy the way an identity carries one.

    Returns:
        Standard padded base64 of the serialised policy, ready to assign to
        :attr:`alis.build.iam.Identity.policy`.
    """
    return base64.b64encode(value.SerializeToString()).decode()
