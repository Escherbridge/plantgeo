"""Inactive bundle determinism, complete refusal diagnostics and offline integrity."""

from __future__ import annotations

import copy
import hashlib
import json
import socket
import tomllib
from itertools import islice, product
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner

from agri_data_service.execution.regional_bootstrap.bundle import checked_compile, preflight_bundle, write_bundle
from agri_data_service.execution.regional_bootstrap.compiler import BootstrapError
from agri_data_service.execution.regional_bootstrap.encoding import json_bytes, toml_bytes
from agri_data_service.execution.regional_bootstrap.models import BootstrapRequest
from agri_data_service.foundation.lane_config.models import LaneConfig, ProviderConfig
from agri_data_service.interface.cli.regional_bootstrap import region_bootstrap, region_preflight

if TYPE_CHECKING:
    from pathlib import Path


def _request() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "deployment_id": "colorado-pilot",
        "region": {
            "slug": "colorado-pilot",
            "display_name": "Colorado pilot",
            "bbox": [-108, 38, -106, 40],
            "timezone": "America/Denver",
            "admin_codes": ["US-CO"],
        },
        "layers": {
            "water": {
                "source": "usgs-water-data",
                "temporal_mode": "daily_archive",
                "history": {"first_day": "2020-01-01", "last_day": "2020-01-31", "minimum_fraction": 0.9},
                "budget": {
                    "forward_max_weighted_calls": 32,
                    "gap_fill_max_weighted_calls": 112,
                    "max_concurrency": 2,
                    "turn_timeout_seconds": 1800,
                },
            }
        },
        "isolation": {"database_name": "colorado-pilot", "dedicated_database": True},
    }


def test_reordered_configuration_is_identical_and_real_lane_schemas_validate() -> None:
    raw = _request()
    first = checked_compile(BootstrapRequest.model_validate(raw))
    second = checked_compile(BootstrapRequest.model_validate(dict(reversed(tuple(raw.items())))))
    assert first.files == second.files
    lane = LaneConfig.model_validate(tomllib.loads(first.files["lanes/water-gauges-daily.toml"].decode()))
    provider = ProviderConfig.model_validate(
        tomllib.loads(first.files["lanes/_providers/usgs-water-data.toml"].decode())
    )
    assert lane.enabled is False
    assert lane.schedule.gap_fill_enabled is False
    assert lane.coverage_target is not None
    assert lane.coverage_target.minimum_fraction == raw["layers"]["water"]["history"]["minimum_fraction"]
    assert provider.api_key_env == "USGS_WATER_DATA_API_KEY"
    assert provider.endpoints["daily"].host == "api.waterdata.usgs.gov"
    assert provider.endpoints["daily"].optional_api_key is True
    service = json.loads(first.files["service.env.json"])
    web = json.loads(first.files["web.env.json"])
    assert service["PLANTGEO_REGION_MANIFEST_JSON"] == web["NEXT_PUBLIC_PLANTGEO_REGION_MANIFEST_JSON"]
    assert service["PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES"] == ""


def test_two_regions_isolate_support_and_namespace_but_history_changes_reuse_identity() -> None:
    raw = _request()
    first = checked_compile(BootstrapRequest.model_validate(raw))
    later = copy.deepcopy(raw)
    later["layers"]["water"]["history"]["last_day"] = "2020-02-29"
    extended = checked_compile(BootstrapRequest.model_validate(later))
    assert first.files["supports/water.json"] == extended.files["supports/water.json"]
    assert first.files["isolation.json"] == extended.files["isolation.json"]
    assert first.readiness["definition_sha256"] == extended.readiness["definition_sha256"]
    assert first.files["lanes/water-gauges-daily.toml"] != extended.files["lanes/water-gauges-daily.toml"]
    other = copy.deepcopy(raw)
    other["region"].update(
        slug="massachusetts-pilot", bbox=[-73, 41, -71, 43], timezone="America/New_York", admin_codes=["US-MA"]
    )
    other["isolation"]["database_name"] = "massachusetts-pilot"
    second = checked_compile(BootstrapRequest.model_validate(other))
    assert first.files["supports/water.json"] != second.files["supports/water.json"]
    assert first.readiness["geography_sha256"] != second.readiness["geography_sha256"]
    assert (
        json.loads(first.files["isolation.json"])["database_name"]
        != json.loads(second.files["isolation.json"])["database_name"]
    )


