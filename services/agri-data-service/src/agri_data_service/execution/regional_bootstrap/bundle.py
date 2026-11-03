"""Bounded local artifact I/O and offline checks; no environment secrets are serialized."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import tomllib
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from pydantic import ValidationError

from agri_data_service.execution.regional_bootstrap.compiler import BootstrapError, compile_bundle
from agri_data_service.execution.regional_bootstrap.models import BootstrapRequest
from agri_data_service.foundation.lane_config.loader import load_lane_configs
from agri_data_service.foundation.region.manifest import MAX_REGION_MANIFEST_BYTES, Region

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.execution.regional_bootstrap.compiler import CompiledBundle

MAX_INPUT_BYTES = MAX_REGION_MANIFEST_BYTES
MAX_BUNDLE_BYTES = 524288
MAX_BUNDLE_FILES = 32
MAX_BUNDLE_ENTRIES = 64


def _read(path: Path, limit: int = MAX_INPUT_BYTES) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise BootstrapError("bundle_file_invalid")
    try:
        with path.open("rb") as stream:
            payload = stream.read(limit + 1)
    except OSError:
        raise BootstrapError("bundle_file_unreadable") from None
    if len(payload) > limit:
        raise BootstrapError("bundle_file_oversized")
    return payload


def load_request(path: Path) -> BootstrapRequest:
    """Parse bounded TOML without leaking unknown field names or supplied values."""
    try:
        return BootstrapRequest.model_validate(tomllib.loads(_read(path).decode("utf-8")))
    except ValueError:
        raise BootstrapError("bootstrap_input_invalid") from None


def checked_compile(request: BootstrapRequest) -> CompiledBundle:
    """Convert model/source-template faults into a safe operator error."""
    try:
        bundle = compile_bundle(request)
    except BootstrapError:
        raise
    except (ValueError, OSError):
        raise BootstrapError("bootstrap_compile_invalid") from None
    _validate_artifact_bounds(bundle)
    return bundle


def _validate_artifact_bounds(bundle: CompiledBundle) -> None:
    if len(bundle.files) > MAX_BUNDLE_FILES or sum(map(len, bundle.files.values())) > MAX_BUNDLE_BYTES:
        raise BootstrapError("bundle_size_limit")
    for name, payload in bundle.files.items():
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or ":" in name or "\\" in name or str(path) != name:
            raise BootstrapError("bundle_path_invalid")
        if len(payload) > MAX_INPUT_BYTES:
            raise BootstrapError("bundle_file_oversized")


def _existing_files(directory: Path) -> dict[str, bytes]:
    if directory.is_symlink() or not directory.is_dir():
        raise BootstrapError("bundle_directory_invalid")
    files: dict[str, bytes] = {}
    total = 0
    for index, path in enumerate(directory.rglob("*")):
        if index >= MAX_BUNDLE_ENTRIES:
            raise BootstrapError("bundle_size_limit")
        if path.is_symlink():
            raise BootstrapError("bundle_symlink_refused")
        if path.is_dir():
            continue
        if len(files) >= MAX_BUNDLE_FILES:
            raise BootstrapError("bundle_size_limit")
        payload = _read(path)
        total += len(payload)
        if total > MAX_BUNDLE_BYTES:
            raise BootstrapError("bundle_size_limit")
        files[path.relative_to(directory).as_posix()] = payload
    return files


def write_bundle(bundle: CompiledBundle, output: Path) -> str:
    """Create a staged bundle or accept an identical existing one; never overwrite differing files."""
    _validate_artifact_bounds(bundle)
    if output.exists() or output.is_symlink():
        if _existing_files(output) != bundle.files:
            raise BootstrapError("bundle_output_differs")
        return "unchanged"
    parent = output.absolute().parent
    if not parent.is_dir() or parent.is_symlink():
        raise BootstrapError("bundle_output_parent_invalid")
    try:
        with tempfile.TemporaryDirectory(prefix=".plantgeo-bundle-", dir=parent) as temporary:
            staging = Path(temporary) / "bundle"
            staging.mkdir()
            for name, payload in sorted(bundle.files.items()):
                destination = staging / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
            if output.exists() or output.is_symlink():
                raise BootstrapError("bundle_output_exists")
            os.rename(staging, output)
    except OSError:
        raise BootstrapError("bundle_output_unwritable") from None
    return "created"


def _verify_files(files: Mapping[str, bytes]) -> BootstrapRequest:
    try:
        checksums = json.loads(files["checksums.json"])
        if not isinstance(checksums, dict) or set(checksums) != set(files) - {"checksums.json"}:
            raise BootstrapError("bundle_checksum_inventory_mismatch")
        for name, digest in checksums.items():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise BootstrapError("bundle_path_invalid")
            if hashlib.sha256(files[name]).hexdigest() != digest:
                raise BootstrapError("bundle_checksum_mismatch")
        request = BootstrapRequest.model_validate_json(files["deployment.json"])
    except BootstrapError:
        raise
    except (KeyError, TypeError, ValueError):
        raise BootstrapError("bundle_content_invalid") from None
    if checked_compile(request).files != files:
        raise BootstrapError("bundle_recompile_mismatch")
    return request


def _database_verdict(value: str, expected_database: str) -> str:
    if not value:
        return "missing"
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("postgresql", "postgresql+asyncpg") or not parsed.hostname:
            return "invalid"
        if unquote(parsed.path.lstrip("/")) != expected_database:
            return "database_name_mismatch"
    except ValueError:
        return "invalid"
    return "declared_target_matches_isolation_unverified"


def preflight_bundle(directory: Path, environment: Mapping[str, str]) -> dict[str, object]:
    """Verify local bundle and environment facts; never authenticate or contact external systems."""
    files = _existing_files(directory)
    request = _verify_files(files)
    try:
        region = Region.model_validate_json(files["region.json"])
        lanes_path = directory / "lanes"
        if lanes_path.exists():
            loaded = load_lane_configs(lanes_path, region)
            if loaded.quarantined or loaded.provider_failures or any(lane.enabled for lane in loaded.lanes.values()):
                raise BootstrapError("bundle_lane_validation_failed")
        readiness = json.loads(files["readiness.json"])
        service = {
            **json.loads(files["service.env.json"]),
            **json.loads(files["reader.env.json"]),
            **json.loads(files["executor.env.json"]),
        }
        web = json.loads(files["web.env.json"])
    except (KeyError, ValidationError, UnicodeError, json.JSONDecodeError):
        raise BootstrapError("bundle_content_invalid") from None
    checks: dict[str, str] = {}
    for key, expected in {**service, **web}.items():
        actual = environment.get(key)
        if key == "AGRI_PARQUET_SERVICE_URL":
            checks[key] = "present_unverified" if actual else "missing"
        elif actual is None:
            checks[key] = "missing"
        else:
            checks[key] = "matches" if actual == expected else "mismatch"
    for key in (
        "OBJECT_STORE_ENDPOINT_URL",
        "OBJECT_STORE_BUCKET",
        "OBJECT_STORE_ACCESS_KEY_ID",
        "OBJECT_STORE_SECRET_ACCESS_KEY",
    ):
        checks[key] = "present_unverified" if environment.get(key, "").strip() else "missing"
    for key in ("LOCAL_SOURCE_LOADER_DATABASE_URL", "PUBLISHED_READER_DATABASE_URL"):
        checks[key] = _database_verdict(environment.get(key, ""), request.isolation.database_name)
    for layer in readiness["layers"]:
        credential = layer.get("credential")
        if credential and credential["environment"]:
            key = credential["environment"]
            required = credential["required"] or checks.get(key) == "missing_required_credential"
            checks[key] = (
                "present_unverified"
                if environment.get(key, "").strip()
                else ("missing_required_credential" if required else "anonymous_allowed")
            )
    return {
        "schema_version": 1,
        "production_ready": False,
        "bundle_integrity": "verified_against_current_compiler",
        "network_checks_performed": False,
        "database_isolation_verified": False,
        "environment": dict(sorted(checks.items())),
        "readiness": readiness,
    }
