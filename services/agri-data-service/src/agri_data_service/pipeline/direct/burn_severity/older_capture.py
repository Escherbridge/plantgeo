"""Capture and replay the isolated 1984-2017 MTBS population; see AGENTS.md."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import httpx

from agri_data_service.ingest.mtbs import MTBS_FEATURE_SERVICE_QUERY_URL
from agri_data_service.pipeline.direct.burn_severity.capture import (
    BBOX,
    MAX_HEADER_LENGTH,
    MAX_RAW_BYTES,
    MAX_REQUESTS,
    MAX_RESPONSE_BYTES,
    MAX_SECONDS,
    CaptureBudget,
    _inventory,
    _parameters,
    _query,
    attributes_match,
)
from agri_data_service.pipeline.direct.burn_severity.current_snapshot import (
    MAX_CAPTURE_ROWS,
    MAX_MANIFEST_BYTES,
    canonical_bytes,
    digest,
    put_local_blob,
)

if TYPE_CHECKING:
    from pathlib import Path

OLDER_YEARS = tuple(range(1984, 2018))
MAX_OLDER_ROWS = 4000
GEOMETRY_PAGE_ROWS = 25
OLDER_SCHEMA = "mtbs-older-recovery/v1"


def _utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("older MTBS capture timestamp must be text")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("older MTBS capture timestamp must be UTC-aware")
    return parsed


def _identity(value: Any) -> str:
    if not isinstance(value, str) or len(value) != hashlib.sha256().digest_size * 2:
        raise ValueError("older MTBS evidence requires a lowercase SHA-256")
    if any(char not in "0123456789abcdef" for char in value):
        raise ValueError("older MTBS evidence requires a lowercase SHA-256")
    return value


def source_fingerprint(features: list[dict[str, Any]]) -> str:
    fingerprint = hashlib.sha256()
    for feature in features:
        fingerprint.update(canonical_bytes(feature) + b"\n")
    return fingerprint.hexdigest()


def _manifest(
    counts: dict[int, int], receipts: list[dict[str, object]], start: datetime, end: datetime, fingerprint: str
) -> dict[str, Any]:
    return {
        "schema": OLDER_SCHEMA,
        "mode": "full_replacement",
        "source_url": MTBS_FEATURE_SERVICE_QUERY_URL,
        "bbox": list(BBOX),
        "crs": "EPSG:4326",
        "covered_years": {"from": OLDER_YEARS[0], "to": OLDER_YEARS[-1]},
        "captured_from": start.isoformat(),
        "captured_through": end.isoformat(),
        "available_day": (end.date() + timedelta(days=1)).isoformat(),
        "capture_complete": True,
        "upstream_population_complete": False,
        "fire_season_completeness": "not_assessed",
        "source_row_count": sum(counts.values()),
        "counts_by_year": {str(year): counts[year] for year in OLDER_YEARS},
        "responses": receipts,
        "source_content_sha256": fingerprint,
        "consistency": "bounded capture interval; matching count and attribute inventories; no upstream version token",
    }


def validate_older_manifest(value: Any) -> dict[str, Any]:
    """Refuse changed scope, invented release dates and excessive evidence before allocation."""
    if not isinstance(value, dict) or len(canonical_bytes(value)) > MAX_MANIFEST_BYTES:
        raise ValueError("older MTBS manifest is invalid or excessive")
    counts = value.get("counts_by_year")
    if not isinstance(counts, dict) or set(counts) != {str(year) for year in OLDER_YEARS}:
        raise ValueError("older MTBS counts require exactly 1984-2017")
    if any(type(count) is not int or not 0 <= count <= MAX_CAPTURE_ROWS for count in counts.values()):
        raise ValueError("older MTBS year count exceeds its source inventory bound")
    if sum(counts.values()) > MAX_OLDER_ROWS:
        raise ValueError("older MTBS capture exceeds its total row cap")
    responses = value.get("responses")
    if not isinstance(responses, list) or len(responses) > MAX_REQUESTS:
        raise ValueError("older MTBS response inventory is invalid or excessive")
    start, end = _utc(value.get("captured_from")), _utc(value.get("captured_through"))
    if not timedelta(0) <= end - start <= timedelta(seconds=MAX_SECONDS):
        raise ValueError("older MTBS capture exceeds its interval cap")
    expected = _manifest(
        {int(year): count for year, count in counts.items()},
        responses,
        start,
        end,
        _identity(value.get("source_content_sha256")),
    )
    if canonical_bytes(value) != canonical_bytes(expected):
        raise ValueError("older MTBS manifest differs from its exact recovery contract")
    return expected


def _capture(output: Path) -> dict[str, object]:
    blobs = output / "blobs"
    blobs.mkdir()
    budget = CaptureBudget()
    start = datetime.now(UTC)
    receipts: list[dict[str, object]] = []
    counts: dict[int, int] = {}
    inventories: dict[int, list[dict[str, Any]]] = {}
    fingerprint = hashlib.sha256()
    with httpx.Client(follow_redirects=False, trust_env=False) as client:
        for year in OLDER_YEARS:
            counts[year], inventories[year] = _inventory(client, budget, blobs, receipts, year)
            if sum(counts.values()) > MAX_OLDER_ROWS:
                raise ValueError("older MTBS capture exceeds its total row cap")
        for year in OLDER_YEARS:
            for offset in range(0, counts[year], GEOMETRY_PAGE_ROWS):
                payload = _query(
                    client,
                    budget,
                    blobs,
                    receipts,
                    _role_parameters("geometry", year, offset),
                    f"geometry:{year}:{offset}",
                )
                features = payload.get("features")
                if not isinstance(features, list) or len(features) != min(GEOMETRY_PAGE_ROWS, counts[year] - offset):
                    raise ValueError("older MTBS geometry page is incomplete")
                if any(not isinstance(row, dict) or not isinstance(row.get("properties"), dict) for row in features):
                    raise ValueError("older MTBS geometry page has invalid properties")
                if not attributes_match(
                    [row["properties"] for row in features], inventories[year][offset : offset + len(features)]
                ):
                    raise ValueError("older MTBS geometry differs from its attribute inventory")
                for feature in features:
                    fingerprint.update(canonical_bytes(feature) + b"\n")
        for year in OLDER_YEARS:
            count, attributes = _inventory(client, budget, blobs, receipts, year)
            if count != counts[year] or canonical_bytes(attributes) != canonical_bytes(inventories[year]):
                raise ValueError("older MTBS source inventory changed during capture")
    budget.remaining()
    manifest = validate_older_manifest(_manifest(counts, receipts, start, datetime.now(UTC), fingerprint.hexdigest()))
    body = canonical_bytes(manifest)
    put_local_blob(blobs, body)
    (output / "manifest.json").write_bytes(body)
    (output / "capture-journal.json").write_bytes(
        canonical_bytes({"status": "complete", "receipts": receipts, "manifest_sha256": digest(body)})
    )
    return {
        "status": "captured_only",
        "manifest_sha256": digest(body),
        "rows": manifest["source_row_count"],
        "counts_by_year": manifest["counts_by_year"],
        "available_day": manifest["available_day"],
        "requests": budget.requests,
        "raw_bytes": budget.raw_bytes,
        "apply_authority": False,
    }


def capture_older_population(output: Path) -> dict[str, object]:
    """Fetch only the fixed public source into a new local directory."""
    if output.exists():
        raise ValueError("older MTBS capture output must not exist")
    output.mkdir(parents=True)
    try:
        return _capture(output)
    except BaseException as error:
        journal = output / "capture-journal.json"
        value = json.loads(journal.read_bytes()) if journal.exists() else {"receipts": []}
        value.update(status="failed", failure_type=type(error).__name__)
        journal.write_bytes(canonical_bytes(value))
        raise


def _role_parameters(role: str, year: int, offset: int = 0) -> dict[str, str]:
    parameters = _parameters(year)
    if role == "count":
        return {**parameters, "returnCountOnly": "true"}
    if role == "attributes":
        return {
            **parameters,
            "returnGeometry": "false",
            "outFields": "*",
            "orderByFields": "fire_id",
            "resultRecordCount": str(MAX_CAPTURE_ROWS),
        }
    return {
        **parameters,
        "f": "geojson",
        "outSR": "4326",
        "outFields": "*",
        "returnGeometry": "true",
        "orderByFields": "fire_id",
        "resultOffset": str(offset),
        "resultRecordCount": str(GEOMETRY_PAGE_ROWS),
    }


def read_local_blob(path: Path, identity: str, size: int, *, limit: int) -> bytes:
    """Read an exact bounded regular blob, refusing symlink substitution."""
    _identity(identity)
    if path.is_symlink() or not path.is_file() or type(size) is not int or not 0 <= size <= limit:
        raise ValueError("older MTBS blob shape or size is invalid")
    with path.open("rb") as handle:
        body = handle.read(size + 1)
    if len(body) != size or digest(body) != identity:
        raise ValueError("older MTBS blob identity differs from its receipt")
    return body


def _response(captured: Path, receipt: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    identity = _identity(receipt.get("sha256"))
    size = receipt.get("bytes")
    if type(size) is not int:
        raise ValueError("older MTBS blob shape or size is invalid")
    raw = read_local_blob(captured / "blobs" / identity, identity, size, limit=MAX_RESPONSE_BYTES)
    if receipt.get("status") != HTTPStatus.OK or receipt.get("body_semantics") != "decoded-http-entity":
        raise ValueError("older MTBS response lacks complete decoded-entity provenance")
    for key in ("content_encoding", "content_type", "started_at", "received_at"):
        if not isinstance(receipt.get(key), str) or len(receipt[key]) > MAX_HEADER_LENGTH:
            raise ValueError("older MTBS response metadata is invalid")
    if not (
        _utc(manifest["captured_from"])
        <= _utc(receipt["started_at"])
        <= _utc(receipt["received_at"])
        <= _utc(manifest["captured_through"])
    ):
        raise ValueError("older MTBS response is outside its capture interval")
    role, year, *offset = receipt["role"].split(":")
    if receipt.get("parameters") != _role_parameters(role, int(year), int(offset[0]) if offset else 0):
        raise ValueError("older MTBS query differs from its declared evidence role")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("error"):
        raise ValueError("older MTBS archived response is not a successful object")
    return payload


def validate_older_capture(captured: Path, expected_sha256: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:  # noqa: PLR0912, PLR0915 - ordered immutable evidence graph checks
    """Replay all count, attribute and geometry evidence without network or publication."""
    path = captured / "manifest.json"
    if captured.is_symlink() or (captured / "blobs").is_symlink() or path.is_symlink():
        raise ValueError("older MTBS capture paths must not be symlinks")
    body = read_local_blob(path, expected_sha256, path.stat().st_size, limit=MAX_MANIFEST_BYTES)
    manifest = validate_older_manifest(json.loads(body))
    if canonical_bytes(manifest) != body:
        raise ValueError("older MTBS source manifest must be canonical")
    counts = {int(year): count for year, count in manifest["counts_by_year"].items()}
    inventories = [role for year in OLDER_YEARS for role in (f"count:{year}", f"attributes:{year}")]
    geometry_roles = [
        f"geometry:{year}:{offset}" for year in OLDER_YEARS for offset in range(0, counts[year], GEOMETRY_PAGE_ROWS)
    ]
    roles = [*inventories, *geometry_roles, *inventories]
    receipts = manifest["responses"]
    if len(roles) > MAX_REQUESTS or any(not isinstance(receipt, dict) for receipt in receipts):
        raise ValueError("older MTBS response graph is invalid or excessive")
    if [receipt.get("role") for receipt in receipts] != roles:
        raise ValueError("older MTBS response graph is incomplete or out of order")
    attributes: dict[int, list[dict[str, Any]]] = {}
    features: list[dict[str, Any]] = []
    consumed = 0
    for receipt in receipts:
        size = receipt.get("bytes")
        if type(size) is not int or not 0 <= size <= MAX_RESPONSE_BYTES:
            raise ValueError("older MTBS response byte count is invalid")
        consumed += size
        if consumed > MAX_RAW_BYTES:
            raise ValueError("older MTBS capture exceeds its raw-byte cap")
        payload = _response(captured, receipt, manifest)
        role, year_text, *offset_text = receipt["role"].split(":")
        year = int(year_text)
        if role == "count":
            if type(payload.get("count")) is not int or payload["count"] != counts[year]:
                raise ValueError("older MTBS archived count differs from its manifest")
            continue
        rows = payload.get("features")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("older MTBS archived response has invalid features")
        if role == "attributes":
            values = [row.get("attributes") for row in rows]
            if payload.get("exceededTransferLimit") or len(values) != counts[year]:
                raise ValueError("older MTBS archived attribute inventory is truncated")
            if any(not isinstance(row, dict) or row.get("year") != year for row in values):
                raise ValueError("older MTBS archived attributes have the wrong year or shape")
            ids = [row.get("fire_id") for row in values]
            if any(not isinstance(identity, str) or not identity for identity in ids) or len(set(ids)) != len(ids):
                raise ValueError("older MTBS archived attributes have duplicate or missing IDs")
            if year in attributes and canonical_bytes(values) != canonical_bytes(attributes[year]):
                raise ValueError("older MTBS archived source inventory changed during capture")
            attributes[year] = values
            continue
        offset = int(offset_text[0])
        if len(rows) != min(GEOMETRY_PAGE_ROWS, counts[year] - offset):
            raise ValueError("older MTBS archived geometry page is truncated")
        values = [row.get("properties") for row in rows]
        if any(not isinstance(row, dict) for row in values):
            raise ValueError("older MTBS archived geometry has invalid properties")
        if not attributes_match(values, attributes[year][offset : offset + len(rows)]):
            raise ValueError("older MTBS archived geometry differs from its pinned attributes")
        features.extend(rows)
    identities = [feature["properties"]["fire_id"] for feature in features]
    if len(features) != manifest["source_row_count"] or len(set(identities)) != len(identities):
        raise ValueError("older MTBS complete identity population differs")
    if source_fingerprint(features) != manifest["source_content_sha256"]:
        raise ValueError("older MTBS source content differs from its manifest digest")
    return manifest, features
