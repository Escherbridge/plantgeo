"""Prepare older MTBS candidates and a preservation packet locally; see AGENTS.md."""

from __future__ import annotations

import io
import json
import tempfile
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import polars as pl
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.duckdb_extensions import extension_directory_setting
from agri_data_service.ingest.mtbs import build_mapping_revision, build_mtbs_snapshot_record
from agri_data_service.pipeline.direct.burn_severity.capture import prepare_capture
from agri_data_service.pipeline.direct.burn_severity.current_snapshot import (
    MAX_ARTIFACT_BYTES,
    MAX_MANIFEST_BYTES,
    canonical_bytes,
    digest,
    put_local_blob,
)
from agri_data_service.pipeline.direct.burn_severity.older_capture import (
    OLDER_YEARS,
    read_local_blob,
    validate_older_capture,
)
from agri_data_service.pipeline.direct.burn_severity.rows import burn_severity_release_day_table
from agri_data_service.pipeline.parquet.objectstore import conform_to_stream_schema
from agri_data_service.warehouse.parquet.tiers import DERIVED_ZOOM_TIERS, derive_tier
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_SCHEMA

if TYPE_CHECKING:
    from collections.abc import Iterator

    import pyarrow as pa
    from duckdb import DuckDBPyConnection

    from agri_data_service.ingest.mtbs import MtbsBurnSeverityRecord

OLDER_RELEASE_PREFIX = "mtbs-older-recovery"
PREPARATION_SCHEMA = "mtbs-older-recovery-preparation/v1"


@contextmanager
def offline_geometry_connection() -> Iterator[DuckDBPyConnection]:
    """Require an installed spatial extension before replay; see AGENTS.md."""
    with duckdb.connect(
        config={"autoinstall_known_extensions": False, "autoload_known_extensions": False}
    ) as connection:
        setting = extension_directory_setting()
        if setting is not None:
            connection.execute(setting)
        try:
            connection.execute("LOAD spatial")
        except duckdb.Error as error:
            raise ValueError("offline MTBS preparation requires an installed DuckDB spatial extension") from error
        yield connection


def fire_id_digest(identities: list[str]) -> str:
    """Bind the exact sorted identity set with an unambiguous canonical JSON encoding."""
    if len(set(identities)) != len(identities):
        raise ValueError("older MTBS candidate has duplicate fire identities")
    return digest(canonical_bytes(sorted(identities)))


def _record(feature: dict[str, Any], *, identity: str, available_at: datetime) -> MtbsBurnSeverityRecord:
    properties = feature["properties"]
    year = properties.get("year")
    if type(year) is not int or year not in OLDER_YEARS:
        raise ValueError("older MTBS source row is outside 1984-2017")
    record = build_mtbs_snapshot_record(feature, year, manifest_sha256=identity, available_at=available_at)
    release = f"{OLDER_RELEASE_PREFIX}:{identity}"
    return record.model_copy(
        update={"release_identifier": release, "mapping_revision": build_mapping_revision(properties, release)}
    )


def _year_tables(
    records: list[MtbsBurnSeverityRecord], day: date, connection: DuckDBPyConnection
) -> list[tuple[int, pa.Table]]:
    if not records:
        return [(tier, BURN_SEVERITY_SCHEMA.arrow_schema.empty_table()) for tier in (13, *DERIVED_ZOOM_TIERS)]
    table = burn_severity_release_day_table(records, observed_day=day)
    frame = pl.from_arrow(table)
    if not isinstance(frame, pl.DataFrame):
        raise ValueError("older MTBS candidate did not produce a dataframe")
    tables = [(13, table)]
    tables.extend(
        (tier, derive_tier(frame, stream="burn-severity", tier=tier, connection=connection).to_arrow())
        for tier in DERIVED_ZOOM_TIERS
    )
    return tables


def prepare_older_capture(captured: Path, manifest_sha256: str, output: Path) -> dict[str, Any]:
    """Prepare one bounded part per year and rung without granting serving admission."""
    with offline_geometry_connection() as connection:
        return _prepare_older_capture(captured, manifest_sha256, output, connection)


