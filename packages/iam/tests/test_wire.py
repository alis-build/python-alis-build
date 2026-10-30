"""Compatibility with the platform identity wire format.

Every expected string here is the exact output the format requires, captured
byte for byte. A change that breaks one of these breaks interoperability with
every other service on the platform.
"""

from __future__ import annotations

import json
from dataclasses import fields, replace

import pytest

from alis.build.iam import (
    Account,
    AccountStanding,
    ActiveAccount,
    BuildAccount,
    ClaimTime,
    IdeateAccount,
    Identity,
    IdentityDecodeError,
    IdentityType,
    Seat,
    SystemConfig,
    b64_std_decode,
    b64url_decode,
    b64url_encode,
    dumps,
    from_unverified_jwt,
    loads,
    promote_system,
    unsigned_jwt,
)

CANONICAL = (
    '{"Type":"user","sub":"1934872948","email":"john@example.com","accounts":null,'
    '"groups":["df913r888"],"policy":"","exp":0,"app":"","scopes":null,'
    '"eap":false,"active_account":null,'
    '"active_ideate_account":null,"active_build_account":null,"authz_roles":null,"restricted":false}'
)

CANONICAL_JWT = (
    "eyJhbGciOiJub25lIn0."
    "eyJUeXBlIjoidXNlciIsInN1YiI6IjE5MzQ4NzI5NDgiLCJlbWFpbCI6ImpvaG5AZXhhbXBsZS5jb20iLCJh"
    "Y2NvdW50cyI6bnVsbCwiZ3JvdXBzIjpbImRmOTEzcjg4OCJdLCJwb2xpY3kiOiIiLCJleHAiOjAsImFwcCI6"
    "IiIsInNjb3BlcyI6bnVsbCwiZWFwIjpmYWxzZSwiYWN0aXZlX2FjY291bnQiOm51bGwsImFjdGl2ZV9pZGVh"
    "dGVfYWNjb3VudCI6bnVsbCwiYWN0aXZlX2J1aWxkX2FjY291bnQiOm51bGwsImF1dGh6X3JvbGVzIjpudWxs"
    "LCJyZXN0cmljdGVkIjpmYWxzZX0"
    ".###"
)

# Same identity as the conftest `user` fixture, usable inside parametrize.
USER = Identity(
    type=IdentityType.USER, id="1934872948", email="john@example.com", group_ids=["df913r888"]
)

# Bytes Go emits for USER carrying the active account and EAP claims. Captured
# from json.Marshal, never hand-written: the omitempty rules are what is tested.
GO_ACTIVE_ACCOUNT_FIXTURES = {
    "eap": (
        '{"Type":"user","sub":"1934872948","email":"john@example.com","accounts":null,'
        '"groups":["df913r888"],"policy":"","exp":0,"app":"","scopes":null,"eap":true,'
        '"active_account":null,"active_ideate_account":null,"active_build_account":null,'
        '"authz_roles":null,"restricted":false}'
    ),
    "full": (
        '{"Type":"user","sub":"1934872948","email":"john@example.com","accounts":null,'
        '"groups":["df913r888"],"policy":"","exp":0,"app":"","scopes":null,"eap":false,'
        '"active_account":{"account_id":"acme","account_credit_limit":1500.5,'
        '"user_credit_limit":250,"account_standing":{"allow":true},'
        '"member_blocked_until":{"seconds":1793491200,"nanos":500},"valid":true},'
        '"active_ideate_account":null,"active_build_account":null,"authz_roles":null,'
        '"restricted":false}'
    ),
    "invalid_with_escapes": (
        '{"Type":"user","sub":"1934872948","email":"john@example.com","accounts":null,'
        '"groups":["df913r888"],"policy":"","exp":0,"app":"","scopes":null,"eap":false,'
        '"active_account":{"account_id":"acme","account_credit_limit":0,"user_credit_limit":0,'
        '"valid":false,"reason":"NO_BILLING_DETAILS",'
        '"comments":"Billing \\u0026 limits \\u003cnone\\u003e"},"active_ideate_account":null,'
        '"active_build_account":null,"authz_roles":null,"restricted":false}'
    ),
    "empty_nested": (
        '{"Type":"user","sub":"1934872948","email":"john@example.com","accounts":null,'
        '"groups":["df913r888"],"policy":"","exp":0,"app":"","scopes":null,"eap":false,'
        '"active_account":{"account_id":"","account_credit_limit":0,"user_credit_limit":0,'
        '"account_standing":{},"member_blocked_until":{}},"active_ideate_account":null,'
        '"active_build_account":null,"authz_roles":null,"restricted":false}'
    ),
    "blocked": (
        '{"Type":"user","sub":"1934872948","email":"john@example.com","accounts":null,'
        '"groups":["df913r888"],"policy":"","exp":0,"app":"","scopes":null,"eap":false,'
        '"active_account":{"account_id":"acme","account_credit_limit":0,"user_credit_limit":0,'
        '"account_standing":{"reason":"NO_BILLING_DETAILS"}},"active_ideate_account":null,'
        '"active_build_account":null,"authz_roles":null,"restricted":false}'
    ),
}


