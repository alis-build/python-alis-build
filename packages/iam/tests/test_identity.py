"""Identity accessors and the privilege model."""

from __future__ import annotations

from dataclasses import replace

import pytest

from alis.build.iam import (
    SYSTEM_IDENTITY,
    ActiveAccount,
    BuildAccount,
    IdeateAccount,
    Identity,
    IdentityType,
    SystemConfig,
    dumps,
    from_unverified_jwt,
    loads,
    promote_system,
    unsigned_jwt,
)

SA_EMAIL = "alis-build@alis-ge-prod-qg6.iam.gserviceaccount.com"


@pytest.mark.parametrize(
    ("identity", "member", "resource"),
    [
        pytest.param(
            Identity(type=IdentityType.USER, id="1934872948"),
            "user:1934872948",
            "users/1934872948",
            id="user",
        ),
        pytest.param(
            Identity(type=IdentityType.SERVICE_ACCOUNT, id="110273892410436981091", email=SA_EMAIL),
            f"serviceAccount:{SA_EMAIL}",
            "",
            id="service_account_uses_email",
        ),
        pytest.param(Identity(type=IdentityType.SYSTEM, id="system"), "system", "", id="system"),
        pytest.param(Identity(type="robot", id="r1"), "robot:r1", "", id="unknown_type"),
        pytest.param(Identity(type=""), ":", "", id="zero"),
    ],
)
def test_policy_member_and_user(identity: Identity, member: str, resource: str) -> None:
    assert identity.policy_member() == member
    assert identity.user() == resource


def test_system_identity_is_frozen() -> None:
    assert SYSTEM_IDENTITY.is_system()
    with pytest.raises(AttributeError):
        SYSTEM_IDENTITY.id = "hijacked"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("identity_type", "is_admin_email", "system", "admin", "privileged"),
    [
        pytest.param(IdentityType.USER, False, False, False, False, id="plain_user"),
        pytest.param(IdentityType.USER, True, False, True, True, id="admin_user"),
        pytest.param(IdentityType.SERVICE_ACCOUNT, False, False, False, False, id="plain_sa"),
        pytest.param(IdentityType.SYSTEM, False, True, False, True, id="system"),
        pytest.param(IdentityType.SYSTEM, True, True, True, True, id="system_and_admin"),
    ],
)
def test_privilege_matrix(
    system_config: SystemConfig,
    identity_type: IdentityType,
    is_admin_email: bool,
    system: bool,
    admin: bool,
    privileged: bool,
) -> None:
    email = "person@example.com"
    if is_admin_email:
        system_config.add_admin_email(email)
    identity = Identity(type=identity_type, id="x", email=email)
    assert identity.is_system() is system
    assert identity.is_admin() is admin
    assert identity.is_privileged() is privileged


def test_admin_keeps_user_type(system_config: SystemConfig) -> None:
    """The whole point of admin: bypass authz without disturbing audit trails."""
    system_config.add_admin_email("admin@example.com")
    identity = Identity(type=IdentityType.USER, id="admin-user-id", email="admin@example.com")
    assert identity.is_privileged()
    assert identity.type == IdentityType.USER
    assert identity.policy_member() == "user:admin-user-id"
    assert identity.user() == "users/admin-user-id"


def test_system_email_promotes_on_decode_only(system_config: SystemConfig) -> None:
    """Promotion happens during decoding and nowhere else.

    Promoting at construction time instead would silently privilege every
    hand-built identity that happened to carry a registered system email.
    """
    system_config.add_system_email("svc@example.com")
    built = Identity(type=IdentityType.USER, id="s1", email="svc@example.com")
    assert not built.is_system()

    assert loads(dumps(built)).is_system()
    assert from_unverified_jwt(unsigned_jwt(built)).is_system()
    assert promote_system(built).is_system()


def test_promotion_preserves_every_other_field(system_config: SystemConfig) -> None:
    system_config.add_system_email("svc@example.com")
    built = Identity(
        type=IdentityType.USER,
        id="s1",
        email="svc@example.com",
        group_ids=["g1"],
        policy="CgQKAggB",
        exp=42,
        app="app",
        scopes=["read"],
    )
    promoted = promote_system(built)
    assert promoted.type == IdentityType.SYSTEM
    assert (promoted.id, promoted.group_ids, promoted.policy, promoted.exp) == (
        built.id,
        built.group_ids,
        built.policy,
        built.exp,
    )


