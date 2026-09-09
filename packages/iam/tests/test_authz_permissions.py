"""Permission checks and the registry that backs them."""

from __future__ import annotations

import pytest

from alis.build.iam import Identity, IdentityType, SystemConfig
from alis.build.iam.authz import (
    Authorizer,
    Registry,
    RegistryFrozenError,
    UnknownPermissionError,
)
from alis.build.iam.authz.testing import binding, policy

USER = Identity(type=IdentityType.USER, id="1934872948", email="john@example.com")
GET = "/example.v1.Books/Get"
DELETE = "/example.v1.Books/Delete"


def test_permission_is_granted_through_a_role(lenient_registry: Registry) -> None:
    lenient_registry.add_role_permissions("roles/viewer", [GET])
    authorizer = Authorizer(USER, registry=lenient_registry)
    granting = policy(binding("roles/viewer", "user:1934872948"))
    assert authorizer.has_permission(GET, granting) is True
    assert authorizer.has_permission(DELETE, granting) is False


def test_unknown_permission_is_denied(lenient_registry: Registry) -> None:
    assert Authorizer(USER, registry=lenient_registry).has_permission("/never/Registered") is False


def test_strict_mode_raises_on_an_unknown_permission(registry: Registry) -> None:
    """Turns a never-called setup function from a puzzling 403 into an error."""
    with pytest.raises(UnknownPermissionError, match="never/Registered"):
        Authorizer(USER, registry=registry).has_permission("/never/Registered")


def test_strict_mode_is_not_masked_by_privilege(registry: Registry) -> None:
    """A system caller must not hide the misconfiguration from the test suite."""
    system = Identity(type=IdentityType.SYSTEM, id="system")
    with pytest.raises(UnknownPermissionError):
        Authorizer(system, registry=registry).has_permission("/never/Registered")


def test_privileged_callers_pass_any_registered_permission(registry: Registry) -> None:
    registry.add_role_permissions("roles/viewer", [GET])
    system = Identity(type=IdentityType.SYSTEM, id="system")
    assert Authorizer(system, registry=registry).has_permission(GET) is True


def test_inherits_composes_permission_sets(registry: Registry) -> None:
    registry.add_role_permissions("roles/viewer", [GET])
    admin = registry.add_role_permissions("roles/admin", [DELETE], inherits=["roles/viewer"])
    assert admin == (DELETE, GET)
    assert registry.roles_for_permission(GET) == frozenset({"roles/viewer", "roles/admin"})


def test_inherits_does_not_depend_on_registration_order(registry: Registry) -> None:
    registry.add_role_permissions("roles/viewer", [GET])
    registry.add_role_permissions("roles/admin", [DELETE], inherits=["roles/viewer"])

    other = Registry(strict_permissions=True)
    other.add_role_permissions("roles/viewer", [GET])
    other.add_role_permissions("roles/admin", [DELETE], inherits=["roles/viewer"])
    assert registry.roles_for_permission(GET) == other.roles_for_permission(GET)


def test_registration_returns_an_immutable_tuple(registry: Registry) -> None:
    """So a caller composing role sets cannot reach back into the registry."""
    viewer = registry.add_role_permissions("roles/viewer", [GET])
    assert isinstance(viewer, tuple)
    registry.add_role_permissions("roles/admin", [*viewer, DELETE])
    assert registry.roles_for_permission(GET) == frozenset({"roles/viewer", "roles/admin"})


def test_open_role_grants_a_permission_to_everyone(registry: Registry) -> None:
    registry.add_open_role_permissions("roles/public", [GET])
    assert Authorizer(USER, registry=registry).has_permission(GET) is True


def test_open_role_reach_is_reported_by_audit(registry: Registry) -> None:
    """Pinning behaviour that surprises people: opening one role makes every
    permission it shares with a closed role world-reachable."""
    registry.add_open_role_permissions("roles/public", [GET])
    registry.add_role_permissions("roles/viewer", [GET])

    assert Authorizer(USER, registry=registry).has_permission(GET) is True
    findings = registry.audit()
    assert any(finding.kind == "open-role-reach" for finding in findings)
    assert GET in str(findings[0])


