"""Configuration, Cloud Tasks region resolution, and environment derivation."""

from __future__ import annotations

import pytest

from alis.build.lro import Config, ConfigError, config_from_env, resolve_cloud_tasks_location

ALIS_ENV = {
    "ALIS_OS_PROJECT": "p",
    "ALIS_REGION": "europe-west1",
    "ALIS_PROJECT_NR": "42",
    "ALIS_MANAGED_SPANNER_PROJECT": "sp",
    "ALIS_MANAGED_SPANNER_INSTANCE": "si",
    "ALIS_MANAGED_SPANNER_DB": "sd",
}


def make_config(**overrides: str) -> Config:
    fields = {
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
    fields.update(overrides)
    return Config(**fields)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _alis_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in ALIS_ENV.items():
        monkeypatch.setenv(key, value)


class TestRegionResolution:
    def test_supported_region_passes_through(self) -> None:
        assert resolve_cloud_tasks_location("europe-west1") == "europe-west1"

    def test_africa_south1_falls_back_to_europe_west1(self) -> None:
        # Cloud Tasks has no africa-south1. The Go client routes to the closest
        # supported European region and this port must not silently differ.
        assert resolve_cloud_tasks_location("africa-south1") == "europe-west1"

    def test_unsupported_region_names_the_supported_ones(self) -> None:
        with pytest.raises(ConfigError) as caught:
            resolve_cloud_tasks_location("mars-north1")
        message = str(caught.value)
        assert "mars-north1" in message
        assert "europe-west1" in message

    def test_supported_regions_are_listed_in_sorted_order(self) -> None:
        with pytest.raises(ConfigError) as caught:
            resolve_cloud_tasks_location("mars-north1")
        listed = str(caught.value).split("supported locations:")[1]
        regions = [region.strip() for region in listed.split(",")]
        assert regions == sorted(regions)


class TestConfigValidation:
    @pytest.mark.parametrize(
        "field",
        [
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
        ],
    )
    def test_missing_required_field_names_it(self, field: str) -> None:
        with pytest.raises(ConfigError) as caught:
            make_config(**{field: ""}).validate()
        assert field in str(caught.value)

    def test_valid_config_validates(self) -> None:
        make_config().validate()

    def test_unsupported_region_rejected_at_validation(self) -> None:
        with pytest.raises(ConfigError, match="mars-north1"):
            make_config(cloud_tasks_location="mars-north1").validate()

    def test_database_role_is_optional(self) -> None:
        assert make_config().database_role == ""

    def test_callback_prefix_defaults_and_normalises(self) -> None:
        assert make_config().callback_prefix == "/resume-operation/"
        assert make_config(callback_prefix="lro").callback_prefix == "/lro/"
        assert make_config(callback_prefix="/lro").callback_prefix == "/lro/"
        assert make_config(callback_prefix="lro/").callback_prefix == "/lro/"

    def test_local_defaults_to_none_meaning_infer(self) -> None:
        # None is the tri-state's "decide from K_SERVICE at call time", which is
        # what keeps a hand-built config behaving like the Go package.
        assert make_config().local is None


class TestFromEnv:
    def test_derives_queue_service_account_and_host(self) -> None:
        config = config_from_env("launchpad-v1")
        assert config.cloud_tasks_queue == "launchpad-v1-operations"
        assert config.cloud_tasks_service_account == "alis-build@p.iam.gserviceaccount.com"
        assert config.host == "https://launchpad-v1-42.europe-west1.run.app"
        assert config.local is None

    def test_copies_spanner_and_project_values(self) -> None:
        config = config_from_env("launchpad-v1")
        assert (config.project, config.cloud_tasks_project) == ("p", "p")
        assert config.spanner_project == "sp"
        assert config.spanner_instance == "si"
        assert config.spanner_database == "sd"

    @pytest.mark.parametrize("variable", sorted(ALIS_ENV))
    def test_missing_variable_names_it(
        self, variable: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(variable, raising=False)
        with pytest.raises(ConfigError, match=variable):
            config_from_env("launchpad-v1")

    def test_host_override_wins(self) -> None:
        config = config_from_env("launchpad-v1", host="https://custom.example.com")
        assert config.host == "https://custom.example.com"

    def test_region_is_resolved_for_cloud_tasks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The deployment region and the Cloud Tasks location are not the same
        # field: a neuron in africa-south1 queues in europe-west1.
        monkeypatch.setenv("ALIS_REGION", "africa-south1")
        config = config_from_env("launchpad-v1")
        assert config.cloud_tasks_location == "europe-west1"
        assert config.host == "https://launchpad-v1-42.africa-south1.run.app"

    def test_unsupported_region_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALIS_REGION", "mars-north1")
        with pytest.raises(ConfigError, match="mars-north1"):
            config_from_env("launchpad-v1")

    def test_import_does_not_read_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key in ALIS_ENV:
            monkeypatch.delenv(key, raising=False)
        import importlib

        import alis.build.lro as module

        importlib.reload(module)  # must not raise
