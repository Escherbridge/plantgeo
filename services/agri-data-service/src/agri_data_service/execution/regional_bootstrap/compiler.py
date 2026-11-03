"""Compile an inactive regional deployment using actual lane/provider schemas."""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agri_data_service.execution.regional_bootstrap.catalogue import SOURCE_PLANS
from agri_data_service.execution.regional_bootstrap.encoding import json_bytes, toml_bytes
from agri_data_service.foundation.lane_config.models import LaneConfig, ProviderConfig
from agri_data_service.foundation.region.identity import (
    IDENTITY_VERSION,
    StreamSupportDescriptor,
    definition_digest,
    geography_digest,
    is_custom_region,
)
from agri_data_service.foundation.region.layer_availability import PLATFORM_LAYER_SLUGS
from agri_data_service.foundation.region.manifest import MAX_REGION_MANIFEST_BYTES, LayerBinding, Region
from agri_data_service.ingest.policy import MAX_LATITUDE_SPAN, MAX_LONGITUDE_SPAN
from agri_data_service.ingest.usgs_water_data import tile_boxes
from agri_data_service.pipeline.parquet.region_admission import namespace_prefix

if TYPE_CHECKING:
    from agri_data_service.execution.regional_bootstrap.models import BootstrapRequest, LayerRequest

_SERVICE_ROOT_PARENT = 4
_MAX_CALLS = 10000
_MAX_CONCURRENCY = 16
_MAX_TURN_SECONDS = 3600


class BootstrapError(ValueError):
    """Public sanitized bootstrap error code."""


@dataclass(frozen=True, slots=True)
class CompiledBundle:
    files: dict[str, bytes]
    readiness: dict[str, object]


def _templates() -> Path:
    return Path(__file__).resolve().parents[_SERVICE_ROOT_PARENT] / "lanes"


def _region(request: BootstrapRequest) -> Region:
    envelope = request.region.envelope()
    bindings = [
        LayerBinding(layer_slug=layer, source_slug=plan.binding, coverage="regional" if name == "water" else "global")
        for name, layer_request in sorted(request.layers.items())
        if (plan := SOURCE_PLANS.get(name)) is not None and layer_request.source == plan.source
        for layer in plan.layers
    ]
    return Region(
        slug=request.region.slug,
        display_name=request.region.display_name,
        envelope=envelope,
        default_camera_envelope=envelope,
        crs=4326,
        lattice_pitch_degrees=0.01,
        lattice_origin_rule="floor_to_cell_origin",
        timezone=request.region.timezone,
        iso_country_codes=("US",),
        admin_codes=request.region.admin_codes,
        platform_layers=tuple(sorted(PLATFORM_LAYER_SLUGS)),
        enabled_layers=tuple(bindings),
    )


def _policy_blockers(request: LayerRequest, temporal_mode: str) -> list[str]:
    reasons: list[str] = []
    if request.temporal_mode != temporal_mode:
        reasons.append("temporal_mode_not_supported")
    if request.history is not None and temporal_mode != "daily_archive":
        reasons.append("current_only_history_requested")
    if request.history is None and temporal_mode == "daily_archive":
        reasons.append("history_target_required")
    correction = request.corrections
    if correction is not None:
        if correction.mode == "independent" and (correction.max_calls is None or correction.recheck_days is None):
            reasons.append("independent_correction_requires_cadence_and_cap")
        if correction.mode == "coupled_forward" and (
            correction.max_calls is not None or correction.recheck_days is not None
        ):
            reasons.append("coupled_correction_does_not_accept_independent_policy")
    if request.repair_max_days is not None:
        reasons.append("independent_repair_budget_not_implemented")
    budget = request.budget
    if (
        max(budget.forward_max_weighted_calls, budget.gap_fill_max_weighted_calls) > _MAX_CALLS
        or budget.max_concurrency > _MAX_CONCURRENCY
        or budget.turn_timeout_seconds > _MAX_TURN_SECONDS
    ):
        reasons.append("acquisition_budget_exceeds_bootstrap_limit")
    return reasons


