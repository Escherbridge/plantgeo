"""Prepare immutable current MTBS captures locally; see direct/AGENTS.md."""

from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import polars as pl
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from agri_data_service.ingest.mtbs import MTBS_FEATURE_SERVICE_QUERY_URL, build_mtbs_snapshot_record
from agri_data_service.pipeline.direct.burn_severity.rows import burn_severity_release_day_table
from agri_data_service.pipeline.parquet.objectstore import conform_to_stream_schema
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS, derive_tier
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    import pyarrow as pa

MAX_CAPTURE_ROWS = 2000
MAX_ARTIFACT_BYTES = 600 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_SOURCE_RESPONSES = 400
SNAPSHOT_SCHEMA = "mtbs-current-snapshot/v1"
FIRST_PARTIAL_YEAR = 2023


def canonical_bytes(value: object) -> bytes:
    """Serialize an immutable receipt deterministically."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def put_local_blob(root: Path, value: bytes) -> dict[str, object]:
    """Store exact evidence bytes exclusively under their content identity."""
    identity = digest(value)
    path = root / identity
    try:
        with path.open("xb") as handle:
            handle.write(value)
    except FileExistsError:
        if path.is_symlink() or path.read_bytes() != value:
            raise ValueError("local evidence identity conflict") from None
    return {"sha256": identity, "bytes": len(value)}


def make_source_manifest(  # noqa: PLR0913 - immutable source identity binds six independent scope/evidence inputs
    *,
    bbox: tuple[float, float, float, float],
    years: tuple[int, ...],
    captured_from: datetime,
    captured_through: datetime,
    counts: Mapping[int, int],
    responses: Sequence[Mapping[str, object]],
    source_content_sha256: str,
) -> dict[str, Any]:
    """Bind a complete bounded query, without claiming completed upstream fire seasons."""
    if captured_from.utcoffset() != timedelta(0) or captured_through.utcoffset() != timedelta(0):
        raise ValueError("capture timestamps must be UTC-aware")
    if captured_through < captured_from or captured_through - captured_from > timedelta(seconds=600):
        raise ValueError("capture interval exceeds the bounded contract")
    if years != tuple(range(2018, 2027)):
        raise ValueError("snapshot requires the explicit contiguous 2018-onward horizon")
    if set(counts) != set(years) or any(type(n) is not int or n < 0 for n in counts.values()):
        raise ValueError("snapshot counts do not cover the exact horizon")
    if sum(counts.values()) > MAX_CAPTURE_ROWS:
        raise ValueError("snapshot exceeds the row cap")
    if len(responses) > MAX_SOURCE_RESPONSES:
        raise ValueError("snapshot exceeds the source response cap")
    if len(source_content_sha256) != hashlib.sha256().digest_size * 2 or any(
        c not in "0123456789abcdef" for c in source_content_sha256
    ):
        raise ValueError("snapshot source content identity is invalid")
    if bbox != (-125.0, 42.0, -111.0, 49.0):
        raise ValueError("snapshot footprint differs from the reviewed deployment footprint")
    return {
        "schema": SNAPSHOT_SCHEMA,
        "mode": "full_replacement",
        "source_url": MTBS_FEATURE_SERVICE_QUERY_URL,
        "bbox": [float(value) for value in bbox],
        "crs": "EPSG:4326",
        "covered_years": {"from": years[0], "to": years[-1]},
        "captured_from": captured_from.isoformat(),
        "captured_through": captured_through.isoformat(),
        "available_day": (captured_through.date() + timedelta(days=1)).isoformat(),
        "capture_complete": True,
        "partial_fire_years": [year for year in years if year >= FIRST_PARTIAL_YEAR],
        "source_row_count": sum(counts.values()),
        "counts_by_year": {str(year): counts[year] for year in years},
        "responses": list(responses),
        "source_content_sha256": source_content_sha256,
        "consistency": "bounded capture interval; matching count and attribute inventories; no upstream version token",
    }


def validate_source_manifest(manifest: object) -> dict[str, Any]:
    """Validate allocation-driving scope and counts before replaying untrusted evidence."""
    if not isinstance(manifest, dict) or len(canonical_bytes(manifest)) > MAX_MANIFEST_BYTES:
        raise ValueError("snapshot manifest is invalid or excessive")
    years = tuple(range(2018, 2027))
    counts = manifest.get("counts_by_year")
    if not isinstance(counts, dict) or set(counts) != {str(year) for year in years}:
        raise ValueError("snapshot counts do not cover the exact horizon")
    if any(type(n) is not int or not 0 <= n <= MAX_CAPTURE_ROWS for n in counts.values()):
        raise ValueError("snapshot has invalid or excessive counts")
    responses = manifest.get("responses")
    if not isinstance(responses, list) or len(responses) > MAX_SOURCE_RESPONSES:
        raise ValueError("snapshot response graph is invalid or excessive")
    if manifest.get("covered_years") != {"from": 2018, "to": 2026}:
        raise ValueError("snapshot requires the exact covered year interval")
    if manifest.get("bbox") != [-125, 42, -111, 49]:
        raise ValueError("snapshot footprint differs from the reviewed deployment footprint")
    expected = make_source_manifest(
        bbox=(-125, 42, -111, 49),
        years=years,
        captured_from=datetime.fromisoformat(manifest["captured_from"]),
        captured_through=datetime.fromisoformat(manifest["captured_through"]),
        counts={int(year): count for year, count in counts.items()},
        responses=responses,
        source_content_sha256=manifest["source_content_sha256"],
    )
    if canonical_bytes(expected) != canonical_bytes(manifest):
        raise ValueError("snapshot manifest differs from its validated contract")
    return expected


def prepare_snapshot(  # noqa: PLR0912, PLR0915 - validate the complete source contract before producing bounded artifacts
    manifest: Mapping[str, Any], features: Sequence[Mapping[str, object]], *, output: Path
) -> dict[str, object]:
    """Create a no-apply candidate with normal four-rung geometry derivation."""
    if output.exists():
        raise ValueError("preparation output must not exist")
    manifest = validate_source_manifest(dict(manifest))
    first, last = manifest["covered_years"]["from"], manifest["covered_years"]["to"]
    if len(features) != manifest["source_row_count"] or len(features) > MAX_CAPTURE_ROWS:
        raise ValueError("candidate count differs from immutable source manifest")
    fingerprint = hashlib.sha256()
    for feature in features:
        fingerprint.update(canonical_bytes(feature) + b"\n")
    if fingerprint.hexdigest() != manifest["source_content_sha256"]:
        raise ValueError("candidate content differs from immutable source fingerprint")
    manifest_body = canonical_bytes(manifest)
    identity = digest(manifest_body)
    day = date.fromisoformat(manifest["available_day"])
    available_at = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    records = []
    actual_counts = dict.fromkeys(range(first, last + 1), 0)
    for feature in features:
        properties = feature.get("properties")
        if not isinstance(properties, dict) or type(properties.get("year")) is not int:
            raise ValueError("source row has no integer ignition year")
        year = properties["year"]
        if year not in actual_counts:
            raise ValueError("source row is outside snapshot year coverage")
        actual_counts[year] += 1
        records.append(build_mtbs_snapshot_record(feature, year, manifest_sha256=identity, available_at=available_at))
    if {str(y): n for y, n in actual_counts.items()} != manifest["counts_by_year"]:
        raise ValueError("candidate year counts differ from source inventory")
    ids = [record.producer_local_id for record in records]
    if len(set(ids)) != len(ids):
        raise ValueError("candidate contains duplicate fire identities")
    table = burn_severity_release_day_table(records, observed_day=day)
    frame = pl.from_arrow(table)
    if not isinstance(frame, pl.DataFrame):
        raise ValueError("candidate did not produce a dataframe")
    tables: list[tuple[int, pa.Table]] = [(13, table)]
    tables.extend(
        (tier, derive_tier(frame, stream="burn-severity", tier=tier).to_arrow()) for tier in DERIVED_ZOOM_TIERS
    )
    output.mkdir(parents=True)
    blobs = output / "blobs"
    blobs.mkdir()
    manifest_ref = put_local_blob(blobs, manifest_body)
    consumed = len(manifest_body)
    rungs = []
    for tier, derived_table in tables:
        rung = conform_to_stream_schema(derived_table, BURN_SEVERITY_SCHEMA)
        if rung.num_rows != len(records):
            raise ValueError("snapshot derivation changed fire count")
        if rung.schema != BURN_SEVERITY_SCHEMA.arrow_schema or rung["geom"].null_count:
            raise ValueError("snapshot derivation changed schema or lost geometry")
        if set(rung["release_identifier"].to_pylist()) != ({f"mtbs-current-snapshot:{identity}"} if records else set()):
            raise ValueError("snapshot rung lost its source manifest binding")
        buffer = io.BytesIO()
        pq.write_table(rung, buffer, compression=BURN_SEVERITY_SCHEMA.compression, write_statistics=True)
        raw = buffer.getvalue()
        consumed += len(raw)
        if consumed > MAX_ARTIFACT_BYTES:
            raise ValueError("prepared artifacts exceed byte cap")
        rungs.append({"zoom": tier, "rows": rung.num_rows, **put_local_blob(blobs, raw)})
    descriptor = {
        key: value
        for key, value in manifest.items()
        if key not in {"responses", "counts_by_year", "consistency", "source_content_sha256"}
    }
    descriptor["manifest_sha256"] = identity
    receipt = {
        "schema": "mtbs-current-snapshot-preparation/v1",
        "apply_authority": False,
        "manifest": manifest_ref,
        "descriptor": descriptor,
        "rungs": rungs,
        "artifact_bytes": consumed,
    }
    (output / "preparation.json").write_bytes(canonical_bytes(receipt))
    return receipt
