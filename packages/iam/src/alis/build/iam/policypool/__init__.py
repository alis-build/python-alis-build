"""Fetching several resources' policies concurrently.

Requires the ``authz`` extra::

    pip install 'alis-build-iam[authz]'

An authorization check often needs the policies of more than one resource --
the resource itself and each of its ancestors, say. Fetching them one after
another adds up their round trips; a pool overlaps them::

    pool = PolicyPool()
    pool.add("resources/a", client.get_iam_policy)
    pool.add("resources/b", client.get_iam_policy)
    collected = await pool.gather()
    authorizer.has_role(["roles/admin"], *collected.policies)

A failed fetch does not throw away the successful ones. That matters: treating
one transient failure as "no policies" turns a partial outage into a blanket
refusal, which is exactly the wrong behaviour at the wrong moment. Call
:meth:`PolicySet.raise_for_errors` when a partial answer will not do.
"""

from __future__ import annotations

from .._optional import missing_dependency

try:
    from google.iam.v1 import iam_policy_pb2 as _iam_policy_pb2
except ImportError as exc:  # pragma: no cover - exercised by the bare-core CI job
    raise missing_dependency(__name__, "authz", "grpc-google-iam-v1") from exc

del _iam_policy_pb2

from .._errors import (  # noqa: E402 - must follow the dependency check above
    PolicyFetchError,
    PoolExhaustedError,
)
from ._pool import (  # noqa: E402
    DEFAULT_LIMIT,
    PolicyFetcher,
    PolicyPool,
    PolicyResult,
    PolicySet,
    SyncPolicyFetcher,
    SyncPolicyPool,
    policies_from,
)

__all__ = [
    "DEFAULT_LIMIT",
    "PolicyFetchError",
    "PolicyFetcher",
    "PolicyPool",
    "PolicyResult",
    "PolicySet",
    "PoolExhaustedError",
    "SyncPolicyFetcher",
    "SyncPolicyPool",
    "policies_from",
]