def _prepare_older_capture(
    captured: Path, manifest_sha256: str, output: Path, connection: DuckDBPyConnection
) -> dict[str, Any]:
    if output.exists():
        raise ValueError("older MTBS preparation output must not exist")
    manifest, features = validate_older_capture(captured, manifest_sha256)
    day = date.fromisoformat(manifest["available_day"])
    available_at = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    records = [_record(feature, identity=manifest_sha256, available_at=available_at) for feature in features]
    identities = [record.producer_local_id for record in records]
    identity_digest = fire_id_digest(identities)
    output.mkdir(parents=True)
    blobs = output / "blobs"
    blobs.mkdir()
    source_body = canonical_bytes(manifest)
    source_ref = put_local_blob(blobs, source_body)
    consumed = len(source_body)
    rungs: dict[int, list[dict[str, Any]]] = {tier: [] for tier in (13, *DERIVED_ZOOM_TIERS)}
    for year in OLDER_YEARS:
        cohort = [record for record in records if record.ignition_year == year]
        if len(cohort) != manifest["counts_by_year"][str(year)]:
            raise ValueError("older MTBS normalized year count differs from capture")
        cohort_ids = sorted(record.producer_local_id for record in cohort)
        for tier, table in _year_tables(cohort, day, connection):
            rung = conform_to_stream_schema(table, BURN_SEVERITY_SCHEMA)
            if (
                rung.num_rows != len(cohort)
                or rung.schema != BURN_SEVERITY_SCHEMA.arrow_schema
                or rung["geom"].null_count
                or sorted(rung["fire_id"].to_pylist()) != cohort_ids
            ):
                raise ValueError("older MTBS derivation changed schema, geometry or fire identity")
            if set(rung["release_identifier"].to_pylist()) != (
                {f"{OLDER_RELEASE_PREFIX}:{manifest_sha256}"} if cohort else set()
            ):
                raise ValueError("older MTBS derivation lost its manifest binding")
            buffer = io.BytesIO()
            pq.write_table(rung, buffer, compression=BURN_SEVERITY_SCHEMA.compression, write_statistics=True)
            raw = buffer.getvalue()
            consumed += len(raw)
            if consumed > MAX_ARTIFACT_BYTES:
                raise ValueError("older MTBS prepared artifacts exceed their byte cap")
            rungs[tier].append(
                {
                    "fire_year": year,
                    "rows": rung.num_rows,
                    "fire_ids_sha256": fire_id_digest(cohort_ids),
                    **put_local_blob(blobs, raw),
                }
            )
    receipt = {
        "schema": PREPARATION_SCHEMA,
        "apply_authority": False,
        "publication_status": "not_admitted",
        "manifest": source_ref,
        "descriptor": {key: value for key, value in manifest.items() if key not in {"responses", "consistency"}},
        "fire_ids_sha256": identity_digest,
        "identity_digest_encoding": "sha256(canonical-json(sorted-fire-ids))",
        "rungs": [{"zoom": tier, "rows": len(records), "parts": parts} for tier, parts in rungs.items()],
        "artifact_bytes": consumed,
    }
    (output / "preparation.json").write_bytes(canonical_bytes(receipt))
    return receipt


def verify_current_preservation(captured: Path, prepared: Path, manifest_sha256: str) -> dict[str, Any]:
    """Reproduce current snapshot evidence and verify all saved rollback bytes locally."""
    with offline_geometry_connection() as connection:
        return _verify_current_preservation(captured, prepared, manifest_sha256, connection)


