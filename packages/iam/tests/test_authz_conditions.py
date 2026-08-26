"""Policy bindings that carry a condition.

A condition is evaluated against the resource being accessed, and a check here
is not given the resource, so the condition cannot be evaluated correctly. The
registry decides what to do about that.
"""

from __future__ import annotations

import pytest

from alis.build.iam import Identity, IdentityType
from alis.build.iam.authz import (
    Authorizer,
    ConditionalBindingError,
    ConditionMode,
    Registry,
    reset_condition_warnings,
)
from alis.build.iam.authz.testing import binding, policy

USER = Identity(type=IdentityType.USER, id="1934872948")
CONDITIONAL = policy(
    binding("roles/viewer", "user:1934872948", condition='resource.name.startsWith("books/")')
)


@pytest.fixture(autouse=True)
def _quiet_warnings() -> None:
    reset_condition_warnings()


def test_allow_mode_grants_and_records(lenient_registry: Registry) -> None:
    authorizer = Authorizer(USER, registry=lenient_registry)
    assert authorizer.has_role(["roles/viewer"], CONDITIONAL) is True

    seen = authorizer.conditional_bindings_seen
    assert len(seen) == 1
    assert seen[0].role == "roles/viewer"
    assert seen[0].expression.startswith("resource.name")


def test_allow_mode_warns_once_per_condition(
    lenient_registry: Registry, caplog: pytest.LogCaptureFixture
) -> None:
    authorizer = Authorizer(USER, registry=lenient_registry)
    with caplog.at_level("WARNING", logger="alis.build.iam.authz._authorizer"):
        for _ in range(5):
            authorizer.has_role(["roles/viewer"], CONDITIONAL)

    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 1, "a condition on a hot path must not flood the log"
    assert "is not evaluated" in warnings[0].getMessage()


def test_deny_mode_refuses_the_binding() -> None:
    registry = Registry(condition_mode=ConditionMode.DENY)
    authorizer = Authorizer(USER, registry=registry)
    assert authorizer.has_role(["roles/viewer"], CONDITIONAL) is False
    assert len(authorizer.conditional_bindings_seen) == 1


def test_error_mode_raises(registry: Registry) -> None:
    """The default in tests, so a conditional binding fails the build."""
    authorizer = Authorizer(USER, registry=registry)
    with pytest.raises(ConditionalBindingError, match="cannot be evaluated"):
        authorizer.has_role(["roles/viewer"], CONDITIONAL)


def test_unconditional_bindings_are_untouched(registry: Registry) -> None:
    plain = policy(binding("roles/viewer", "user:1934872948"))
    authorizer = Authorizer(USER, registry=registry)
    assert authorizer.has_role(["roles/viewer"], plain) is True
    assert authorizer.conditional_bindings_seen == ()


def test_a_condition_on_the_carried_policy_is_seen_at_construction() -> None:
    import base64

    registry = Registry(condition_mode=ConditionMode.DENY)
    identity = Identity(
        type=IdentityType.USER,
        id="1934872948",
        policy=base64.b64encode(CONDITIONAL.SerializeToString()).decode(),
    )
    authorizer = Authorizer(identity, registry=registry)
    assert authorizer.roles == frozenset()
    assert len(authorizer.conditional_bindings_seen) == 1