def _water_lane(request: LayerRequest) -> LaneConfig:
    raw = tomllib.loads((_templates() / "water-gauges-daily.toml").read_text(encoding="utf-8"))
    target = request.history
    if target is None:
        raise BootstrapError("history_target_required")
    raw["enabled"] = False
    raw["schedule"]["gap_fill_enabled"] = False
    raw["schedule"].pop("gap_fill_enabled_at_gate", None)
    raw["budget"] = request.budget.model_dump()
    raw["coverage_target"] = target.model_dump(exclude_none=True)
    raw["days"]["floor"] = target.first_day
    raw["days"]["floor_basis"] = "operator_declared_target_not_measured_coverage"
    raw["days"].pop("revision_window_days", None)
    raw["days"].pop("revision_days_per_turn", None)
    raw.pop("recurring_cost", None)
    recurring: dict[str, object] = {"source_contract": "usgs-daily-mean-v1"}
    if request.unresolved_recheck_days is not None:
        recurring["unresolved_recheck_days"] = request.unresolved_recheck_days
    if request.corrections is not None:
        raw["days"]["revision_window_days"] = request.corrections.window_days
        raw["days"]["revision_days_per_turn"] = request.corrections.days_per_turn
        if request.corrections.mode == "independent":
            recurring["correction_recheck_days"] = request.corrections.recheck_days
            recurring["correction_max_weighted_calls"] = request.corrections.max_calls
    if len(recurring) > 1:
        raw["recurring_cost"] = recurring
    for stream in raw["streams"]:
        stream["history_floor"] = target.first_day
        stream["complete_history_floor"] = target.first_day
        stream["floor_basis"] = "operator_declared_target_not_measured_coverage"
    return LaneConfig.model_validate(raw)


def _water_support(region: Region) -> dict[str, object]:
    tiles = sorted(tile.bbox for tile in tile_boxes(region.default_camera_envelope))
    descriptor = StreamSupportDescriptor(
        "water-gauges",
        "usgs_water_data",
        "daily-discharge",
        "proposal-usgs-daily-v1",
        "water-gauges-daily-v1",
        {"request_tiles": tiles, "parameter": "00060", "statistic": "00003"},
    )
    return {
        "admitted": False,
        "support_sha256": descriptor.digest(region),
        "request_tiles": tiles,
        "request_tile_count": len(tiles),
        "completeness_unit_count": len(tiles) * 2,
        "scope": "request_tiles_not_gauge_inventory",
    }


def _provider_document(provider: ProviderConfig) -> dict[str, object]:
    """Serialize validated endpoints without asking JSON mode to encode their immutable mapping."""
    document = provider.model_dump(mode="json", exclude={"endpoints"}, exclude_none=True)
    document["endpoints"] = {
        name: endpoint.model_dump(mode="json", exclude_none=True) for name, endpoint in provider.endpoints.items()
    }
    return document


def _compile_layers(request: BootstrapRequest, region: Region, files: dict[str, bytes]) -> list[dict[str, object]]:
    reports: list[dict[str, object]] = []
    for name, selected in sorted(request.layers.items()):
        report: dict[str, object] = {
            "layer": name,
            "source": selected.source,
            "status": "blocked",
            "temporal_mode": selected.temporal_mode,
            "lane_generated": False,
        }
        reports.append(report)
        plan = SOURCE_PLANS.get(name)
        if plan is None or selected.source != plan.source:
            report["reasons"] = ["unsupported_layer" if plan is None else "unsupported_source_binding"]
            continue
        provider = ProviderConfig.model_validate(
            tomllib.loads((_templates() / "_providers" / f"{plan.provider}.toml").read_text(encoding="utf-8"))
        )
        reasons = [*plan.blockers, *_policy_blockers(selected, plan.temporal_mode)]
        bounds = region.envelope
        if bounds.east - bounds.west > MAX_LONGITUDE_SPAN or bounds.north - bounds.south > MAX_LATITUDE_SPAN:
            reasons.append("footprint_requires_partitioning")
        if selected.credential_env is not None and selected.credential_env != provider.api_key_env:
            reasons.append("unsupported_credential_environment_name")
        if selected.require_credential and provider.api_key_env is None:
            reasons.append("source_has_no_credential_contract")
        required = selected.require_credential or plan.provider == "nasa-firms"
        report["credential"] = {"environment": provider.api_key_env, "required": required}
        if name == "water" and not reasons:
            try:
                lane = _water_lane(selected)
            except ValueError:
                reasons.append("lane_policy_not_supported")
            else:
                files[f"lanes/{lane.id}.toml"] = toml_bytes(lane.model_dump(mode="json", exclude_none=True))
                files[f"lanes/_providers/{provider.id}.toml"] = toml_bytes(_provider_document(provider))
                files["supports/water.json"] = json_bytes(_water_support(region))
                report["lane_generated"] = True
                report["historical_acquisition_intended"] = True
                report["intentional_remaining_fraction"] = (
                    1.0 - selected.history.minimum_fraction if selected.history else None
                )
        report["reasons"] = sorted({*reasons, "region_source_support_not_admitted"})
    return reports