def test_every_requested_layer_gets_a_reason_without_speculative_lane_files() -> None:
    raw = _request()
    for name, source, mode in [
        ("weather", "open-meteo", "current"),
        ("fire", "nasa-firms", "current"),
        ("soil", "open-meteo-era5-land", "daily_archive"),
        ("vegetation", "sentinel2", "daily_archive"),
        ("climate", "nasa-power", "daily_archive"),
        ("unknown", "unknown", "static"),
    ]:
        selected = copy.deepcopy(raw["layers"]["water"])
        selected.update(source=source, temporal_mode=mode)
        raw["layers"][name] = selected
    bundle = checked_compile(BootstrapRequest.model_validate(raw))
    reports = {report["layer"]: report for report in json.loads(bundle.files["readiness.json"])["layers"]}
    assert set(reports) == set(raw["layers"])
    assert "current_only_history_requested" in reports["weather"]["reasons"]
    assert "source_requests_ignore_manifest" in reports["fire"]["reasons"]
    assert "source_support_not_region_generic" in reports["soil"]["reasons"]
    assert reports["unknown"]["reasons"] == ["unsupported_layer"]
    assert all(report["status"] == "blocked" for report in reports.values())
    assert bundle.readiness["production_ready"] is False
    assert sorted(name for name in bundle.files if name.startswith("lanes/") and "/_providers/" not in name) == [
        "lanes/water-gauges-daily.toml"
    ]


def test_independent_policy_and_large_footprint_do_not_emit_a_water_lane() -> None:
    raw = _request()
    raw["layers"]["water"]["corrections"] = {
        "mode": "independent",
        "window_days": 550,
        "days_per_turn": 31,
        "max_calls": 5,
    }
    raw["layers"]["water"]["repair_max_days"] = 10
    raw["region"]["bbox"] = [-125, 25, -70, 49]
    bundle = checked_compile(BootstrapRequest.model_validate(raw))
    reasons = json.loads(bundle.files["readiness.json"])["layers"][0]["reasons"]
    assert "independent_correction_requires_cadence_and_cap" in reasons
    assert "independent_repair_budget_not_implemented" in reasons
    assert "footprint_requires_partitioning" in reasons
    assert not any(name.startswith("lanes/") for name in bundle.files)


def test_output_never_overwrites_different_or_foreign_files(tmp_path: Path) -> None:
    bundle = checked_compile(BootstrapRequest.model_validate(_request()))
    output = tmp_path / "bundle"
    assert write_bundle(bundle, output) == "created"
    assert write_bundle(bundle, output) == "unchanged"
    foreign = output / "operator-note.txt"
    foreign.write_text("retain me")
    with pytest.raises(BootstrapError, match="bundle_output_differs"):
        write_bundle(bundle, output)
    assert foreign.read_text() == "retain me"


def test_implemented_recurring_policy_round_trips_without_claiming_source_admission() -> None:
    raw = _request()
    policy = {"mode": "independent", "window_days": 550, "days_per_turn": 31, "max_calls": 16, "recheck_days": 30}
    raw["layers"]["water"].update(unresolved_recheck_days=7, corrections=policy)
    bundle = checked_compile(BootstrapRequest.model_validate(raw))
    lane = LaneConfig.model_validate(tomllib.loads(bundle.files["lanes/water-gauges-daily.toml"].decode()))
    assert lane.recurring_cost is not None
    assert lane.recurring_cost.correction_recheck_days == policy["recheck_days"]
    assert lane.recurring_cost.correction_max_weighted_calls == policy["max_calls"]
    assert lane.recurring_cost.unresolved_recheck_days == raw["layers"]["water"]["unresolved_recheck_days"]
    assert not lane.enabled
    readiness = json.loads(bundle.files["readiness.json"])
    assert readiness["production_ready"] is False
    assert "region_source_support_not_admitted" in readiness["layers"][0]["reasons"]


