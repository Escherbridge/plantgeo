"""Bounded public MTBS capture into local evidence, with no publication path."""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import httpx

from agri_data_service.ingest.mtbs import MTBS_FEATURE_SERVICE_QUERY_URL
from agri_data_service.pipeline.direct.burn_severity.current_snapshot import (
    MAX_CAPTURE_ROWS,
    canonical_bytes,
    digest,
    make_source_manifest,
    prepare_snapshot,
    put_local_blob,
    validate_source_manifest,
)

MAX_REQUESTS = 400
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
MAX_RAW_BYTES = 400 * 1024 * 1024
MAX_SECONDS = 600
SHA256_LENGTH = 64
MAX_HEADER_LENGTH = 256
COORDINATE_SERIALIZATION_TOLERANCE = 1e-12
YEARS = tuple(range(2018, 2027))
BBOX = (-125.0, 42.0, -111.0, 49.0)

if TYPE_CHECKING:
    from pathlib import Path


def attributes_match(actual: list[dict[str, Any]], expected: list[dict[str, Any]]) -> bool:
    """Permit only documented EDW latitude/longitude JSON serialization tails."""
    if len(actual) != len(expected):
        return False
    for observed, pinned in zip(actual, expected, strict=True):
        if observed.keys() != pinned.keys():
            return False
        for key, value in observed.items():
            other = pinned[key]
            if canonical_bytes(value) == canonical_bytes(other):
                continue
            if (
                type(value) in {int, float}
                and type(other) in {int, float}
                and math.isfinite(value)
                and math.isfinite(other)
                and value == other
            ):
                continue
            if (
                key not in {"latitude", "longitude"}
                or type(value) not in {int, float}
                or type(other) not in {int, float}
            ):
                return False
            if (
                not math.isfinite(value)
                or not math.isfinite(other)
                or abs(value - other) > COORDINATE_SERIALIZATION_TOLERANCE
            ):
                return False
    return True


