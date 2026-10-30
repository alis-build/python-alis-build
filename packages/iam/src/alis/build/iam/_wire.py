"""Byte-exact codec for the platform identity wire format.

Every service on the platform serialises an identity the same way, so the bytes
here are a contract rather than an implementation detail: any service can hand
an identity to any other unchanged. The encoder is hand-written rather than
delegated to :func:`json.dumps` because the format differs from Python's
defaults on four points, each reproduced below: compact separators, HTML
escaping, float formatting, and byte-wise map key ordering.

Two base64 alphabets appear in this format and must never be swapped:
:data:`Identity.policy` is standard padded base64, while JWT segments are
url-safe and unpadded. They have separate helpers for that reason.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
from dataclasses import replace
from decimal import Decimal
from typing import Any, Final

from ._errors import IdentityDecodeError
from ._identity import (
    Account,
    AccountStanding,
    ActiveAccount,
    BuildAccount,
    ClaimTime,
    IdeateAccount,
    Identity,
    IdentityType,
    Seat,
)
from ._registry import SystemConfig, system_config

_SERVICE_ACCOUNT_SUFFIX: Final = ".iam.gserviceaccount.com"

_JWT_HEADER: Final = b'{"alg":"none"}'
_JWT_HEADER_SEGMENT: Final = "eyJhbGciOiJub25lIn0"
_JWT_SIGNATURE_SEGMENT: Final = "###"

# The format escapes these so the output is safe to embed in HTML. Python's
# json does not, so we apply them after encoding each string.
_HTML_ESCAPES: Final = {
    "<": "\\u003c",
    ">": "\\u003e",
    "&": "\\u0026",
    "\u2028": "\\u2028",
    "\u2029": "\\u2029",
}


# --------------------------------------------------------------------------
# base64
# --------------------------------------------------------------------------


def b64_std_decode(text: str) -> bytes:
    """Decodes standard-alphabet base64 with padding.

    Args:
        text: Standard-alphabet base64 with correct padding.

    Returns:
        The decoded bytes.

    Raises:
        IdentityDecodeError: If text is not valid standard base64. ``validate``
            is on because Python otherwise discards non-alphabet characters
            silently, accepting input the format does not allow.
    """
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise IdentityDecodeError(f"invalid standard base64: {exc}") from exc


def b64url_encode(data: bytes) -> str:
    """Encodes url-safe base64 with the padding stripped."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(text: str) -> bytes:
    """Decodes url-safe base64 that carries no padding.

    Raises:
        IdentityDecodeError: If text is not valid unpadded url-safe base64.
    """
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise IdentityDecodeError(f"invalid url-safe base64: {exc}") from exc


# --------------------------------------------------------------------------
# encoding
# --------------------------------------------------------------------------


def _quote(value: str) -> str:
    """Renders a JSON string literal in the format's escaping style."""
    text = json.dumps(value, ensure_ascii=False)
    for char, escape in _HTML_ESCAPES.items():
        if char in text:
            text = text.replace(char, escape)
    return text


