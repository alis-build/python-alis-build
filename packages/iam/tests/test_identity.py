"""Identity accessors and the privilege model."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from alis.build.iam import (
    MEMBER_CREDIT_LIMIT_REACHED,
    SYSTEM_IDENTITY,
    ActiveAccount,
    BuildAccount,
    ClaimTime,
    CreditStanding,
    IdeateAccount,
    Identity,
    IdentityType,
    SystemConfig,
    Validity,
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
        pytest.param(loads('{"eap":false}'), False, id="opted_out"),
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


# -- credit standing ----------------------------------------------------------
#
# Claims are built exactly as the issuer mints them: empty values are left out,
# so a blocked standing has no "allow", and member_blocked_until is a protobuf
# Timestamp ({"seconds": ...}).

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
NEXT_MONTH = datetime(2026, 11, 1, tzinfo=UTC)
NEXT_MONTH_SECONDS = 1793491200  # 2026-11-01T00:00:00Z
AN_HOUR_AGO_SECONDS = 1792062000  # 2026-10-15T11:00:00Z
A_DAY_AFTER_NOW_SECONDS = 1792152000  # 2026-10-16T12:00:00Z

MEMBER_MESSAGE = (
    "You have reached your personal credit limit for this billing period. "
    "Please contact your account administrator to adjust your limit."
)
ACCOUNT_LIMIT_MESSAGE = (
    "Your account has reached its credit limit. "
    "Please purchase new credits or upgrade your plan to continue."
)


def _with_active(active: dict[str, object] | None) -> Identity:
    claims: dict[str, object] = {"sub": "123", "email": "jan@example.com"}
    if active is not None:
        claims["active_account"] = active
    return loads(json.dumps(claims))


@pytest.mark.parametrize(
    ("active", "account", "expected"),
    [
        pytest.param(None, "accounts/acme", CreditStanding(), id="no_active_account"),
        pytest.param(
            {"account_id": "acme"}, "accounts/acme", CreditStanding(), id="no_standing_received"
        ),
        pytest.param(
            {"account_id": "acme", "account_standing": {"allow": True}},
            "accounts/other",
            CreditStanding(),
            id="another_account",
        ),
        pytest.param(
            {"account_id": "acme", "account_standing": {"allow": True}},
            "accounts/acme",
            CreditStanding(known=True, allowed=True),
            id="allowed",
        ),
        pytest.param(
            {"account_id": "acme", "account_standing": {"allow": True}},
            "acme",
            CreditStanding(known=True, allowed=True),
            id="bare_account_id",
        ),
        pytest.param(
            {"account_id": "acme", "account_standing": {"reason": "NO_BILLING_DETAILS"}},
            "accounts/acme",
            CreditStanding(
                known=True,
                reason="NO_BILLING_DETAILS",
                message="Please add your billing details to continue.",
            ),
            id="blocked_arrives_without_allow",
        ),
        pytest.param(
            {
                "account_id": "acme",
                "account_standing": {"allow": True},
                "member_blocked_until": {"seconds": NEXT_MONTH_SECONDS},
            },
            "accounts/acme",
            CreditStanding(
                known=True,
                reason="MEMBER_CREDIT_LIMIT_REACHED",
                message=MEMBER_MESSAGE,
                blocked_until=NEXT_MONTH,
            ),
            id="member_blocked",
        ),
        pytest.param(
            {
                "account_id": "acme",
                "account_standing": {"allow": True},
                "member_blocked_until": {"seconds": AN_HOUR_AGO_SECONDS},
            },
            "accounts/acme",
            CreditStanding(known=True, allowed=True),
            id="member_block_lapsed",
        ),
        pytest.param(
            {
                "account_id": "acme",
                "account_standing": {"reason": "ACCOUNT_CREDIT_LIMIT_REACHED"},
                "member_blocked_until": {"seconds": NEXT_MONTH_SECONDS},
            },
            "accounts/acme",
            CreditStanding(
                known=True, reason="ACCOUNT_CREDIT_LIMIT_REACHED", message=ACCOUNT_LIMIT_MESSAGE
            ),
            id="account_block_outranks_member_block",
        ),
    ],
)
def test_credit_standing(
    active: dict[str, object] | None, account: str, expected: CreditStanding
) -> None:
    assert _with_active(active).credit_standing(account, NOW) == expected


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        ("NO_BILLING_DETAILS", "Please add your billing details to continue."),
        (
            "TRIAL_EXPIRED",
            "Your account trial is expired. Please upgrade your plan to continue.",
        ),
        ("ACCOUNT_ARCHIVED", "This account is archived."),
        ("ACCOUNT_CREDIT_LIMIT_REACHED", ACCOUNT_LIMIT_MESSAGE),
        ("A_REASON_ADDED_LATER", ACCOUNT_LIMIT_MESSAGE),
    ],
)
def test_credit_messages(reason: str, message: str) -> None:
    """The words users see must match what the accounts service itself returns."""
    identity = _with_active({"account_id": "acme", "account_standing": {"reason": reason}})
    assert identity.credit_standing("acme", NOW).message == message


def test_credit_standing_survives_forwarding() -> None:
    original = _with_active(
        {
            "account_id": "acme",
            "account_standing": {"allow": True},
            "member_blocked_until": {"seconds": A_DAY_AFTER_NOW_SECONDS},
        }
    )
    forwarded = loads(dumps(original))
    assert forwarded.credit_standing("accounts/acme", NOW) == CreditStanding(
        known=True,
        reason=MEMBER_CREDIT_LIMIT_REACHED,
        message=MEMBER_MESSAGE,
        blocked_until=datetime(2026, 10, 16, 12, tzinfo=UTC),
    )


def test_credit_standing_judges_the_block_at_the_given_time() -> None:
    """An explicit `now` wins over the wall clock, so verdicts are reproducible."""
    identity = _with_active(
        {
            "account_id": "acme",
            "account_standing": {"allow": True},
            "member_blocked_until": {"seconds": A_DAY_AFTER_NOW_SECONDS},
        }
    )
    assert identity.credit_standing("acme", NOW).allowed is False
    assert identity.credit_standing("acme", datetime(2026, 10, 17, tzinfo=UTC)).allowed is True


def test_member_block_ends_at_its_timestamp() -> None:
    """The block applies only while its end lies after `now`, so at that instant it has lapsed."""
    identity = _with_active(
        {
            "account_id": "acme",
            "account_standing": {"allow": True},
            "member_blocked_until": {"seconds": A_DAY_AFTER_NOW_SECONDS},
        }
    )
    end = datetime(2026, 10, 16, 12, tzinfo=UTC)
    assert identity.credit_standing("acme", end) == CreditStanding(known=True, allowed=True)


def test_member_credit_limit_reason_value() -> None:
    assert MEMBER_CREDIT_LIMIT_REACHED == "MEMBER_CREDIT_LIMIT_REACHED"


def test_credit_standing_defaults_now_to_the_current_time() -> None:
    year_2100 = 4102444800  # 2100-01-01T00:00:00Z
    identity = _with_active(
        {
            "account_id": "acme",
            "account_standing": {"allow": True},
            "member_blocked_until": {"seconds": year_2100},
        }
    )
    assert identity.credit_standing("acme").reason == MEMBER_CREDIT_LIMIT_REACHED


def test_claim_time_as_datetime_truncates_to_microseconds() -> None:
    assert ClaimTime(seconds=NEXT_MONTH_SECONDS, nanos=1500).as_datetime() == datetime(
        2026, 11, 1, 0, 0, 0, 1, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "seconds",
    [
        pytest.param(253402300800, id="year_10000"),
        pytest.param(10**20, id="beyond_time_t"),
        pytest.param(2**63 - 1, id="int64_max"),
        pytest.param(-(2**63), id="int64_min"),
    ],
)
def test_out_of_range_claim_time_raises_value_error(seconds: int) -> None:
    with pytest.raises(ValueError, match="out of range"):
        ClaimTime(seconds=seconds).as_datetime()


# -- credit validity ------------------------------------------------------------
#
# `valid` is left out when the issuer has no verdict, and false is minted as false.


@pytest.mark.parametrize(
    ("active", "expected"),
    [
        pytest.param(None, None, id="no_active_account"),
        pytest.param({"account_id": "acme"}, None, id="no_verdict"),
        pytest.param(
            {"account_id": "acme", "account_standing": {"allow": True}},
            None,
            id="a_standing_is_not_a_verdict",
        ),
        pytest.param({"account_id": "acme", "valid": True}, Validity(valid=True), id="valid"),
        pytest.param(
            {
                "account_id": "acme",
                "valid": False,
                "reason": "NO_BILLING_DETAILS",
                "comments": "This account has no billing details.",
            },
            Validity(
                valid=False,
                reason="NO_BILLING_DETAILS",
                comments="This account has no billing details.",
            ),
            id="not_valid",
        ),
    ],
)
def test_credit_validity(active: dict[str, object] | None, expected: Validity | None) -> None:
    assert _with_active(active).credit_validity() == expected


def test_credit_validity_survives_forwarding() -> None:
    original = _with_active(
        {
            "account_id": "acme",
            "valid": False,
            "reason": "MEMBER_USAGE_LIMIT_REACHED",
            "comments": "You have reached your personal usage limit.",
        }
    )
    assert loads(dumps(original)).credit_validity() == Validity(
        valid=False,
        reason="MEMBER_USAGE_LIMIT_REACHED",
        comments="You have reached your personal usage limit.",
    )
