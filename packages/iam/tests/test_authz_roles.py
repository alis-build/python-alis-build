"""Role checks."""

from __future__ import annotations

import asyncio

import pytest

from alis.build.iam import Identity, IdentityType, SystemConfig
from alis.build.iam.authz import Authorizer, InvalidPolicyError, Registry, decode_policy
from alis.build.iam.authz.testing import binding, encode_policy, policy

USER = Identity(type=IdentityType.USER, id="1934872948", email="john@example.com")


def test_no_policy_means_no_roles(registry: Registry) -> None:
    authorizer = Authorizer(USER, registry=registry)
    assert authorizer.roles == frozenset()
    assert authorizer.has_role(["roles/viewer"]) is False


def test_roles_are_seeded_from_the_identity_policy(registry: Registry) -> None:
    carried = policy(binding("roles/viewer", "user:1934872948"))
    identity = Identity(type=IdentityType.USER, id="1934872948", policy=encode_policy(carried))
    authorizer = Authorizer(identity, registry=registry)
    assert authorizer.roles == frozenset({"roles/viewer"})
    assert authorizer.has_role(["roles/viewer"]) is True


def test_bindings_that_do_not_name_the_caller_are_ignored(registry: Registry) -> None:
    carried = policy(binding("roles/viewer", "user:someone-else"))
    identity = Identity(type=IdentityType.USER, id="1934872948", policy=encode_policy(carried))
    assert Authorizer(identity, registry=registry).roles == frozenset()


def test_an_empty_role_list_is_always_refused(registry: Registry) -> None:
    assert Authorizer(USER, registry=registry).has_role([]) is False
    assert Authorizer(USER, registry=registry).has_role(()) is False


def test_any_one_of_the_requested_roles_is_enough(registry: Registry) -> None:
    authorizer = Authorizer(USER, registry=registry)
    authorizer.add_roles("roles/editor")
    assert authorizer.has_role(["roles/viewer", "roles/editor"]) is True


def test_ad_hoc_policies_are_not_remembered(registry: Registry) -> None:
    authorizer = Authorizer(USER, registry=registry)
    granting = policy(binding("roles/viewer", "user:1934872948"))

    assert authorizer.has_role(["roles/viewer"], granting) is True
    assert authorizer.has_role(["roles/viewer"]) is False
    assert authorizer.roles == frozenset()


def test_add_roles_from_policies_persists(registry: Registry) -> None:
    authorizer = Authorizer(USER, registry=registry)
    authorizer.add_roles_from_policies(policy(binding("roles/viewer", "user:1934872948")))
    assert authorizer.has_role(["roles/viewer"]) is True


def test_with_roles_from_policies_leaves_the_original_alone(registry: Registry) -> None:
    """The safe spelling for a handler checking many resources in one request."""
    authorizer = Authorizer(USER, registry=registry)
    extended = authorizer.with_roles_from_policies(
        policy(binding("roles/viewer", "user:1934872948"))
    )
    assert extended.has_role(["roles/viewer"]) is True
    assert authorizer.has_role(["roles/viewer"]) is False


def test_none_policies_are_skipped(registry: Registry) -> None:
    authorizer = Authorizer(USER, registry=registry)
    assert authorizer.has_role(["roles/viewer"], None, None) is False


def test_roles_snapshot_is_immutable(registry: Registry) -> None:
    authorizer = Authorizer(USER, registry=registry)
    authorizer.add_roles("roles/viewer")
    assert isinstance(authorizer.roles, frozenset)
    with pytest.raises(AttributeError):
        authorizer.roles.add("roles/admin")  # type: ignore[attr-defined]


def test_duplicate_roles_collapse(registry: Registry) -> None:
    authorizer = Authorizer(USER, registry=registry)
    authorizer.add_roles("roles/viewer", "roles/viewer")
    authorizer.add_roles_from_policies(policy(binding("roles/viewer", "user:1934872948")))
    assert authorizer.roles == frozenset({"roles/viewer"})


@pytest.mark.parametrize(
    ("identity", "privileged"),
    [
        pytest.param(USER, False, id="user"),
        pytest.param(Identity(type=IdentityType.SYSTEM, id="system"), True, id="system"),
    ],
)
def test_privileged_callers_bypass_every_check(
    registry: Registry, identity: Identity, privileged: bool
) -> None:
    authorizer = Authorizer(identity, registry=registry)
    assert authorizer.has_role(["roles/anything"]) is privileged
    # Even an empty request passes for a privileged caller.
    assert authorizer.has_role([]) is privileged


def test_admins_bypass_but_keep_their_type(registry: Registry, system_config: SystemConfig) -> None:
    system_config.add_admin_email("admin@example.com")
    admin = Identity(type=IdentityType.USER, id="a1", email="admin@example.com")
    authorizer = Authorizer(admin, registry=registry)
    assert authorizer.has_role(["roles/anything"]) is True
    assert admin.type == IdentityType.USER
    assert admin.policy_member() == "user:a1"


def test_open_roles_grant_to_anyone(registry: Registry) -> None:
    """Deliberately surprising: the check is on the requested name, not on what
    the caller holds, so any caller at all passes."""
    registry.add_open_role_permissions("roles/public", ["/x/Get"])
    assert Authorizer(USER, registry=registry).has_role(["roles/public"]) is True


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("not base64!", id="not_base64"),
        pytest.param("SGVsbG8gd29ybGQ=", id="not_a_policy"),
    ],
)
def test_undecodable_policy_raises(registry: Registry, value: str) -> None:
    identity = Identity(type=IdentityType.USER, id="x", policy=value)
    with pytest.raises(InvalidPolicyError):
        Authorizer(identity, registry=registry)


def test_url_safe_base64_is_rejected(registry: Registry) -> None:
    """The policy uses the standard alphabet; the url-safe one is a different field."""
    import base64

    raw = policy(binding("roles/viewer", "user:1934872948")).SerializeToString()
    url_safe = base64.urlsafe_b64encode(bytes([251, 255]) + raw).decode()
    identity = Identity(type=IdentityType.USER, id="x", policy=url_safe)
    with pytest.raises(InvalidPolicyError):
        Authorizer(identity, registry=registry)


def test_decode_policy_handles_the_empty_case() -> None:
    assert decode_policy("") is None


def test_from_policies_seeds_and_persists(registry: Registry) -> None:
    authorizer = Authorizer.from_policies(
        USER, policy(binding("roles/viewer", "user:1934872948")), registry=registry
    )
    assert authorizer.has_role(["roles/viewer"]) is True


async def test_concurrent_checks_do_not_bleed_into_each_other(registry: Registry) -> None:
    """A check must never mutate the authorizer, or one request's ad-hoc policy
    would grant roles to another."""
    authorizer = Authorizer(USER, registry=registry)

    async def check(index: int) -> tuple[bool, bool]:
        mine = policy(binding(f"roles/r{index}", "user:1934872948"))
        await asyncio.sleep(0)
        held = authorizer.has_role([f"roles/r{index}"], mine)
        others = authorizer.has_role([f"roles/r{(index + 1) % 50}"], mine)
        return held, others

    results = await asyncio.gather(*(check(index) for index in range(50)))
    assert all(held for held, _ in results)
    assert not any(others for _, others in results)
    assert authorizer.roles == frozenset()