def _number(value: float) -> str:
    """Renders a float in the wire format's notation.

    The format uses ``'e'`` notation only outside ``[1e-6, 1e21)``, the shortest
    round-tripping digits, and a negative exponent with no leading zero
    (``1e-7``, not ``1e-07``). Python's ``repr`` agrees on the digits but not on
    either of those choices.
    """
    if math.isnan(value) or math.isinf(value):
        raise IdentityDecodeError(f"cannot encode non-finite float: {value!r}")

    magnitude = abs(value)
    if magnitude != 0 and (magnitude < 1e-6 or magnitude >= 1e21):
        text = repr(float(value))
        size = len(text)
        if size >= 4 and text[size - 4] == "e" and text[size - 3] == "-" and text[size - 2] == "0":
            text = text[: size - 2] + text[size - 1]
        return text

    text = format(Decimal(repr(float(value))), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _seat(seat: Seat) -> str:
    return f'{{"plan":{seat.plan:d},"seat":{seat.seat:d}}}'


def _seats(seats: dict[str, Seat] | None) -> str:
    if seats is None:
        return "null"
    # Map keys are ordered byte-wise on their encoded form, so numeric seat
    # keys come out "1","10","2". Python's str ordering is code-point
    # ordering, which agrees with UTF-8 byte ordering.
    items = ",".join(f"{_quote(key)}:{_seat(seats[key])}" for key in sorted(seats))
    return f"{{{items}}}"


def _account(account: Account) -> str:
    return (
        f'{{"seats":{_seats(account.seats)},'
        f'"ideate_account_credit_limit":{_number(account.ideate_account_credit_limit)},'
        f'"ideate_user_credit_limit":{_number(account.ideate_user_credit_limit)}}}'
    )


def _accounts(accounts: dict[str, Account] | None) -> str:
    if accounts is None:
        return "null"
    items = ",".join(f"{_quote(key)}:{_account(accounts[key])}" for key in sorted(accounts))
    return f"{{{items}}}"


def _ideate_account(account: IdeateAccount | None) -> str:
    if account is None:
        return "null"
    return (
        f'{{"account_id":{_quote(account.account_id)},'
        f'"ideate_account_credit_limit":{_number(account.ideate_account_credit_limit)},'
        f'"ideate_user_credit_limit":{_number(account.ideate_user_credit_limit)}}}'
    )


def _build_account(account: BuildAccount | None) -> str:
    if account is None:
        return "null"
    return f'{{"account_id":{_quote(account.account_id)}}}'


def _account_standing(standing: AccountStanding) -> str:
    # The format omits false and empty values here, so a blocked standing has
    # no "allow" and an empty one is {}.
    parts = []
    if standing.allow:
        parts.append('"allow":true')
    if standing.reason:
        parts.append(f'"reason":{_quote(standing.reason)}')
    return "{" + ",".join(parts) + "}"


def _claim_time(value: ClaimTime) -> str:
    parts = []
    if value.seconds:
        parts.append(f'"seconds":{value.seconds:d}')
    if value.nanos:
        parts.append(f'"nanos":{value.nanos:d}')
    return "{" + ",".join(parts) + "}"


def _active_account(account: ActiveAccount | None) -> str:
    if account is None:
        return "null"
    # The three basics are always written; the rest only when set. The optional
    # objects and `valid` are pointers in the format, omitted only when absent,
    # so valid=False is still written.
    parts = [
        f'"account_id":{_quote(account.account_id)}',
        f'"account_credit_limit":{_number(account.account_credit_limit)}',
        f'"user_credit_limit":{_number(account.user_credit_limit)}',
    ]
    if account.account_standing is not None:
        parts.append(f'"account_standing":{_account_standing(account.account_standing)}')
    if account.member_blocked_until is not None:
        parts.append(f'"member_blocked_until":{_claim_time(account.member_blocked_until)}')
    if account.valid is not None:
        parts.append(f'"valid":{"true" if account.valid else "false"}')
    if account.reason:
        parts.append(f'"reason":{_quote(account.reason)}')
    if account.comments:
        parts.append(f'"comments":{_quote(account.comments)}')
    return "{" + ",".join(parts) + "}"


def _string_list(values: list[str] | None) -> str:
    if values is None:
        return "null"
    return "[" + ",".join(_quote(value) for value in values) + "]"


def dumps(identity: Identity, /) -> bytes:
    """Serialises an identity to the exact bytes the wire format requires.

    Field order is fixed and every field is always present -- nothing is
    omitted when empty. Note the first key is ``"Type"`` with a capital T while
    every other key is lowercase; that inconsistency is part of the format.

    Args:
        identity: The identity to serialise.

    Returns:
        Compact UTF-8 JSON in the platform wire format.

    Raises:
        IdentityDecodeError: If a credit limit is NaN or infinite, neither of
            which the format can represent.
    """
    return (
        "{"
        f'"Type":{_quote(str(identity.type))},'
        f'"sub":{_quote(identity.id)},'
        f'"email":{_quote(identity.email)},'
        f'"accounts":{_accounts(identity.accounts)},'
        f'"groups":{_string_list(identity.group_ids)},'
        f'"policy":{_quote(identity.policy)},'
        f'"exp":{identity.exp:d},'
        f'"app":{_quote(identity.app)},'
        f'"scopes":{_string_list(identity.scopes)},'
        f'"eap":{"true" if identity.eap else "false"},'
        f'"active_account":{_active_account(identity.active_account)},'
        f'"active_ideate_account":{_ideate_account(identity.active_ideate_account)},'
        f'"active_build_account":{_build_account(identity.active_build_account)},'
        f'"authz_roles":{_string_list(identity.authz_roles)},'
        f'"restricted":{"true" if identity.restricted else "false"}'
        "}"
    ).encode()


# --------------------------------------------------------------------------
# decoding
# --------------------------------------------------------------------------


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise IdentityDecodeError(f"field {key!r} must be a string, got {type(value).__name__}")
    return value


def _require_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if value is None:
        return 0
    # bool is an int subclass in Python, but the format wants a real integer.
    if not isinstance(value, int) or isinstance(value, bool):
        raise IdentityDecodeError(f"field {key!r} must be an integer, got {type(value).__name__}")
    return value


def _require_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise IdentityDecodeError(f"field {key!r} must be a boolean, got {type(value).__name__}")
    return value


def _require_float(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    if value is None:
        return 0.0
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise IdentityDecodeError(f"field {key!r} must be a number, got {type(value).__name__}")
    return float(value)


def _require_str_list(payload: dict[str, Any], key: str) -> list[str] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, list):
        raise IdentityDecodeError(f"field {key!r} must be a list, got {type(value).__name__}")
    for item in value:
        if not isinstance(item, str):
            raise IdentityDecodeError(f"field {key!r} must contain only strings")
    return list(value)


def _require_mapping(payload: dict[str, Any], key: str) -> dict[str, Any] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise IdentityDecodeError(f"field {key!r} must be an object, got {type(value).__name__}")
    return value


def _parse_seats(raw: dict[str, Any] | None) -> dict[str, Seat] | None:
    if raw is None:
        return None
    seats: dict[str, Seat] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            raise IdentityDecodeError(f"seat {key!r} must be an object")
        seats[key] = Seat(plan=_require_int(value, "plan"), seat=_require_int(value, "seat"))
    return seats


def _parse_accounts(raw: dict[str, Any] | None) -> dict[str, Account] | None:
    if raw is None:
        return None
    accounts: dict[str, Account] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            raise IdentityDecodeError(f"account {key!r} must be an object")
        accounts[key] = Account(
            seats=_parse_seats(_require_mapping(value, "seats")),
            ideate_account_credit_limit=_require_float(value, "ideate_account_credit_limit"),
            ideate_user_credit_limit=_require_float(value, "ideate_user_credit_limit"),
        )
    return accounts


def _parse_ideate_account(raw: dict[str, Any] | None) -> IdeateAccount | None:
    if raw is None:
        return None
    return IdeateAccount(
        account_id=_require_str(raw, "account_id"),
        ideate_account_credit_limit=_require_float(raw, "ideate_account_credit_limit"),
        ideate_user_credit_limit=_require_float(raw, "ideate_user_credit_limit"),
    )


def _parse_build_account(raw: dict[str, Any] | None) -> BuildAccount | None:
    if raw is None:
        return None
    return BuildAccount(account_id=_require_str(raw, "account_id"))


def _coerce_type(value: str) -> IdentityType | str:
    """Keeps unrecognised type strings intact rather than rejecting them."""
    try:
        return IdentityType(value)
    except ValueError:
        return value


def _from_payload(payload: dict[str, Any]) -> Identity:
    """Builds an identity from a decoded JSON object, ignoring unknown keys.

    Ignoring unknowns is what lets real JWT payloads -- which carry ``aud``,
    ``iss`` and ``iat`` -- decode without complaint.
    """
    return Identity(
        type=_coerce_type(_require_str(payload, "Type")),
        id=_require_str(payload, "sub"),
        email=_require_str(payload, "email"),
        accounts=_parse_accounts(_require_mapping(payload, "accounts")),
        group_ids=_require_str_list(payload, "groups"),
        policy=_require_str(payload, "policy"),
        exp=_require_int(payload, "exp"),
        app=_require_str(payload, "app"),
        scopes=_require_str_list(payload, "scopes"),
        active_ideate_account=_parse_ideate_account(
            _require_mapping(payload, "active_ideate_account")
        ),
        active_build_account=_parse_build_account(
            _require_mapping(payload, "active_build_account")
        ),
        authz_roles=_require_str_list(payload, "authz_roles"),
        restricted=_require_bool(payload, "restricted"),
    )


def promote_system(identity: Identity, config: SystemConfig | None = None) -> Identity:
    """Returns identity as a system identity if its email is registered as one.

    Promotion happens on every decode path and nowhere else. Applying it during
    construction instead would silently privilege any hand-built identity that
    happened to carry a registered email, so it lives here.
    """
    if identity.type == IdentityType.SYSTEM:
        return identity
    if (config or system_config()).is_system_email(identity.email):
        # `replace` rather than a field-by-field rebuild: a field left out of
        # such a rebuild is dropped silently, and for `restricted` that would
        # hand a scoped credential its principal's full authority.
        return replace(identity, type=IdentityType.SYSTEM)
    return identity


def loads(data: bytes | str, /, config: SystemConfig | None = None) -> Identity:
    """Parses the JSON representation of an identity.

    Args:
        data: JSON in the platform wire format.
        config: System email registry to consult. Defaults to the process-wide one.

    Returns:
        The identity, promoted to system type if its email is registered.

    Raises:
        IdentityDecodeError: If the data is not JSON, is not a JSON object, or
            has a field of the wrong type.
    """
    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise IdentityDecodeError(f"invalid identity JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise IdentityDecodeError(f"identity must be a JSON object, got {type(payload).__name__}")
    return promote_system(_from_payload(payload), config)


def unsigned_jwt(identity: Identity, /) -> str:
    """Builds the unsigned JWT used by the legacy forwarded-authorization header.

    The header is always ``{"alg":"none"}`` and the signature segment is always
    the literal ``###``, so the result is not a credential and must never be
    treated as one.
    """
    return f"{_JWT_HEADER_SEGMENT}.{b64url_encode(dumps(identity))}.{_JWT_SIGNATURE_SEGMENT}"


def from_unverified_jwt(token: str, /, config: SystemConfig | None = None) -> Identity:
    """Decodes a JWT payload into an identity **without verifying anything**.

    No signature is checked, no ``exp`` is enforced, and the identity's type is
    derived from the unverified ``email`` claim. This is a parser, not an
    authenticator: never grant access on its result unless the token was
    verified elsewhere first.

    Args:
        token: A compact JWT, ``header.payload.signature``.
        config: System email registry to consult. Defaults to the process-wide one.

    Returns:
        The identity described by the payload.

    Raises:
        IdentityDecodeError: If the token is not three segments, the payload is
            not valid base64, or the payload is not a valid identity object.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise IdentityDecodeError("invalid token format, expect {hdr}.{body}.{sig}")

    try:
        payload = json.loads(b64url_decode(parts[1]))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise IdentityDecodeError(f"failed to unmarshal payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise IdentityDecodeError("token payload must be a JSON object")

    identity = _from_payload(payload)
    # The type is always re-derived from the email suffix, overwriting whatever
    # the payload claimed, so a system type never survives a JWT round trip.
    derived = (
        IdentityType.SERVICE_ACCOUNT
        if identity.email.endswith(_SERVICE_ACCOUNT_SUFFIX)
        else IdentityType.USER
    )
    return promote_system(replace(identity, type=derived), config)