def test_canonical_bytes(user: Identity) -> None:
    """Pins key order, the capital-T Type key, compact separators, and nulls."""
    assert dumps(user).decode() == CANONICAL


def test_unsigned_jwt(user: Identity) -> None:
    assert unsigned_jwt(user) == CANONICAL_JWT
    assert unsigned_jwt(user).split(".")[0] == "eyJhbGciOiJub25lIn0"
    assert unsigned_jwt(user).endswith(".###")


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        pytest.param(
            Identity(type=""),
            '{"Type":"","sub":"","email":"","accounts":null,"groups":null,"policy":"",'
            '"exp":0,"app":"","scopes":null,"eap":false,"active_account":null,"active_ideate_account":null,'
            '"active_build_account":null,"authz_roles":null,"restricted":false}',
            id="zero",
        ),
        pytest.param(
            Identity(type=IdentityType.USER, id="u2", accounts={}, group_ids=[], scopes=[]),
            '{"Type":"user","sub":"u2","email":"","accounts":{},"groups":[],"policy":"",'
            '"exp":0,"app":"","scopes":[],"eap":false,"active_account":null,"active_ideate_account":null,'
            '"active_build_account":null,"authz_roles":null,"restricted":false}',
            id="empty_collections_are_not_null",
        ),
        pytest.param(
            Identity(type=IdentityType.USER, id="a<b>c&d", email="a&b@example.com", app="<script>"),
            '{"Type":"user","sub":"a\\u003cb\\u003ec\\u0026d","email":"a\\u0026b@example.com",'
            '"accounts":null,"groups":null,"policy":"","exp":0,"app":"\\u003cscript\\u003e",'
            '"scopes":null,"eap":false,"active_account":null,"active_ideate_account":null,"active_build_account":null,"authz_roles":null,"restricted":false}',
            id="html_chars_escaped",
        ),
        pytest.param(
            Identity(type=IdentityType.USER, id="u1", email="jörg.müller@例え.jp"),
            '{"Type":"user","sub":"u1","email":"jörg.müller@例え.jp","accounts":null,'
            '"groups":null,"policy":"","exp":0,"app":"","scopes":null,"eap":false,"active_account":null,'
            '"active_ideate_account":null,"active_build_account":null,"authz_roles":null,"restricted":false}',
            id="non_ascii_not_escaped",
        ),
        pytest.param(
            Identity(type=IdentityType.USER, id="u8", exp=-1),
            '{"Type":"user","sub":"u8","email":"","accounts":null,"groups":null,"policy":"",'
            '"exp":-1,"app":"","scopes":null,"eap":false,"active_account":null,"active_ideate_account":null,'
            '"active_build_account":null,"authz_roles":null,"restricted":false}',
            id="negative_exp",
        ),
    ],
)
def test_encoding_quirks(identity: Identity, expected: str) -> None:
    assert dumps(identity).decode() == expected


