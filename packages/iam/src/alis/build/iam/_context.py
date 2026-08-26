"""Ambient propagation of the caller identity.

The identity is ambient: it is bound to a context variable rather than passed
down through every call, so nothing here takes a context parameter.

Three behaviours are worth knowing before relying on this:

* ``asyncio.create_task`` and ``TaskGroup.create_task`` snapshot the current
  context, so child tasks inherit the identity and a child's change never leaks
  back to its parent.
* Awaiting a coroutine does *not* create a new context, so a bare
  :func:`set_identity` inside an awaited coroutine persists in the caller. Use
  :func:`identity_context` instead, or reset the token in a ``finally``.
* ``threading.Thread`` and ``Executor.submit`` start with an empty context, so
  the identity does not cross. ``asyncio.to_thread`` copies the context, so it
  does. To carry it explicitly, use ``contextvars.copy_context().run(fn)``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from ._errors import IdentityNotFoundError
from ._identity import Identity

_identity_var: ContextVar[Identity | None] = ContextVar("alis.build.iam.identity", default=None)


def from_context() -> Identity:
    """Returns the identity for the current context.

    Raises:
        IdentityNotFoundError: If no identity has been set.
    """
    identity = _identity_var.get()
    if identity is None:
        raise IdentityNotFoundError("no identity in the current context")
    return identity


def from_context_or_none() -> Identity | None:
    """Returns the identity for the current context, or ``None`` if unset."""
    return _identity_var.get()


def set_identity(identity: Identity, /) -> Token[Identity | None]:
    """Sets the identity for the current context.

    Prefer :func:`identity_context` unless you need to control the reset point
    yourself; an unreset identity leaks to everything that shares the context,
    including the next task on a reused server thread.

    Returns:
        A token to hand to :func:`reset_identity`.
    """
    return _identity_var.set(identity)


def reset_identity(token: Token[Identity | None], /) -> None:
    """Restores the identity that was in effect before ``token`` was issued."""
    _identity_var.reset(token)


@contextmanager
def identity_context(identity: Identity, /) -> Iterator[Identity]:
    """Binds an identity for the duration of the block.

    Usable from sync and async code alike: the set and the reset run in the
    same task, so a plain context manager is correct in both.
    """
    token = _identity_var.set(identity)
    try:
        yield identity
    finally:
        _identity_var.reset(token)