class CaptureBudget:
    """Charge every received byte and request, including refused responses."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self.requests = 0
        self.raw_bytes = 0

    def remaining(self) -> float:
        remaining = MAX_SECONDS - (time.monotonic() - self.started)
        if remaining <= 0:
            raise ValueError("MTBS capture exceeded its wall-time cap")
        return remaining

    def request(self) -> None:
        self.remaining()
        self.requests += 1
        if self.requests > MAX_REQUESTS:
            raise ValueError("MTBS capture exceeded its request cap")

    def charge(self, amount: int) -> None:
        self.remaining()
        self.raw_bytes += amount
        if self.raw_bytes > MAX_RAW_BYTES:
            raise ValueError("MTBS capture exceeded its raw-byte cap")


def _query(  # noqa: PLR0913 - transport, budget, archive, receipt sink, query and role are independent inputs
    client: httpx.Client,
    budget: CaptureBudget,
    blobs: Path,
    receipts: list[dict[str, object]],
    parameters: dict[str, str],
    role: str,
) -> dict[str, Any]:
    budget.request()
    body = bytearray()
    started = datetime.now(UTC).isoformat()
    journal = blobs.parent / "capture-journal.json"
    journal.write_bytes(
        canonical_bytes(
            {
                "status": "pending",
                "receipts": receipts,
                "current_request": {"role": role, "parameters": parameters, "started_at": started},
            }
        )
    )
    with client.stream(
        "GET", MTBS_FEATURE_SERVICE_QUERY_URL, params=parameters, timeout=min(30.0, budget.remaining())
    ) as response:
        response.raise_for_status()
        if response.status_code != HTTPStatus.OK:
            raise ValueError("MTBS response status is not a complete HTTP 200 entity")
        if any(len(response.headers.get(key, "")) > MAX_HEADER_LENGTH for key in ("content-type", "content-encoding")):
            raise ValueError("MTBS response headers exceed provenance bounds")
        for chunk in response.iter_bytes(chunk_size=65536):
            budget.charge(len(chunk))
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("MTBS response exceeded its byte cap")
    raw = bytes(body)
    reference = put_local_blob(blobs, raw)
    receipts.append(
        {
            "role": role,
            "parameters": parameters,
            "status": response.status_code,
            "content_encoding": response.headers.get("content-encoding", "identity"),
            "content_type": response.headers.get("content-type", ""),
            "body_semantics": "decoded-http-entity",
            "started_at": started,
            "received_at": datetime.now(UTC).isoformat(),
            **reference,
        }
    )
    journal.write_bytes(canonical_bytes({"status": "pending", "receipts": receipts}))
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("error"):
        raise ValueError("MTBS returned an invalid response or service error")
    return payload


def _parameters(year: int) -> dict[str, str]:
    return {
        "where": f"year = {year}",
        "geometry": ",".join(str(n) for n in BBOX),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "f": "json",
    }


def _inventory(
    client: httpx.Client,
    budget: CaptureBudget,
    blobs: Path,
    receipts: list[dict[str, object]],
    year: int,
) -> tuple[int, list[dict[str, Any]]]:
    parameters = _parameters(year)
    count_payload = _query(client, budget, blobs, receipts, {**parameters, "returnCountOnly": "true"}, f"count:{year}")
    count = count_payload.get("count")
    if type(count) is not int or not 0 <= count <= MAX_CAPTURE_ROWS:
        raise ValueError("MTBS inventory count is invalid or excessive")
    payload = _query(
        client,
        budget,
        blobs,
        receipts,
        {
            **parameters,
            "returnGeometry": "false",
            "outFields": "*",
            "orderByFields": "fire_id",
            "resultRecordCount": str(MAX_CAPTURE_ROWS),
        },
        f"attributes:{year}",
    )
    features = payload.get("features")
    if payload.get("exceededTransferLimit") or not isinstance(features, list) or len(features) != count:
        raise ValueError("MTBS attribute inventory is truncated or inconsistent")
    attributes = []
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("attributes"), dict):
            raise ValueError("MTBS attribute inventory has an invalid row")
        attributes.append(feature["attributes"])
    ids = [row.get("fire_id") for row in attributes]
    if any(not isinstance(identity, str) or not identity for identity in ids) or len(set(ids)) != len(ids):
        raise ValueError("MTBS attribute inventory has duplicate or missing fire IDs")
    return count, attributes


def capture_snapshot(output: Path) -> dict[str, object]:
    """Capture the reviewed 2018-2026 footprint; default preparation never calls this function."""
    if output.exists():
        raise ValueError("capture output must not exist")
    output.mkdir(parents=True)
    try:
        result = _capture_snapshot(output)
    except BaseException as error:
        journal = output / "capture-journal.json"
        content = json.loads(journal.read_bytes()) if journal.exists() else {"receipts": []}
        content.update(status="failed", failure_type=type(error).__name__)
        journal.write_bytes(canonical_bytes(content))
        raise
    return result


def _capture_snapshot(output: Path) -> dict[str, object]:  # noqa: PLR0912 - sequential inventory/page/recheck refusal gates
    if datetime.now(UTC).year > YEARS[-1]:
        raise ValueError("MTBS current-snapshot horizon requires review for the new calendar year")
    blobs = output / "blobs"
    blobs.mkdir()
    budget = CaptureBudget()
    started = datetime.now(UTC)
    receipts: list[dict[str, object]] = []
    counts: dict[int, int] = {}
    inventories = {}
    source_content = hashlib.sha256()
    with httpx.Client(follow_redirects=False, trust_env=False) as client:
        for year in YEARS:
            counts[year], inventories[year] = _inventory(client, budget, blobs, receipts, year)
        if sum(counts.values()) > MAX_CAPTURE_ROWS:
            raise ValueError("MTBS snapshot exceeds its total row cap")
        for year in YEARS:
            captured_ids: list[str] = []
            for offset in range(0, counts[year], 25):
                payload = _query(
                    client,
                    budget,
                    blobs,
                    receipts,
                    {
                        **_parameters(year),
                        "f": "geojson",
                        "outSR": "4326",
                        "outFields": "*",
                        "returnGeometry": "true",
                        "orderByFields": "fire_id",
                        "resultOffset": str(offset),
                        "resultRecordCount": "25",
                    },
                    f"geometry:{year}:{offset}",
                )
                features = payload.get("features")
                if not isinstance(features, list) or len(features) != min(25, counts[year] - offset):
                    raise ValueError("MTBS geometry page is incomplete")
                for feature in features:
                    if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
                        raise ValueError("MTBS geometry page contains an invalid feature")
                    captured_ids.append(feature["properties"].get("fire_id"))
                properties = [feature["properties"] for feature in features]
                for feature in features:
                    source_content.update(canonical_bytes(feature) + b"\n")
                if not attributes_match(properties, inventories[year][offset : offset + len(features)]):
                    raise ValueError("MTBS geometry properties differ from pinned attribute inventory")
            if captured_ids != [row["fire_id"] for row in inventories[year]]:
                raise ValueError("MTBS geometry IDs differ from its pinned attribute inventory")
        for year in YEARS:
            final_count, final_attributes = _inventory(client, budget, blobs, receipts, year)
            if final_count != counts[year] or canonical_bytes(final_attributes) != canonical_bytes(inventories[year]):
                raise ValueError("MTBS changed its source inventory during capture")
    budget.remaining()
    manifest = make_source_manifest(
        bbox=BBOX,
        years=YEARS,
        captured_from=started,
        captured_through=datetime.now(UTC),
        counts=counts,
        responses=receipts,
        source_content_sha256=source_content.hexdigest(),
    )
    manifest = validate_source_manifest(manifest)
    body = canonical_bytes(manifest)
    put_local_blob(blobs, body)
    (output / "manifest.json").write_bytes(body)
    (output / "capture-journal.json").write_bytes(
        canonical_bytes({"status": "complete", "receipts": receipts, "manifest_sha256": digest(body)})
    )
    return {
        "manifest_sha256": digest(body),
        "rows": sum(counts.values()),
        "requests": budget.requests,
        "raw_bytes": budget.raw_bytes,
        "apply_authority": False,
    }


def validate_capture(capture: Path, expected_sha256: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:  # noqa: PLR0912, PLR0915 - ordered evidence graph checks
    """Validate the complete archived query graph without deriving any Parquet artifacts."""
    path = capture / "manifest.json"
    if capture.is_symlink() or (capture / "blobs").is_symlink() or path.is_symlink():
        raise ValueError("invalid capture manifest file")
    with path.open("rb") as handle:
        raw = handle.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("capture manifest exceeds byte cap")
    if digest(raw) != expected_sha256:
        raise ValueError("capture manifest does not match the reviewed SHA-256")
    manifest = validate_source_manifest(json.loads(raw))
    counts = {int(year): count for year, count in manifest["counts_by_year"].items()}
    years = tuple(sorted(counts))
    inventory_roles = [role for year in years for role in (f"count:{year}", f"attributes:{year}")]
    geometry_roles = [f"geometry:{year}:{offset}" for year in years for offset in range(0, counts[year], 25)]
    expected_roles = [*inventory_roles, *geometry_roles, *inventory_roles]
    if len(expected_roles) > MAX_REQUESTS or [r["role"] for r in manifest["responses"]] != expected_roles:
        raise ValueError("capture response graph is incomplete or out of order")
    features = []
    consumed = 0
    inventories: dict[int, list[dict[str, Any]]] = {}
    page_ids: dict[int, list[str]] = {year: [] for year in years}
    for receipt in manifest["responses"]:
        identity = receipt["sha256"]
        if (
            not isinstance(identity, str)
            or len(identity) != SHA256_LENGTH
            or any(c not in "0123456789abcdef" for c in identity)
        ):
            raise ValueError("invalid source blob identity")
        blob = capture / "blobs" / identity
        if blob.is_symlink() or type(receipt["bytes"]) is not int or not 0 <= receipt["bytes"] <= MAX_RESPONSE_BYTES:
            raise ValueError("source blob has changed shape")
        consumed += receipt["bytes"]
        if consumed > MAX_RAW_BYTES:
            raise ValueError("source evidence exceeds capture budget")
        with blob.open("rb") as handle:
            value = handle.read(receipt["bytes"] + 1)
        if len(value) != receipt["bytes"] or digest(value) != identity:
            raise ValueError("source blob failed its immutable identity")
        payload = json.loads(value)
        if receipt.get("status") != HTTPStatus.OK or receipt.get("body_semantics") != "decoded-http-entity":
            raise ValueError("source response lacks its decoded-entity/status provenance")
        for key in ("content_encoding", "content_type", "started_at", "received_at"):
            if not isinstance(receipt.get(key), str) or len(receipt[key]) > MAX_HEADER_LENGTH:
                raise ValueError("source response metadata is invalid or excessive")
        sent = datetime.fromisoformat(receipt["started_at"])
        received = datetime.fromisoformat(receipt["received_at"])
        if sent.utcoffset() != timedelta(0) or received.utcoffset() != timedelta(0):
            raise ValueError("source response timestamps must be UTC-aware")
        if (
            not datetime.fromisoformat(manifest["captured_from"])
            <= sent
            <= received
            <= datetime.fromisoformat(manifest["captured_through"])
        ):
            raise ValueError("source response timestamps lie outside capture interval")
        role, year_text, *offset_text = receipt["role"].split(":")
        year = int(year_text)
        parameters = receipt["parameters"]
        if any(parameters.get(key) != val for key, val in _parameters(year).items() if key != "f"):
            raise ValueError("source response query differs from the reviewed footprint/year")
        if role == "count":
            expected_parameters = {**_parameters(year), "returnCountOnly": "true"}
        elif role == "attributes":
            expected_parameters = {
                **_parameters(year),
                "returnGeometry": "false",
                "outFields": "*",
                "orderByFields": "fire_id",
                "resultRecordCount": str(MAX_CAPTURE_ROWS),
            }
        else:
            expected_parameters = {
                **_parameters(year),
                "f": "geojson",
                "outSR": "4326",
                "outFields": "*",
                "returnGeometry": "true",
                "orderByFields": "fire_id",
                "resultOffset": offset_text[0],
                "resultRecordCount": "25",
            }
        if parameters != expected_parameters:
            raise ValueError("source response query differs from its declared evidence role")
        if not isinstance(payload, dict) or payload.get("error"):
            raise ValueError("archived source response is an error")
        if role == "count":
            if payload.get("count") != counts[year]:
                raise ValueError("archived source count differs from manifest")
        elif role == "attributes":
            rows = [row["attributes"] for row in payload["features"]]
            if payload.get("exceededTransferLimit") or len(rows) != counts[year]:
                raise ValueError("archived source attributes are truncated")
            if year in inventories and canonical_bytes(rows) != canonical_bytes(inventories[year]):
                raise ValueError("archived source attributes changed during capture")
            inventories[year] = rows
        else:
            offset = int(offset_text[0])
            rows = payload["features"]
            if len(rows) != min(25, counts[year] - offset):
                raise ValueError("archived source geometry page is truncated")
            expected_ids = [row["fire_id"] for row in inventories[year][offset : offset + len(rows)]]
            actual_ids = [row["properties"]["fire_id"] for row in rows]
            if actual_ids != expected_ids:
                raise ValueError("archived geometry differs from source ID inventory")
            if not attributes_match(
                [row["properties"] for row in rows], inventories[year][offset : offset + len(rows)]
            ):
                raise ValueError("archived geometry properties differ from pinned source inventory")
            page_ids[year].extend(actual_ids)
            features.extend(rows)
            if len(features) > MAX_CAPTURE_ROWS:
                raise ValueError("source evidence exceeds row cap")
    if any(len(set(ids)) != counts[year] for year, ids in page_ids.items()):
        raise ValueError("archived geometry identity coverage is incomplete")
    content = hashlib.sha256()
    for feature in features:
        content.update(canonical_bytes(feature) + b"\n")
    if content.hexdigest() != manifest["source_content_sha256"]:
        raise ValueError("captured source content differs from its manifest digest")
    return manifest, features


def prepare_capture(capture: Path, expected_sha256: str, output: Path) -> dict[str, object]:
    """Prepare only from hash-verified archived responses, never from caller-supplied feature rows."""
    manifest, features = validate_capture(capture, expected_sha256)
    return prepare_snapshot(manifest, features, output=output)