def _verify_current_preservation(
    captured: Path, prepared: Path, manifest_sha256: str, connection: DuckDBPyConnection
) -> dict[str, Any]:
    if prepared.is_symlink() or (prepared / "blobs").is_symlink() or (prepared / "preparation.json").is_symlink():
        raise ValueError("current MTBS preservation paths must not be symlinks")
    with tempfile.TemporaryDirectory(prefix="mtbs-current-preservation-") as temporary:
        replayed = prepare_capture(captured, manifest_sha256, Path(temporary) / "prepared", connection=connection)
    expected = canonical_bytes(replayed)
    read_local_blob(prepared / "preparation.json", digest(expected), len(expected), limit=MAX_MANIFEST_BYTES)
    value = json.loads(expected)
    descriptor = value["descriptor"]
    identity_set: set[str] | None = None
    for rung in value["rungs"]:
        raw = read_local_blob(
            prepared / "blobs" / rung["sha256"], rung["sha256"], rung["bytes"], limit=MAX_ARTIFACT_BYTES
        )
        table = pq.read_table(io.BytesIO(raw))
        current_ids = set(table["fire_id"].to_pylist())
        if len(current_ids) != descriptor["source_row_count"] or (
            identity_set is not None and identity_set != current_ids
        ):
            raise ValueError("current MTBS preserved rung identity sets differ")
        identity_set = current_ids
    return {
        "status": "local_source_and_artifacts_verified",
        "manifest_sha256": manifest_sha256,
        "preparation_sha256": digest(expected),
        "descriptor": descriptor,
        "rungs": value["rungs"],
        "fire_ids_sha256": fire_id_digest(sorted(identity_set or set())),
        "identity_digest_encoding": "sha256(canonical-json(sorted-fire-ids))",
        "source_evidence_directory": str(captured.resolve()),
        "prepared_evidence_directory": str(prepared.resolve()),
        "production_state_verified": False,
    }


def _verify_preparation(prepared: Path, candidate: dict[str, Any], captured: Path) -> None:  # noqa: PLR0912 - whole-ladder proof before producing a packet
    manifest, features = validate_older_capture(captured, candidate["manifest"]["sha256"])
    if candidate.get("descriptor") != {
        key: value for key, value in manifest.items() if key not in {"responses", "consistency"}
    }:
        raise ValueError("older MTBS candidate descriptor differs from source")
    source_body = canonical_bytes(manifest)
    if candidate["manifest"] != {"bytes": len(source_body), "sha256": digest(source_body)}:
        raise ValueError("older MTBS candidate source reference differs")
    read_local_blob(
        prepared / "blobs" / digest(source_body), digest(source_body), len(source_body), limit=MAX_MANIFEST_BYTES
    )
    expected_ids = [feature["properties"]["fire_id"] for feature in features]
    expected_by_year = {
        year: sorted(feature["properties"]["fire_id"] for feature in features if feature["properties"]["year"] == year)
        for year in OLDER_YEARS
    }
    expected_digest = fire_id_digest(expected_ids)
    if candidate.get("fire_ids_sha256") != expected_digest:
        raise ValueError("older MTBS candidate fire identities differ from source")
    rungs = candidate.get("rungs")
    if not isinstance(rungs, list) or len(rungs) != 1 + len(DERIVED_ZOOM_TIERS):
        raise ValueError("older MTBS candidate has an incomplete ladder")
    if [rung.get("zoom") for rung in rungs] != [13, *DERIVED_ZOOM_TIERS]:
        raise ValueError("older MTBS candidate has duplicate or changed rungs")
    consumed = len(source_body)
    for rung in rungs:
        parts = rung.get("parts")
        if not isinstance(parts, list) or [part.get("fire_year") for part in parts] != list(OLDER_YEARS):
            raise ValueError("older MTBS candidate lacks its exact year partition set")
        if rung.get("rows") != manifest["source_row_count"]:
            raise ValueError("older MTBS candidate rung count differs from source")
        observed_ids: list[str] = []
        for part in parts:
            year = part["fire_year"]
            expected_rows = manifest["counts_by_year"][str(year)]
            if part.get("rows") != expected_rows or type(part.get("bytes")) is not int:
                raise ValueError("older MTBS candidate part count differs from source")
            consumed += part["bytes"]
            if consumed > MAX_ARTIFACT_BYTES:
                raise ValueError("older MTBS candidate exceeds its aggregate byte cap")
            raw = read_local_blob(
                prepared / "blobs" / part["sha256"], part["sha256"], part["bytes"], limit=MAX_ARTIFACT_BYTES
            )
            table = pq.read_table(io.BytesIO(raw))
            if (
                table.schema != BURN_SEVERITY_SCHEMA.arrow_schema
                or table.num_rows != expected_rows
                or table["geom"].null_count
            ):
                raise ValueError("older MTBS candidate part violates schema/count/geometry contract")
            identities = table["fire_id"].to_pylist()
            if sorted(identities) != expected_by_year[year] or fire_id_digest(identities) != part.get(
                "fire_ids_sha256"
            ):
                raise ValueError("older MTBS candidate part identities differ")
            for column, expected in (
                ("fire_year", year),
                ("release_identifier", f"{OLDER_RELEASE_PREFIX}:{candidate['manifest']['sha256']}"),
                ("observed_day", date.fromisoformat(manifest["available_day"])),
                (
                    "data_available_at",
                    datetime.combine(date.fromisoformat(manifest["available_day"]), datetime.min.time(), tzinfo=UTC),
                ),
            ):
                if set(table[column].to_pylist()) != ({expected} if expected_rows else set()):
                    raise ValueError("older MTBS candidate part lost its year/release/day binding")
            observed_ids.extend(identities)
        if fire_id_digest(observed_ids) != expected_digest:
            raise ValueError("older MTBS candidate rung identity population differs from source")
    if candidate.get("artifact_bytes") != consumed:
        raise ValueError("older MTBS candidate artifact byte total differs")


