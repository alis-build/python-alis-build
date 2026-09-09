"""gRPC integration: the standard google.longrunning.Operations service.

Requires the ``grpc`` extra::

    pip install 'alis-build-lro[grpc]'

Wire it onto a server at startup::

    register_grpc(server, client)
"""

from __future__ import annotations

from .._optional import missing_dependency

try:
    import grpc as _grpc
except ImportError as exc:  # pragma: no cover - exercised by the bare-core CI job
    raise missing_dependency(__name__, "grpc", "grpcio") from exc

del _grpc

from ._server import (  # noqa: E402 - must follow the dependency check above
    OperationsServicer,
    register_grpc,
)

__all__ = ["OperationsServicer", "register_grpc"]
