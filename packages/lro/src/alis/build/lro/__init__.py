"""Long-running operations for services on the Alis Build platform.

An operation is created, persisted, resumed through a named handler, and
finished — possibly across several process lifetimes, because the callback that
resumes it may land on an instance that never ran the request that created it.

Storage is a seam. Pass any :class:`OperationStore`; the in-memory one in
:mod:`alis.build.lro.testing` makes the whole workflow testable with no cloud.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