def test_audit_reports_an_empty_role(registry: Registry) -> None:
    registry.add_role_permissions("roles/hollow", [])
    assert any(finding.kind == "empty-role" for finding in registry.audit())


def test_audit_is_quiet_when_nothing_is_wrong(registry: Registry) -> None:
    registry.add_role_permissions("roles/viewer", [GET])
    assert registry.audit() == []


def test_freezing_closes_the_registry(registry: Registry) -> None:
    registry.add_role_permissions("roles/viewer", [GET])
    registry.freeze()
    assert registry.frozen

    with pytest.raises(RegistryFrozenError):
        registry.add_role_permissions("roles/admin", [DELETE])
    with pytest.raises(RegistryFrozenError):
        registry.add_open_role_permissions("roles/public", [GET])
    with pytest.raises(RegistryFrozenError):
        registry.add_member_resolver(["robot"], lambda identity, member: True)

    # Reads still work; only changes are refused.
    assert Authorizer(USER, registry=registry).has_permission(GET) is False


def test_freeze_logs_audit_findings(registry: Registry, caplog: pytest.LogCaptureFixture) -> None:
    registry.add_open_role_permissions("roles/public", [GET])
    registry.add_role_permissions("roles/viewer", [GET])
    with caplog.at_level("WARNING", logger="alis.build.iam.authz._registry"):
        registry.freeze()
    assert "open-role-reach" in caplog.text


def test_reset_restores_the_builtin_resolvers(registry: Registry) -> None:
    registry.add_role_permissions("roles/viewer", [GET])
    registry.add_member_resolver(["robot"], lambda identity, member: True)
    registry.reset()

    assert registry.roles_for_permission(GET) == frozenset()
    assert "robot" not in registry.member_types
    assert "user" in registry.member_types


def test_snapshot_is_independent(registry: Registry) -> None:
    registry.add_role_permissions("roles/viewer", [GET])
    saved = registry.snapshot()
    registry.add_role_permissions("roles/admin", [DELETE])

    assert saved.roles_for_permission(DELETE) == frozenset()
    assert registry.roles_for_permission(DELETE) == frozenset({"roles/admin"})


# -- restricted credentials ------------------------------------------------


CREATE = "/example.v1.Books/Create"


def test_restricted_identity_loses_open_role_permissions(lenient_registry: Registry) -> None:
    lenient_registry.add_open_role_permissions("roles/open", [CREATE])
    lenient_registry.add_role_permissions("roles/viewer", [GET])
    identity = Identity(type=IdentityType.USER, id="restricted-1234", restricted=True)
    authorizer = Authorizer(identity, registry=lenient_registry)

    # The open role no longer grants its permissions.
    assert authorizer.has_permission(CREATE) is False

    # Roles the credential explicitly carries still grant theirs.
    authorizer.add_roles("roles/viewer")
    assert authorizer.has_permission(GET) is True
    assert authorizer.has_permission(CREATE) is False


def test_restricted_admin_loses_permission_bypass(
    lenient_registry: Registry, system_config: SystemConfig
) -> None:
    lenient_registry.add_role_permissions("roles/admin", [DELETE])
    lenient_registry.add_open_role_permissions("roles/open", [CREATE])
    admin = Identity(
        type=IdentityType.USER,
        id="restricted-permission-admin-id",
        email="restricted-permission-admin@example.com",
        restricted=True,
    )
    system_config.add_admin_email(admin.email)
    authorizer = Authorizer(admin, registry=lenient_registry)

    assert authorizer.has_permission(DELETE) is False
    assert authorizer.has_permission(CREATE) is False

    authorizer.add_roles("roles/admin")
    assert authorizer.has_permission(DELETE) is True
    assert admin.type == IdentityType.USER