@pytest.mark.parametrize(
    ("identity", "key"),
    [
        pytest.param(replace(USER, eap=True), "eap", id="eap"),
        pytest.param(
            replace(
                USER,
                active_account=ActiveAccount(
                    account_id="acme",
                    account_credit_limit=1500.5,
                    user_credit_limit=250,
                    account_standing=AccountStanding(allow=True),
                    member_blocked_until=ClaimTime(seconds=1793491200, nanos=500),
                    valid=True,
                ),
            ),
            "full",
            id="full",
        ),
        pytest.param(
            replace(
                USER,
                active_account=ActiveAccount(
                    account_id="acme",
                    valid=False,
                    reason="NO_BILLING_DETAILS",
                    comments="Billing & limits <none>",
                ),
            ),
            "invalid_with_escapes",
            id="invalid_with_escapes",
        ),
        pytest.param(
            replace(
                USER,
                active_account=ActiveAccount(
                    account_standing=AccountStanding(), member_blocked_until=ClaimTime()
                ),
            ),
            "empty_nested",
            id="empty_nested",
        ),
        pytest.param(
            replace(
                USER,
                active_account=ActiveAccount(
                    account_id="acme", account_standing=AccountStanding(reason="NO_BILLING_DETAILS")
                ),
            ),
            "blocked",
            id="blocked",
        ),
    ],
)
def test_active_account_encoding_matches_go(identity: Identity, key: str) -> None:
    """Mirrors Go's omitempty: a nil pointer is left out, but a false one is not."""
    assert dumps(identity).decode() == GO_ACTIVE_ACCOUNT_FIXTURES[key]


def test_map_keys_sort_byte_wise() -> None:
    """Map keys sort on their encoded form, so 10 comes before 2."""
    identity = Identity(
        type=IdentityType.USER,
        id="u4",
        accounts={
            "acc2": Account(seats={"1": Seat(1, 1)}),
            "acc10": Account(seats={"10": Seat(10, 3), "2": Seat(2, 2), "1": Seat(1, 1)}),
        },
    )
    encoded = dumps(identity).decode()
    assert '"accounts":{"acc10":' in encoded
    assert '"seats":{"1":{"plan":1,"seat":1},"10":{"plan":10,"seat":3},"2":' in encoded


def test_float_formatting_matches_go() -> None:
    """The format emits 2 not 2.0, 1e+21 not 1e21, and 1e-7 not 1e-07."""
    identity = Identity(
        type=IdentityType.USER,
        id="u5",
        accounts={
            "a": Account(ideate_account_credit_limit=0, ideate_user_credit_limit=1.5),
            "b": Account(ideate_account_credit_limit=2, ideate_user_credit_limit=1e21),
            "c": Account(ideate_account_credit_limit=0.1, ideate_user_credit_limit=1e-7),
        },
    )
    encoded = dumps(identity).decode()
    for key, account_limit, user_limit in [
        ("a", "0", "1.5"),
        ("b", "2", "1e+21"),
        ("c", "0.1", "1e-7"),
    ]:
        assert (
            f'"{key}":{{"seats":null,'
            f'"ideate_account_credit_limit":{account_limit},'
            f'"ideate_user_credit_limit":{user_limit}}}'
        ) in encoded


def test_nested_accounts_round_trip() -> None:
    identity = Identity(
        type=IdentityType.USER,
        id="u6",
        active_ideate_account=IdeateAccount("acc1", 100, 12.5),
        active_build_account=BuildAccount("acc2"),
    )
    assert loads(dumps(identity)) == identity


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_floats_rejected(value: float) -> None:
    identity = Identity(accounts={"a": Account(ideate_user_credit_limit=value)})
    with pytest.raises(IdentityDecodeError, match="non-finite"):
        dumps(identity)


