"""The scoped credential claims must survive every hop, not just the first parse.

The distinction between an absent allowlist and an empty one is the safety
property: absent means unrestricted, empty means restricted to no roles at all.
A transport that collapses one into the other turns the most restricted
credential there is into the least restricted one.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from alis.build.iam import (
    Identity,
    IdentityType,
    dumps,
    from_unverified_jwt,
    loads,
    unsigned_jwt,
)
from alis.build.iam.grpc import identity_from_metadata, identity_metadata
from alis.build.iam.headers import identity_from_headers, identity_headers

Transport = Callable[[Identity], Identity]

TRANSPORTS: dict[str, Transport] = {
    "wire": lambda identity: loads(dumps(identity)),
    "header": lambda identity: identity_from_headers(identity_headers(identity)),
    "metadata": lambda identity: identity_from_metadata(identity_metadata(identity)),
    "jwt": lambda identity: from_unverified_jwt(unsigned_jwt(identity)),
}

FIXTURES: dict[str, tuple[list[str] | None, bool]] = {
    "unrestricted": (None, False),
    "restricted to nothing": ([], True),
    "restricted to roles": (["roles/app.developer", "roles/viewer"], True),
    "restricted with no allowlist": (None, True),
}


@pytest.mark.parametrize("transport", TRANSPORTS.values(), ids=list(TRANSPORTS))
@pytest.mark.parametrize("fixture", FIXTURES.values(), ids=list(FIXTURES))
def test_scoped_claims_round_trip(
    transport: Transport, fixture: tuple[list[str] | None, bool]
) -> None:
    authz_roles, restricted = fixture
    identity = Identity(
        type=IdentityType.USER,
        id="1934872948",
        email="john@example.com",
        authz_roles=authz_roles,
        restricted=restricted,
    )

    out = transport(identity)

    # Asserted apart from the contents: `[] == []` would also hold for None.
    assert (out.authz_roles is None) == (authz_roles is None)
    assert out.authz_roles == authz_roles
    assert out.restricted is restricted