def compile_bundle(request: BootstrapRequest) -> CompiledBundle:
    """Generate all requested diagnostics without credentials, network or acquisition."""
    if request.region.bbox[0] > request.region.bbox[2]:
        raise BootstrapError("footprint_requires_partitioning")
    region = _region(request)
    if not is_custom_region(region):
        raise BootstrapError("custom_region_cannot_shadow_packaged_region")
    files: dict[str, bytes] = {}
    reports = _compile_layers(request, region, files)
    bounds = region.envelope
    blockers = [
        "source_admission_required",
        "namespace_receipt_not_created",
        "database_isolation_unverified",
        "direct_cli_acquisition_not_admitted",
    ]
    if bounds.east - bounds.west > MAX_LONGITUDE_SPAN or bounds.north - bounds.south > MAX_LATITUDE_SPAN:
        blockers.append("footprint_requires_partitioning")
    document = {
        "slug": region.slug,
        "display_name": region.display_name,
        "envelope": bounds.model_dump(),
        "default_camera_envelope": bounds.model_dump(),
        "sub_envelopes": {},
        "crs": region.crs,
        "lattice_pitch_degrees": region.lattice_pitch_degrees,
        "lattice_origin_rule": region.lattice_origin_rule,
        "timezone": region.timezone,
        "iso_country_codes": list(region.iso_country_codes),
        "admin_codes": sorted(region.admin_codes),
        "platform_layers": list(region.platform_layers),
        "enabled_layers": [binding.model_dump() for binding in region.enabled_layers],
        "analysis_lattices": {},
    }
    manifest_text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(manifest_text.encode("utf-8")) > MAX_REGION_MANIFEST_BYTES:
        raise BootstrapError("region_manifest_oversized")
    service = {
        "PLANTGEO_REGION": region.slug,
        "PLANTGEO_REGION_MANIFEST_JSON": manifest_text,
        "PLANTGEO_LANES_DIRECTORY": "/app/regional-bundle/lanes",
        "PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES": "",
        "OBJECT_STORE_PREFIX": namespace_prefix(region),
    }
    for key in (
        "OBJECT_STORE_ENDPOINT_URL",
        "OBJECT_STORE_BUCKET",
        "OBJECT_STORE_ACCESS_KEY_ID",
        "OBJECT_STORE_SECRET_ACCESS_KEY",
    ):
        service[key] = ""
    for name, selected in request.layers.items():
        plan = SOURCE_PLANS.get(name)
        if plan is not None and selected.source == plan.source:
            provider = ProviderConfig.model_validate(
                tomllib.loads((_templates() / "_providers" / f"{plan.provider}.toml").read_text(encoding="utf-8"))
            )
            if provider.api_key_env is not None:
                service[provider.api_key_env] = ""
    files["region.json"] = json_bytes(document)
    files["service.env.json"] = json_bytes(service)
    files["reader.env.json"] = json_bytes({"SERVICE_PROFILE": "published_reader", "PUBLISHED_READER_DATABASE_URL": ""})
    files["executor.env.json"] = json_bytes(
        {"LOCAL_SOURCE_LOADER_DATABASE_URL": "", "PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES": ""}
    )
    files["web.env.json"] = json_bytes(
        {
            "NEXT_PUBLIC_PLANTGEO_REGION": region.slug,
            "NEXT_PUBLIC_PLANTGEO_REGION_MANIFEST_JSON": manifest_text,
            "AGRI_PARQUET_SERVICE_URL": "",
        }
    )
    files["isolation.json"] = json_bytes(
        {
            "geography_sha256": geography_digest(region),
            "object_store_prefix": namespace_prefix(region),
            "database_name": request.isolation.database_name,
            "dedicated_database_required": True,
            "executor_database_env": "LOCAL_SOURCE_LOADER_DATABASE_URL",
            "reader_database_env": "PUBLISHED_READER_DATABASE_URL",
            "namespace_admitted": False,
        }
    )
    readiness = {
        "schema_version": 1,
        "production_ready": False,
        "identity_version": IDENTITY_VERSION,
        "definition_sha256": definition_digest(region),
        "geography_sha256": geography_digest(region),
        "deployment_id": request.deployment_id,
        "blockers": sorted(blockers),
        "layers": reports,
        "footprint_semantics": "operator_declared_us_bbox_not_state_mask_or_boundary_certification",
    }
    files["readiness.json"] = json_bytes(readiness)
    deployment = request.model_dump(mode="json", exclude_none=True)
    deployment["region"]["admin_codes"] = sorted(request.region.admin_codes)
    files["deployment.json"] = json_bytes(deployment)
    files["checksums.json"] = json_bytes(
        {name: hashlib.sha256(payload).hexdigest() for name, payload in sorted(files.items())}
    )
    return CompiledBundle(files=files, readiness=readiness)
