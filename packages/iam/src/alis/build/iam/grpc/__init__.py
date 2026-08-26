"""gRPC integration: metadata helpers and identity interceptors.

Requires the ``grpc`` extra::

    pip install 'alis-build-iam[grpc]'

On the server, add the interceptor for your server flavour::

    server = grpc.aio.server(interceptors=[AsyncIdentityInterceptor()])

Handlers then read the caller with :func:`alis.build.iam.from_context`.
"""

from __future__ import annotations

from .._optional import missing_dependency

try:
    import grpc as _grpc
except ImportError as exc:  # pragma: no cover - exercised by the bare-core CI job
    raise missing_dependency(__name__, "grpc", "grpcio") from exc

del _grpc

from ._client import (  # noqa: E402 - must follow the dependency check above
    AsyncIdentityClientInterceptor,
    IdentityClientInterceptor,
    identity_client_interceptors,
)
from ._metadata import (  # noqa: E402
    LEGACY_METADATA_KEY,
    METADATA_KEY,
    identity_from_metadata,
    identity_from_metadata_or_none,
    identity_metadata,
    merge_metadata,
)
from ._server import (  # noqa: E402
    AsyncIdentityInterceptor,
    IdentityInterceptor,
)

__all__ = [
    "LEGACY_METADATA_KEY",
    "METADATA_KEY",
    "AsyncIdentityClientInterceptor",
    "AsyncIdentityInterceptor",
    "IdentityClientInterceptor",
    "IdentityInterceptor",
    "identity_client_interceptors",
    "identity_from_metadata",
    "identity_from_metadata_or_none",
    "identity_metadata",
    "merge_metadata",
]
