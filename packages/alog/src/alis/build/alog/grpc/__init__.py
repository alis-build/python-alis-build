"""gRPC integration: reading trace correlation off incoming call metadata.

Requires the ``grpc`` extra::

    pip install 'alis-build-alog[grpc]'

Add the interceptor for your server flavour::

    server = grpc.aio.server(interceptors=[AsyncTraceContextInterceptor()])

Every log line written while handling an RPC then carries that call's trace,
whichever library wrote it.
"""

from __future__ import annotations

from .._optional import missing_dependency

try:
    import grpc as _grpc
except ImportError as exc:  # pragma: no cover - exercised by the bare-core CI job
    raise missing_dependency(__name__, "grpc", "grpcio") from exc

del _grpc

from ._metadata import (  # noqa: E402 - must follow the dependency check above
    METADATA_KEY,
    trace_context_from_metadata,
    trace_metadata,
)
from ._server import (  # noqa: E402
    AsyncTraceContextInterceptor,
    TraceContextInterceptor,
)

__all__ = [
    "METADATA_KEY",
    "AsyncTraceContextInterceptor",
    "TraceContextInterceptor",
    "trace_context_from_metadata",
    "trace_metadata",
]