def test_empty_email_never_privileges(system_config: SystemConfig) -> None:
    """An identity with no email must not match a registry entry."""
    with pytest.raises(ValueError, match="must not be empty"):
        system_config.add_system_email("")
    with pytest.raises(ValueError, match="must not be empty"):
        system_config.add_admin_email("")
    assert not Identity(email="").is_admin()
    assert not promote_system(Identity(email="")).is_system()


def test_alis_os_project_seeds_the_environment_service_account(
    monkeypatch: pytest.MonkeyPatch, system_config: SystemConfig
) -> None:
    """Read lazily, so setting the variable after import still works."""
    monkeypatch.setenv("ALIS_OS_PROJECT", "my-project")
    assert system_config.is_system_email("alis-build@my-project.iam.gserviceaccount.com")


def test_no_env_seeds_nothing(system_config: SystemConfig) -> None:
    assert system_config.system_emails == frozenset()


def test_registry_snapshots_are_immutable(system_config: SystemConfig) -> None:
    system_config.add_admin_email("a@example.com")
    assert system_config.admin_emails == frozenset({"a@example.com"})
    assert isinstance(system_config.admin_emails, frozenset)


# -- scoped credentials ----------------------------------------------------


def test_scoped_claims_default_to_unrestricted() -> None:
    """Absent claims must mean unrestricted, or every existing token changes meaning."""
    identity = Identity(type=IdentityType.USER, id="1", email="a@example.com")
    assert identity.authz_roles is None
    assert identity.restricted is False


def test_restricted_admin_is_not_privileged(system_config: SystemConfig) -> None:
    admin = Identity(
        type=IdentityType.USER,
        id="restricted-admin-user-id",
        email="restricted-admin@example.com",
        restricted=True,
    )
    system_config.add_admin_email(admin.email)

    # The principal is still an admin; only the credential is restricted.
    assert admin.is_admin(system_config) is True
    assert admin.is_system() is False
    assert admin.is_privileged(system_config) is False
    assert admin.type == IdentityType.USER
    assert admin.policy_member() == "user:restricted-admin-user-id"

    # The same admin without the claim keeps the bypass it has always had.
    assert replace(admin, restricted=False).is_privileged(system_config) is True


def test_restricted_system_is_not_privileged() -> None:
    system = Identity(type=IdentityType.SYSTEM, id="system", restricted=True)
    assert system.is_system() is True
    assert system.is_privileged() is False
    assert SYSTEM_IDENTITY.is_privileged() is True


# -- early access program ---------------------------------------------------


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        pytest.param(Identity(), False, id="default"),
        pytest.param(Identity(eap=True), True, id="opted_in"),
        pytest.param(loads('{"eap":true}'), True, id="decoded"),
        pytest.param(loads("{}"), False, id="claim_absent"),
    ],
)
def test_is_eap(identity: Identity, expected: bool) -> None:
    assert identity.is_eap() is expected


# -- active account ---------------------------------------------------------


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        pytest.param(Identity(), "", id="none_set"),
        pytest.param(
            Identity(active_account=ActiveAccount(account_id="accounts/acme")),
            "acme",
            id="prefix_stripped",
        ),
        pytest.param(
            Identity(
                active_account=ActiveAccount(account_id="acme"),
                active_build_account=BuildAccount(account_id="other"),
            ),
            "acme",
            id="active_account_preferred",
        ),
        pytest.param(
            Identity(
                active_account=ActiveAccount(),
                active_build_account=BuildAccount(account_id="accounts/b1"),
            ),
            "b1",
            id="empty_active_falls_back_to_build",
        ),
        pytest.param(
            Identity(active_ideate_account=IdeateAccount(account_id="i1")),
            "i1",
            id="ideate_only",
        ),
        pytest.param(
            Identity(
                active_build_account=BuildAccount(account_id=""),
                active_ideate_account=IdeateAccount(account_id="i1"),
            ),
            "i1",
            id="empty_build_falls_back_to_ideate",
        ),
        pytest.param(
            loads('{"active_build_account":{"account_id":"accounts/legacy"}}'),
            "legacy",
            id="token_minted_before_consolidation",
        ),
    ],
)
def test_active_account_id(identity: Identity, expected: str) -> None:
    assert identity.active_account_id() == expected
