"""Which policy binding members name which identities."""

from __future__ import annotations

import logging

import pytest

from alis.build.iam import Identity, IdentityType
from alis.build.iam.authz import (
    Member,
    MemberResolverConflictError,
    Registry,
    ServiceAccountMatch,
)

USER = Identity(
    type=IdentityType.USER, id="1934872948", email="john@example.com", group_ids=["df913r888"]
)
SA = Identity(
    type=IdentityType.SERVICE_ACCOUNT,
    id="110273892410436981091",
    email="alis-build@project.iam.gserviceaccount.com",
)
SYSTEM = Identity(type=IdentityType.SYSTEM, id="system")
BLANK = Identity(type=IdentityType.USER, id="", email="")


@pytest.mark.parametrize(
    ("text", "member_type", "member_id"),
    [
        pytest.param("user:1234", "user", "1234", id="simple"),
        pytest.param(
            "principal://iam.example.com/x",
            "principal",
            "//iam.example.com/x",
            id="splits_on_first_colon_only",
        ),
        pytest.param("deleted:user:x?uid=1", "deleted", "user:x?uid=1", id="multi_colon"),
        pytest.param("allUsers", "allUsers", "", id="no_colon"),
        pytest.param("email:", "email", "", id="trailing_colon"),
        pytest.param("", "", "", id="empty"),
    ],
)
def test_member_parsing(text: str, member_type: str, member_id: str) -> None:
    assert Member.parse(text) == Member(type=member_type, id=member_id)


@pytest.mark.parametrize(
    ("member", "identity", "expected"),
    [
        pytest.param("user:1934872948", USER, True, id="user_match"),
        pytest.param("user:other", USER, False, id="user_wrong_id"),
        pytest.param(
            "user:110273892410436981091",
            SA,
            False,
            id="user_member_never_matches_a_service_account",
        ),
        pytest.param("serviceAccount:110273892410436981091", SA, True, id="sa_by_id"),
        pytest.param(
            "serviceAccount:alis-build@project.iam.gserviceaccount.com", SA, True, id="sa_by_email"
        ),
        pytest.param("serviceAccount:1934872948", USER, False, id="sa_member_never_matches_a_user"),
        pytest.param("domain:example.com", USER, True, id="domain_match"),
        pytest.param("domain:evilexample.com", USER, False, id="domain_must_match_the_whole_label"),
        pytest.param("domain:john@example.com", USER, False, id="domain_is_not_an_address"),
        pytest.param("group:df913r888", USER, True, id="group_match"),
        pytest.param("group:other", USER, False, id="group_absent"),
        pytest.param("email:john@example.com", USER, True, id="email_match"),
        pytest.param("email:JOHN@EXAMPLE.COM", USER, False, id="email_is_case_sensitive"),
        pytest.param("system:", SYSTEM, True, id="system_matches_a_system_identity"),
        pytest.param("system", SYSTEM, True, id="system_needs_no_identifier"),
        pytest.param("system", USER, False, id="system_does_not_match_a_user"),
        pytest.param("allUsers", USER, False, id="all_users_is_not_supported"),
        pytest.param("allAuthenticatedUsers", USER, False, id="all_authenticated_is_not_supported"),
        pytest.param("User:1934872948", USER, False, id="member_type_is_case_sensitive"),
    ],
)
def test_resolver_table(
    registry: Registry, member: str, identity: Identity, expected: bool
) -> None:
    assert registry.is_member(identity, Member.parse(member)) is expected


@pytest.mark.parametrize(
    "member", ["user:", "serviceAccount:", "domain:", "group:", "email:", "user", ""]
)
@pytest.mark.parametrize("identity", [USER, SA, BLANK], ids=["user", "sa", "blank"])
def test_an_empty_identifier_never_matches(
    registry: Registry, member: str, identity: Identity
) -> None:
    """A malformed member must not become a broad grant.

    Without this guard 'email:' would match any identity that happens to carry
    no email, so a truncated policy entry would quietly authorize people.
    """
    assert registry.is_member(identity, Member.parse(member)) is False


def test_unknown_member_type_is_denied_and_logged(
    registry: Registry, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger="alis.build.iam.authz._registry"):
        assert registry.is_member(USER, Member.parse("principal://x/y")) is False
    assert "principal" in caplog.text


def test_service_account_email_match_warns(
    registry: Registry, caplog: pytest.LogCaptureFixture
) -> None:
    """The email path is newly reachable, so make each grant countable."""
    member = Member.parse("serviceAccount:alis-build@project.iam.gserviceaccount.com")
    with caplog.at_level(logging.WARNING, logger="alis.build.iam.authz._members"):
        assert registry.is_member(SA, member) is True
    assert "by email rather than id" in caplog.text


@pytest.mark.parametrize(
    ("match", "by_id", "by_email"),
    [
        pytest.param(ServiceAccountMatch.ID_OR_EMAIL, True, True, id="both"),
        pytest.param(ServiceAccountMatch.ID_ONLY, True, False, id="id_only"),
        pytest.param(ServiceAccountMatch.EMAIL_ONLY, False, True, id="email_only"),
    ],
)
def test_service_account_match_strategies(
    registry: Registry, match: ServiceAccountMatch, by_id: bool, by_email: bool
) -> None:
    registry.use_service_account_match(match)
    assert registry.is_member(SA, Member.parse(f"serviceAccount:{SA.id}")) is by_id
    assert registry.is_member(SA, Member.parse(f"serviceAccount:{SA.email}")) is by_email


def test_custom_resolver(registry: Registry) -> None:
    registry.add_member_resolver(["robot"], lambda identity, member: member.id == identity.id)
    assert registry.is_member(USER, Member.parse("robot:1934872948")) is True
    assert registry.is_member(USER, Member.parse("robot:other")) is False


def test_duplicate_registration_is_all_or_nothing(registry: Registry) -> None:
    """A partial registration would leave the registry in a state nobody chose."""
    before = registry.member_types
    with pytest.raises(MemberResolverConflictError, match="user"):
        registry.add_member_resolver(["robot", "user"], lambda identity, member: True)
    assert registry.member_types == before
    assert "robot" not in registry.member_types


def test_replace_allows_overriding_a_builtin(registry: Registry) -> None:
    registry.add_member_resolver(["user"], lambda identity, member: True, replace=True)
    assert registry.is_member(SA, Member.parse("user:anything")) is True


def test_member_str_round_trips() -> None:
    assert str(Member.parse("user:1234")) == "user:1234"
    assert str(Member.parse("allUsers")) == "allUsers"