def write_recovery_packet(prepared: Path, current_preservation: dict[str, Any], *, captured: Path) -> dict[str, Any]:
    """Bind concrete candidate bytes and rollback invariants, keeping production gates open."""
    path = prepared / "preparation.json"
    if (
        prepared.is_symlink()
        or (prepared / "blobs").is_symlink()
        or path.is_symlink()
        or path.stat().st_size > MAX_MANIFEST_BYTES
    ):
        raise ValueError("older MTBS preparation packet path is invalid")
    raw = path.read_bytes()
    candidate = json.loads(raw)
    if candidate.get("schema") != PREPARATION_SCHEMA or candidate.get("apply_authority") is not False:
        raise ValueError("older MTBS recovery packet requires an unadmitted preparation")
    _verify_preparation(prepared, candidate, captured)
    packet = {
        "schema": "mtbs-older-recovery-packet/v1",
        "status": "candidate_prepared_admission_blocked",
        "apply_authority": False,
        "prepared_at": datetime.now(UTC).isoformat(),
        "candidate_preparation_sha256": digest(raw),
        "candidate_manifest": candidate["manifest"],
        "candidate_descriptor": candidate["descriptor"],
        "candidate_fire_ids_sha256": candidate["fire_ids_sha256"],
        "candidate_rungs": candidate["rungs"],
        "candidate_verification": (
            "source graph replay and local hash/schema/count/year/identity/date checks; "
            "independent value/geometry replay still required"
        ),
        "source_evidence_directory": str(captured.resolve()),
        "current_snapshot_preservation": current_preservation,
        "rollback": {
            "scope": "only the future older recovery admission and its owned objects",
            "current_snapshot": "retain the exact verified 2018-2026 source and four rungs",
            "historical_releases": "retain all existing release partitions and historical refusal/truncation semantics",
            "writer_control": (
                "disable only the affected executor duty; never restore a database writer or Railway cron"
            ),
            "destructive_action_authorized": False,
        },
        "blocking_requirements": [
            "A reviewed distinct older-recovery descriptor and catalogue admission contract; "
            "current v1 rejects this scope.",
            "Fresh exact deployed revision, active definitions, leases and lane-day ownership evidence.",
            "Archive current physical objects, completion/absence receipts, availability head "
            "and generation before mutation.",
            "Resolve any availability-day collision with current capture; never overwrite either population.",
            "Use ordinary publication barrier, day locks, all-rung finalization and exact rollback pins.",
            "Obtain exact operator authorization for staging/publication and any future rollback action.",
            "Verify selected-day, each rung, spatial/temporal neighbours, empty replacements "
            "and historical truncation.",
        ],
    }
    with (prepared / "recovery-packet.json").open("xb") as handle:
        handle.write(canonical_bytes(packet))
    return packet
