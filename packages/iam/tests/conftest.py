"""Shared fixtures.

The system/admin email registries are process-wide and append-only, so without
an autouse reset a test that registers an admin email would silently privilege
that address for every test that runs afterwards.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from alis.build.iam import Identity, IdentityType, SystemConfig
from alis.build.iam.testing import fresh_authz_registry, fresh_system_config


@pytest.fixture(autouse=True)
def system_config(monkeypatch: pytest.MonkeyPatch) -> Iterator[SystemConfig]:
    """Gives every test an empty registry and no ALIS_OS_PROJECT."""
    monkeypatch.delenv("ALIS_OS_PROJECT", raising=False)
    with fresh_system_config() as config:
        yield config


@pytest.fixture
def user() -> Identity:
    """The reference identity the wire-format expectations are built around."""
    return Identity(
        type=IdentityType.USER,
        id="1934872948",
        email="john@example.com",
        group_ids=["df913r888"],
    )


@pytest.fixture
def registry() -> Iterator[Any]:
    """An empty authorization registry, stricter than the production default."""
    with fresh_authz_registry() as value:
        yield value


@pytest.fixture
def lenient_registry() -> Iterator[Any]:
    """An authorization registry configured the way production is."""
    from alis.build.iam.authz import ConditionMode

    with fresh_authz_registry(
        strict_permissions=False, condition_mode=ConditionMode.ALLOW
    ) as value:
        yield value


# -- authn helpers ---------------------------------------------------------


@pytest.fixture(scope="session")
def signing_key() -> Any:
    """An RSA key pair to sign test tokens with."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def jwks(signing_key: Any) -> Callable[..., dict[str, Any]]:
    """Builds the key set document an issuer would publish."""

    def build(kid: str = "key-1", *, legacy_names: bool = False, kty: str | None = "RSA") -> Any:
        numbers = signing_key.public_key().public_numbers()

        def encode(value: int) -> str:
            length = (value.bit_length() + 7) // 8
            return base64.urlsafe_b64encode(value.to_bytes(length, "big")).decode().rstrip("=")

        entry: dict[str, Any] = {"kid": kid}
        if kty is not None:
            entry["kty"] = kty
        if legacy_names:
            entry["mod"], entry["exp"] = encode(numbers.n), encode(numbers.e)
        else:
            entry["n"], entry["e"] = encode(numbers.n), encode(numbers.e)
        return {"keys": [entry]}

    return build


@pytest.fixture
def make_token(signing_key: Any) -> Callable[..., str]:
    """Builds a signed token, or a deliberately broken one."""

    def build(
        *,
        kid: str = "key-1",
        algorithm: str = "RS256",
        expires_in: int = 3600,
        sign: bool = True,
        **claims: Any,
    ) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        def segment(payload: dict[str, Any]) -> str:
            raw = json.dumps(payload, separators=(",", ":")).encode()
            return base64.urlsafe_b64encode(raw).decode().rstrip("=")

        header: dict[str, Any] = {"alg": algorithm, "typ": "JWT"}
        if kid:
            header["kid"] = kid

        body: dict[str, Any] = dict(claims)
        if expires_in is not None and "exp" not in body:
            body["exp"] = int(time.time()) + expires_in

        head, payload = segment(header), segment(body)
        if not sign:
            return f"{head}.{payload}.not-a-signature"
        signature = signing_key.sign(
            f"{head}.{payload}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
        )
        return f"{head}.{payload}.{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"

    return build
