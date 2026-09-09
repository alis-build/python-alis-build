"""Shared fixtures: a fully configured client over an in-memory store."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from alis.build.lro import Client, Config
from alis.build.lro.testing import InMemoryStore

_DEFAULTS: dict[str, Any] = {
    "neuron": "launchpad-v1",
    "project": "p",
    "spanner_project": "sp",
    "spanner_instance": "si",
    "spanner_database": "sd",
    "cloud_tasks_project": "p",
    "cloud_tasks_location": "europe-west1",
    "cloud_tasks_queue": "launchpad-v1-operations",
    "cloud_tasks_service_account": "alis-build@p.iam.gserviceaccount.com",
    "host": "https://launchpad-v1-42.europe-west1.run.app",
}


@pytest.fixture
def make_config() -> Callable[..., Config]:
    """Builds a valid config, with any field overridden."""

    def build(**overrides: Any) -> Config:
        return Config(**{**_DEFAULTS, **overrides})

    return build


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def client(store: InMemoryStore, make_config: Callable[..., Config]) -> Client:
    return Client(make_config(), store=store)
