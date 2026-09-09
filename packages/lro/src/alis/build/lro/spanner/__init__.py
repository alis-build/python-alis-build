"""Spanner-backed persistence for operations.

Requires the ``spanner`` extra::

    pip install 'alis-build-lro[spanner]'

The backing table must exist before a store is built. See the infrastructure
contract in the package README for its schema and the Terraform that provisions
it.
"""

from __future__ import annotations

from .._optional import missing_dependency

try:
    import google.cloud.spanner as _spanner
except ImportError as exc:  # pragma: no cover - exercised by the bare-core CI job
    raise missing_dependency(__name__, "spanner", "google-cloud-spanner") from exc

del _spanner

from ._store import SpannerStore  # noqa: E402 - must follow the dependency check above

__all__ = ["SpannerStore"]
