"""Client configuration and the environment it can be derived from."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

from ._errors import ConfigError

__all__ = [
    "Config",
    "config_from_env",
    "resolve_cloud_tasks_location",
]

# Cloud Tasks is available in fewer regions than Cloud Run, so a neuron's
# deployment region is not always a valid queue location.
_SUPPORTED_CLOUD_TASKS_LOCATIONS: Final[frozenset[str]] = frozenset(
    {
        "asia-east1",
        "asia-east2",
        "asia-northeast1",
        "asia-northeast2",
        "asia-northeast3",
        "asia-south1",
        "asia-southeast1",
        "asia-southeast2",
        "australia-southeast1",
        "europe-central2",
        "europe-west1",
        "europe-west2",
        "europe-west3",
        "europe-west6",
        "northamerica-northeast1",
        "southamerica-east1",
        "us-central1",
        "us-east1",
        "us-east4",
        "us-west1",
        "us-west2",
        "us-west3",
        "us-west4",
    }
)

_CLOUD_TASKS_LOCATION_FALLBACKS: Final[dict[str, str]] = {
    # Cloud Tasks is not available in africa-south1. Route via the closest
    # supported European region.
    "africa-south1": "europe-west1",
}

_DEFAULT_CALLBACK_PREFIX: Final = "/resume-operation/"

_REQUIRED_FIELDS: Final = (
    "neuron",
    "project",
    "spanner_project",
    "spanner_instance",
    "spanner_database",
    "cloud_tasks_project",
    "cloud_tasks_location",
    "cloud_tasks_queue",
    "cloud_tasks_service_account",
    "host",
)


def resolve_cloud_tasks_location(location: str) -> str:
    """Returns a Cloud Tasks location usable for a deployment region.

    Args:
        location: The deployment region, usually ``ALIS_REGION``.

    Returns:
        The region itself when Cloud Tasks supports it, or the configured
        fallback for a region it does not.

    Raises:
        ConfigError: The region is unsupported and has no fallback. The message
            lists every supported location.
    """
    if location in _SUPPORTED_CLOUD_TASKS_LOCATIONS:
        return location
    fallback = _CLOUD_TASKS_LOCATION_FALLBACKS.get(location)
    if fallback is not None:
        return fallback
    supported = ", ".join(sorted(_SUPPORTED_CLOUD_TASKS_LOCATIONS))
    raise ConfigError(
        f"region {location!r} is not a supported Cloud Tasks location and no "
        f"fallback is configured; supported locations: {supported}"
    )


def _normalise_prefix(prefix: str) -> str:
    """Returns the prefix with exactly one leading and one trailing slash."""
    if not prefix:
        return "/"
    return "/" + prefix.strip("/") + "/"


@dataclass(frozen=True, slots=True)
class Config:
    """Everything a client needs to persist and resume operations.

    Args:
        neuron: The service this client belongs to. Also names the operations
            table and the Cloud Tasks queue, so it must match the deployed
            service name.
        project: The owning project, used to derive the operations table name.
        spanner_project: Project holding the Spanner instance.
        spanner_instance: Spanner instance id.
        spanner_database: Spanner database id.
        cloud_tasks_project: Project holding the Cloud Tasks queue.
        cloud_tasks_location: Queue location. Not necessarily the deployment
            region — see :func:`resolve_cloud_tasks_location`.
        cloud_tasks_queue: Queue id.
        cloud_tasks_service_account: Service account whose OIDC token authorises
            the callback request.
        host: Base URL callbacks are sent to.
        database_role: Optional Spanner database role to run as.
        callback_prefix: Where resumable callbacks are served. The single source
            of truth: the client builds callback URLs from it and the ASGI
            middleware matches incoming paths against it.
        local: Whether to resume in process instead of through Cloud Tasks.
            ``None`` decides per call from ``K_SERVICE``, which is what makes a
            hand-built config behave the way the Go package does.
    """

    neuron: str
    project: str
    spanner_project: str
    spanner_instance: str
    spanner_database: str
    cloud_tasks_project: str
    cloud_tasks_location: str
    cloud_tasks_queue: str
    cloud_tasks_service_account: str
    host: str
    database_role: str = ""
    callback_prefix: str = _DEFAULT_CALLBACK_PREFIX
    local: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "callback_prefix", _normalise_prefix(self.callback_prefix))

    def validate(self) -> None:
        """Checks every required value is present and the location is usable.

        Raises:
            ConfigError: A required field is empty, or the Cloud Tasks location
                is one no queue can live in. The message names the field.
        """
        for name in _REQUIRED_FIELDS:
            if not getattr(self, name):
                raise ConfigError(f"{name} is required")
        resolve_cloud_tasks_location(self.cloud_tasks_location)


def _required_env(key: str) -> str:
    value = os.environ.get(key, "")
    if not value:
        raise ConfigError(f"{key} is not set")
    return value


def config_from_env(neuron: str, *, host: str | None = None) -> Config:
    """Builds a config from the ALIS-managed environment variables.

    The callback host defaults to the Cloud Run URL pattern
    ``https://{service}-{project_number}.{region}.run.app``. Pass ``host`` when
    the service is reachable somewhere else.

    Args:
        neuron: The service name. Names the queue and the operations table.
        host: Overrides the inferred callback host.

    Returns:
        A validated config.

    Raises:
        ConfigError: A required environment variable is unset, or the region is
            not a usable Cloud Tasks location. The message names it.
    """
    project = _required_env("ALIS_OS_PROJECT")
    region = _required_env("ALIS_REGION")
    project_number = _required_env("ALIS_PROJECT_NR")
    spanner_project = _required_env("ALIS_MANAGED_SPANNER_PROJECT")
    spanner_instance = _required_env("ALIS_MANAGED_SPANNER_INSTANCE")
    spanner_database = _required_env("ALIS_MANAGED_SPANNER_DB")

    config = Config(
        neuron=neuron,
        project=project,
        spanner_project=spanner_project,
        spanner_instance=spanner_instance,
        spanner_database=spanner_database,
        cloud_tasks_project=project,
        cloud_tasks_location=resolve_cloud_tasks_location(region),
        cloud_tasks_queue=f"{neuron}-operations",
        cloud_tasks_service_account=f"alis-build@{project}.iam.gserviceaccount.com",
        # The host keeps the deployment region even when the queue falls back to
        # another one: the service is reachable where it is actually deployed.
        host=host or f"https://{neuron}-{project_number}.{region}.run.app",
    )
    config.validate()
    return config
