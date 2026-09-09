"""Cloud Tasks scheduling for resumable callbacks.

Requires the ``tasks`` extra::

    pip install 'alis-build-lro[tasks]'

The queue must exist before a client is built. See the infrastructure contract
in the package README.
"""

from __future__ import annotations

from .._optional import missing_dependency

try:
    import google.cloud.tasks_v2 as _tasks
except ImportError as exc:  # pragma: no cover - exercised by the bare-core CI job
    raise missing_dependency(__name__, "tasks", "google-cloud-tasks") from exc

del _tasks

from ._queue import (  # noqa: E402 - must follow the dependency check above
    AsyncCloudTasksQueue,
    CloudTasksQueue,
)

__all__ = ["AsyncCloudTasksQueue", "CloudTasksQueue"]
