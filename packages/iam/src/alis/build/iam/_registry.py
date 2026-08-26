"""Process-wide registry of trusted system and admin emails.

Registration is startup configuration: writes take a lock, reads are lock-free
(rebinding a ``frozenset`` attribute is atomic under the GIL). That matches the
read-mostly access pattern of a request handler.
"""

from __future__ import annotations

import os
import threading

_ENV_PROJECT = "ALIS_OS_PROJECT"


class SystemConfig:
    """Trusted emails for a process.

    An email in the system set promotes a decoded identity to
    :attr:`~alis.build.iam.IdentityType.SYSTEM`. An email in the admin set
    grants the same authorization bypass while leaving the identity's type
    alone, so resource names and audit trails still say "user".
    """

    __slots__ = ("_admin", "_env_loaded", "_lock", "_system")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._system: frozenset[str] = frozenset()
        self._admin: frozenset[str] = frozenset()
        self._env_loaded = False

    def _ensure_env(self) -> None:
        """Seeds the environment's own service account on first use.

        Reading the variable lazily rather than at import time keeps the value
        independent of import order and lets tests set it with ``monkeypatch``.
        """
        if self._env_loaded:
            return
        with self._lock:
            if self._env_loaded:
                return
            project = os.environ.get(_ENV_PROJECT, "")
            if project:
                self._system |= {f"alis-build@{project}.iam.gserviceaccount.com"}
            self._env_loaded = True

    def add_system_email(self, email: str) -> None:
        """Registers email as a trusted system identity."""
        if not email:
            raise ValueError("system email must not be empty")
        self._ensure_env()
        with self._lock:
            self._system |= {email}

    def add_admin_email(self, email: str) -> None:
        """Registers email as a privileged admin identity."""
        if not email:
            raise ValueError("admin email must not be empty")
        with self._lock:
            self._admin |= {email}

    def is_system_email(self, email: str) -> bool:
        """Reports whether email is registered as a system identity."""
        if not email:
            return False
        self._ensure_env()
        return email in self._system

    def is_admin_email(self, email: str) -> bool:
        """Reports whether email is registered as an admin identity."""
        if not email:
            return False
        return email in self._admin

    @property
    def system_emails(self) -> frozenset[str]:
        """Immutable snapshot of the registered system emails."""
        self._ensure_env()
        return self._system

    @property
    def admin_emails(self) -> frozenset[str]:
        """Immutable snapshot of the registered admin emails."""
        return self._admin

    def reset(self) -> None:
        """Clears everything, including the cached environment lookup."""
        with self._lock:
            self._system = frozenset()
            self._admin = frozenset()
            self._env_loaded = False


_default = SystemConfig()


def system_config() -> SystemConfig:
    """Returns the process-wide config used when none is injected."""
    return _default


def set_system_config(config: SystemConfig, /) -> None:
    """Replaces the process-wide config. Intended for tests."""
    # Module-level singleton, deliberately swapped by tests.
    global _default
    _default = config


def add_system_email(email: str) -> None:
    """Registers email as a trusted system identity on the default config."""
    _default.add_system_email(email)


def add_admin_email(email: str) -> None:
    """Registers email as a privileged admin identity on the default config."""
    _default.add_admin_email(email)