def test_unknown_keys_ignored() -> None:
    """Real JWT payloads carry aud, iss and iat; decoding must drop them."""
    identity = loads(
        '{"Type":"user","sub":"u1","aud":"todo","iss":"https://example.com","iat":123}'
    )
    assert identity == Identity(type=IdentityType.USER, id="u1")


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param('{"exp":"not-an-int"}', id="exp_string"),
        pytest.param('{"exp":1.5}', id="exp_float"),
        pytest.param('{"sub":123}', id="sub_int"),
        pytest.param('{"groups":"a"}', id="groups_string"),
        pytest.param('{"groups":[1]}', id="groups_non_string"),
        pytest.param('{"accounts":[]}', id="accounts_list"),
        pytest.param("[]", id="not_an_object"),
        pytest.param("{", id="not_json"),
    ],
)
def test_wrong_types_rejected(payload: str) -> None:
    with pytest.raises(IdentityDecodeError):
        loads(payload)


def test_unknown_type_string_preserved() -> None:
    identity = loads('{"Type":"robot","sub":"r1"}')
    assert identity.type == "robot"
    assert dumps(identity).decode().startswith('{"Type":"robot"')


def test_from_unverified_jwt_derives_type_from_email() -> None:
    payload = b64url_encode(b'{"Type":"system","sub":"x","email":"a@b.iam.gserviceaccount.com"}')
    identity = from_unverified_jwt(f"eyJhbGciOiJub25lIn0.{payload}.###")
    # The type is re-derived from the email, so a claimed system type never survives.
    assert identity.type == IdentityType.SERVICE_ACCOUNT


def test_from_unverified_jwt_trusts_the_email_claim() -> None:
    """Pins a sharp edge: the caller controls the email, so it controls the type."""
    payload = b64url_encode(b'{"sub":"x","email":"attacker@evil.com.iam.gserviceaccount.com"}')
    identity = from_unverified_jwt(f"eyJhbGciOiJub25lIn0.{payload}.###")
    assert identity.type == IdentityType.SERVICE_ACCOUNT


def test_unsigned_jwt_round_trips(user: Identity) -> None:
    assert from_unverified_jwt(unsigned_jwt(user)) == user


@pytest.mark.parametrize(
    "token",
    [
        pytest.param("a.b", id="two_segments"),
        pytest.param("a.b.c.d", id="four_segments"),
        pytest.param("", id="empty"),
        pytest.param("a.!!!.c", id="bad_base64"),
        pytest.param(f"a.{b64url_encode(b'not json')}.c", id="payload_not_json"),
        pytest.param(f"a.{b64url_encode(b'[]')}.c", id="payload_not_object"),
    ],
)
def test_malformed_jwt_rejected(token: str) -> None:
    with pytest.raises(IdentityDecodeError):
        from_unverified_jwt(token)


def test_base64_alphabets_are_not_interchangeable() -> None:
    """The policy field is standard padded base64; JWT segments are url-safe raw."""
    raw = bytes(range(256))
    assert b64_std_decode(__import__("base64").b64encode(raw).decode()) == raw
    assert b64url_decode(b64url_encode(raw)) == raw

    url_safe = b64url_encode(raw)
    with pytest.raises(IdentityDecodeError):
        b64_std_decode(url_safe)


def test_standard_base64_rejects_junk() -> None:
    """Python discards non-alphabet bytes without validate=True; the format must not."""
    with pytest.raises(IdentityDecodeError):
        b64_std_decode("AA*A")


# -- reconstruction ---------------------------------------------------------