def test_offline_preflight_never_connects_or_discloses_environment_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_network(*_args: object, **_kwargs: object) -> None:
        pytest.fail("offline compilation and preflight must not connect")

    monkeypatch.setattr(socket, "create_connection", refuse_network)
    bundle = checked_compile(BootstrapRequest.model_validate(_request()))
    output = tmp_path / "bundle"
    write_bundle(bundle, output)
    secret = "provider-secret-value"
    report = preflight_bundle(
        output,
        {
            "USGS_WATER_DATA_API_KEY": secret,
            "LOCAL_SOURCE_LOADER_DATABASE_URL": f"postgresql://owner:{secret}@localhost/colorado-pilot",
        },
    )
    assert secret not in json.dumps(report)
    assert report["network_checks_performed"] is False
    assert report["database_isolation_verified"] is False
    assert report["production_ready"] is False
    assert report["environment"]["USGS_WATER_DATA_API_KEY"] == "present_unverified"


def test_modified_bundle_is_refused_even_when_local_checksums_are_rewritten(tmp_path: Path) -> None:
    bundle = checked_compile(BootstrapRequest.model_validate(_request()))
    output = tmp_path / "bundle"
    write_bundle(bundle, output)
    modified = output / "service.env.json"
    payload = b"{}\n"
    modified.write_bytes(payload)
    with pytest.raises(BootstrapError, match="bundle_checksum_mismatch"):
        preflight_bundle(output, {})
    checksums = json.loads((output / "checksums.json").read_bytes())
    checksums["service.env.json"] = hashlib.sha256(payload).hexdigest()
    (output / "checksums.json").write_bytes(json_bytes(checksums))
    with pytest.raises(BootstrapError, match="bundle_recompile_mismatch"):
        preflight_bundle(output, {})


def test_cli_errors_do_not_echo_accidental_secret_fields(tmp_path: Path) -> None:
    config = tmp_path / "deployment.toml"
    secret = "accidental-secret-value"
    config.write_text(f'api_key = "{secret}"\n')
    result = CliRunner().invoke(region_bootstrap, ["--config", str(config), "--output", str(tmp_path / "bundle")])
    not_ready = 78
    assert result.exit_code == not_ready
    assert secret not in result.output
    assert json.loads(result.output)["error"] == "bootstrap_input_invalid"
    assert not (tmp_path / "bundle").exists()


def test_cli_valid_config_creates_an_explicitly_inactive_bundle(tmp_path: Path) -> None:
    config = tmp_path / "deployment.toml"
    config.write_bytes(toml_bytes(_request()))
    output = tmp_path / "bundle"
    result = CliRunner().invoke(region_bootstrap, ["--config", str(config), "--output", str(output)])
    not_ready = 78
    assert result.exit_code == not_ready
    payload = json.loads(result.output)
    assert "readiness" in payload, payload
    assert payload["readiness"]["production_ready"] is False
    assert (output / "checksums.json").is_file()


@pytest.mark.parametrize(("count", "accepted"), [(3900, True), (6000, False)])
def test_generated_artifact_limits_match_preflight_reader_limits(tmp_path: Path, count: int, accepted: bool) -> None:
    raw = _request()
    raw["region"]["admin_codes"] = [
        "US-" + "".join(code) for code in islice(product("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", repeat=3), count)
    ]
    output = tmp_path / "bundle"
    if accepted:
        bundle = checked_compile(BootstrapRequest.model_validate(raw))
        write_bundle(bundle, output)
        assert preflight_bundle(output, {})["bundle_integrity"] == "verified_against_current_compiler"
    else:
        with pytest.raises(BootstrapError, match=r"bundle_file_oversized|bundle_size_limit|region_manifest_oversized"):
            write_bundle(checked_compile(BootstrapRequest.model_validate(raw)), output)
        assert not output.exists()


def test_preflight_cli_reports_not_ready_without_claiming_authentication(tmp_path: Path) -> None:
    output = tmp_path / "bundle"
    write_bundle(checked_compile(BootstrapRequest.model_validate(_request())), output)
    result = CliRunner().invoke(region_preflight, ["--bundle", str(output)])
    not_ready = 78
    assert result.exit_code == not_ready
    assert json.loads(result.output)["production_ready"] is False
