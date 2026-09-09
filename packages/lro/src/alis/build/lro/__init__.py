"""Long-running operations for services on the Alis Build platform.

An operation is created, persisted, resumed through a named handler, and
finished — possibly across several process lifetimes, because the callback that
resumes it may land on an instance that never ran the request that created it.

Storage is a seam. Pass any :class:`OperationStore`; the in-memory one in
:mod:`alis.build.lro.testing` makes the whole workflow testable with no cloud.
"""

from __future__ import annotations

from ._client import Client, ResumeHandler
from ._config import Config, config_from_env, resolve_cloud_tasks_location
from ._errors import (
    ConfigError,
    HandlerError,
    HandlerNotFoundError,
    LroError,
    MissingDependencyError,
    OperationNameError,
    OperationNotFoundError,
    StateCodecError,
)
from ._names import validate_operation_name
from ._operation import Operation, OperationRecord
from ._state import decode_state, encode_state
from ._store import TASK_DEADLINE, OperationStore

__version__ = "0.1.0"

__all__ = [
    "TASK_DEADLINE",
    "Client",
    "Config",
    "ConfigError",
    "HandlerError",
    "HandlerNotFoundError",
    "LroError",
    "MissingDependencyError",
    "Operation",
    "OperationNameError",
    "OperationNotFoundError",
    "OperationRecord",
    "OperationStore",
    "ResumeHandler",
    "StateCodecError",
    "__version__",
    "config_from_env",
    "decode_state",
    "encode_state",
    "resolve_cloud_tasks_location",
    "validate_operation_name",
]