def _populated() -> Identity:
    """An identity with every field set to something distinguishable."""
    return Identity(
        type=IdentityType.USER,
        id="1934872948",
        email="john@example.com",
        accounts={
            "1": Account(
                seats={"1": Seat(plan=1, seat=2)},
                ideate_account_credit_limit=1.5,
                ideate_user_credit_limit=2.5,
            )
        },
        group_ids=["df913r888"],
        policy="",
        exp=1893456000,
        app="app-id",
        scopes=["scope-a"],
        eap=True,
        active_account=ActiveAccount(
            account_id="acme",
            account_credit_limit=10,
            user_credit_limit=5,
            account_standing=AccountStanding(allow=True),
            member_blocked_until=ClaimTime(seconds=1793491200),
            valid=False,
            reason="NO_BILLING_DETAILS",
            comments="none",
        ),
        active_ideate_account=IdeateAccount(account_id="ideate-1"),
        active_build_account=BuildAccount(account_id="build-1"),
        authz_roles=["roles/app.developer"],
        restricted=True,
    )


def test_promote_system_carries_every_field(system_config: SystemConfig) -> None:
    """A field dropped here is silently lost, and for `restricted` that fails open.

    Iterating the dataclass fields rather than listing them means a field added
    later is covered without this test being edited again.
    """
    identity = _populated()
    system_config.add_system_email(identity.email)

    promoted = promote_system(identity, system_config)

    assert promoted.type == IdentityType.SYSTEM
    for field in fields(Identity):
        if field.name == "type":
            continue
        assert getattr(promoted, field.name) == getattr(identity, field.name), field.name


# -- scoped credential claims ----------------------------------------------


@pytest.mark.parametrize(
    ("identity", "fragment"),
    [
        pytest.param(Identity(), '"authz_roles":null', id="nil_roles_are_null"),
        pytest.param(Identity(), '"restricted":false', id="unrestricted_is_false"),
        pytest.param(Identity(authz_roles=[]), '"authz_roles":[]', id="empty_roles_are_an_array"),
        pytest.param(
            Identity(authz_roles=["roles/x"]), '"authz_roles":["roles/x"]', id="named_roles"
        ),
        pytest.param(Identity(restricted=True), '"restricted":true', id="restricted_is_true"),
    ],
)
def test_scoped_claims_encoding(identity: Identity, fragment: str) -> None:
    """The tripwire for anyone omitting an empty value or normalising null.

    Rendering the empty list as null would turn the most restricted credential
    there is into an unrestricted one.
    """
    assert fragment in dumps(identity).decode()


def test_scoped_claims_are_the_last_two_keys() -> None:
    assert dumps(Identity()).decode().endswith('"authz_roles":null,"restricted":false}')


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        pytest.param({}, None, id="absent_is_unrestricted"),
        pytest.param({"authz_roles": None}, None, id="null_is_unrestricted"),
        pytest.param({"authz_roles": []}, [], id="empty_is_restricted_to_nothing"),
        pytest.param({"authz_roles": ["roles/app.developer"]}, ["roles/app.developer"], id="named"),
    ],
)
def test_authz_roles_decoding(claim: dict[str, object], expected: list[str] | None) -> None:
    identity = loads(json.dumps({"sub": "1", "email": "a@example.com", **claim}))
    # `==` cannot tell None from []; the nil-ness is what carries the meaning.
    assert (identity.authz_roles is None) == (expected is None)
    assert identity.authz_roles == expected


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        pytest.param({}, False, id="absent"),
        pytest.param({"restricted": True}, True, id="true"),
        pytest.param({"restricted": False}, False, id="false"),
    ],
)
def test_restricted_decoding(claim: dict[str, object], expected: bool) -> None:
    identity = loads(json.dumps({"sub": "1", "email": "a@example.com", **claim}))
    assert identity.restricted is expected


def test_restricted_must_be_a_boolean() -> None:
    """A string \"true\" must not quietly become truthy."""
    with pytest.raises(IdentityDecodeError, match="must be a boolean"):
        loads(json.dumps({"sub": "1", "restricted": "true"}))


def test_a_token_minted_before_these_claims_is_unrestricted() -> None:
    identity = loads(json.dumps({"sub": "1", "email": "a@example.com"}))
    assert identity.authz_roles is None
    assert identity.restricted is False
