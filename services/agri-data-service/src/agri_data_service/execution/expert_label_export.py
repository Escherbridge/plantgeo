"""Export one reviewed expert-label release to Parquet for the ML service; see execution/AGENTS.md."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

import click
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from agri_data_service.db.engine import ingest_session
from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.jobs.lease import fetch_rows
from agri_data_service.pipeline.parquet.objectstore import ML_PREFIX, ObjectStore, validate_ml_relative_path
from agri_data_service.warehouse.schemas.expert_labels import (
    EXPERT_LABEL_COLUMNS,
    EXPERT_LABEL_EXPORT_SCHEMA,
    EXPERT_LABEL_PART_NAME,
    EXPERT_LABEL_PREFIX,
    EXPERT_LABEL_SORT_COLUMNS,
    MAX_EXPERT_LABELS,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

_SELECT_LABELS: Final = text(load_query_sql("execution/select_expert_labels_for_release.sql"))

PARQUET_CONTENT_TYPE: Final = "application/vnd.apache.parquet"
RECEIPT_CONTENT_TYPE: Final = "application/json"
RECEIPT_NAME: Final = "receipt.json"

#: The codec every other stream in this warehouse uses. Stated here rather than inherited because
#: this object is not a lane partition and never passes through `write_partition`.
PARQUET_COMPRESSION: Final = "zstd"

#: A release identifier becomes one object-key segment, so it may hold no separator.
_FORBIDDEN_RELEASE_CHARACTERS: Final = frozenset({"/", "\\"})

#: Columns whose database value is a `jsonb` object and whose exported value is canonical JSON TEXT.
_CANONICAL_JSON_COLUMNS: Final[tuple[str, ...]] = ("condition_envelope",)


class ExpertLabelExportRefusal(click.ClickException):
    """Refuse an export rather than write a file a reader would mis-type or an operator mistrust."""


@dataclass(frozen=True, slots=True)
class ExpertLabelExport:
    """One export's outcome: where it went, what it holds, and the digest of the exact bytes."""

    release: str
    part_path: str
    receipt_path: str
    label_count: int
    sha256: str
    byte_count: int
    written: bool


def validate_release_identifier(release: str) -> str:
    """Return `release` if it is a safe single object-key segment, else refuse.

    The same rule the ML service's reader applies. A release identifier reaches this function from
    an operator's command line and becomes a path segment, so `..` or a separator would place the
    export somewhere no reader looks and somewhere no listing expects.
    """
    if not release or release != release.strip() or release in {".", ".."}:
        raise ExpertLabelExportRefusal(f"release identifier {release!r} is not a usable object-key segment")
    if _FORBIDDEN_RELEASE_CHARACTERS & set(release):
        raise ExpertLabelExportRefusal(f"release identifier {release!r} may not contain a path separator")
    return release


def _export_key(release: str, *, prefix: str, name: str) -> str:
    """Return one export object's key, refusing a `--prefix` that would place it outside `ml/`.

    The store applies this same rule and raises `ValueError`, but only at WRITE time and in the
    vocabulary of object keys. An operator who mistyped `--prefix` would see a bare `ValueError`
    from two layers down, after the release had already been read and encoded, and a dry run --
    which never writes -- would not see it at all. Checking here makes the refusal typed, makes it
    name the flag the operator actually passed, and makes the dry run refuse exactly what the
    apply run would.
    """
    key = f"{prefix}/{validate_release_identifier(release)}/{name}"
    try:
        return validate_ml_relative_path(key)
    except ValueError as error:
        raise ExpertLabelExportRefusal(
            f"--prefix {prefix!r} does not name a location under {ML_PREFIX!r}: {error}. Exports live "
            f"beside the ML service's own objects; a key outside that prefix is one no reader lists"
        ) from error


def expert_label_part_path(release: str, *, prefix: str = EXPERT_LABEL_PREFIX) -> str:
    """Return the relative object key of one release's single part file."""
    return _export_key(release, prefix=prefix, name=EXPERT_LABEL_PART_NAME)


def expert_label_receipt_path(release: str, *, prefix: str = EXPERT_LABEL_PREFIX) -> str:
    """Return the relative object key of one release's export receipt."""
    return _export_key(release, prefix=prefix, name=RECEIPT_NAME)


def _canonical_json(value: object) -> str:
    """Render one envelope as canonical JSON: sorted keys, no incidental whitespace.

    Canonical rather than whatever the driver handed back, because the exported bytes must be a
    function of the CONTENT alone. Two runs that read the same envelope through different driver
    versions would otherwise produce different text and the idempotence check below would report a
    conflict that is really a formatting difference.
    """
    if isinstance(value, str):
        # Already text: re-encode through the parser so the canonical form is reached either way.
        value = json.loads(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _exported_row(row: Mapping[str, Any]) -> dict[str, object]:
    """Narrow one database row to the exported columns, in schema order, canonicalising the envelope."""
    missing = tuple(column for column in EXPERT_LABEL_COLUMNS if column not in row)
    if missing:
        raise ExpertLabelExportRefusal(f"the label query returned no value for column(s) {missing}")
    exported: dict[str, object] = {}
    for column in EXPERT_LABEL_COLUMNS:
        value = row[column]
        exported[column] = _canonical_json(value) if column in _CANONICAL_JSON_COLUMNS else value
    return exported


def build_expert_label_table(rows: Sequence[Mapping[str, Any]], *, release: str) -> pa.Table:
    """Assemble the pinned Arrow table for one release, or refuse by naming what is wrong.

    The cast to the pinned schema is the contract check, not a convenience: the ML service compares
    the file's schema against its own copy and refuses a mismatch outright, so a column that arrived
    as the wrong type must fail HERE, where the operator can read why, rather than at read time in
    another service.
    """
    if not rows:
        raise ExpertLabelExportRefusal(
            f"release {release!r} holds no labels; an empty export would publish a release that does not "
            "exist and a reader could not tell it from one that was never exported"
        )
    if len(rows) > MAX_EXPERT_LABELS:
        raise ExpertLabelExportRefusal(
            f"release {release!r} holds more than {MAX_EXPERT_LABELS} labels; the ML service's reader "
            "refuses that file, so writing it would publish an unreadable object"
        )
    exported = [_exported_row(row) for row in rows]
    keys = [str(entry["label_key"]) for entry in exported]
    if len(set(keys)) != len(keys):
        raise ExpertLabelExportRefusal(f"release {release!r} returned duplicate label keys; the sort is not total")
    exported.sort(key=lambda entry: str(entry["label_key"]))
    return pa.Table.from_pylist(exported, schema=EXPERT_LABEL_EXPORT_SCHEMA)


def encode_expert_label_table(table: pa.Table) -> bytes:
    """Serialise the pinned table to Parquet bytes, with no file-level metadata to drift."""
    buffer = io.BytesIO()
    # `store_schema=False` would drop the schema the reader compares against; what IS dropped is
    # pandas metadata, which carries a library version and would make two identical exports differ.
    pq.write_table(table.replace_schema_metadata(None), buffer, compression=PARQUET_COMPRESSION)
    return buffer.getvalue()


def sha256_of(payload: bytes) -> str:
    """Return the hex digest of exactly these bytes."""
    return hashlib.sha256(payload).hexdigest()


def _receipt_payload(export: ExpertLabelExport, *, exported_at: datetime) -> bytes:
    """Render the receipt beside the part file: what was written, from what, and of what size."""
    return json.dumps(
        {
            "event": "plantgeo_expert_label_export",
            "release": export.release,
            "part_path": export.part_path,
            "label_count": export.label_count,
            "sha256": export.sha256,
            "byte_count": export.byte_count,
            "schema_columns": list(EXPERT_LABEL_COLUMNS),
            "sort_columns": list(EXPERT_LABEL_SORT_COLUMNS),
            "compression": PARQUET_COMPRESSION,
            "exported_at": exported_at.isoformat(),
        },
        sort_keys=True,
    ).encode("utf-8")


def write_expert_label_export(  # noqa: PLR0913 - one argument per exported release's own identifying fields
    store: ObjectStore,
    payload: bytes,
    *,
    release: str,
    label_count: int,
    prefix: str = EXPERT_LABEL_PREFIX,
    exported_at: datetime | None = None,
) -> ExpertLabelExport:
    """Write one release's part file and receipt, refusing an already-present object of other bytes.

    IDEMPOTENT, NEVER OVERWRITING. Re-exporting a release whose object already holds exactly these
    bytes is a no-op that reports `written=False`: the same labels were read twice and the store
    already agrees. Re-exporting one whose object holds DIFFERENT bytes is refused, because the ML
    service pins a model's training set by this object's digest -- silently replacing it would move
    the ground under an artifact that already cited it, and no reader would ever learn that the
    labels it trained on are not the labels now published under that name.
    """
    part_path = expert_label_part_path(release, prefix=prefix)
    digest = sha256_of(payload)
    existing = store.read_ml_object(part_path)
    if existing is not None and sha256_of(existing) != digest:
        raise ExpertLabelExportRefusal(
            f"{part_path} already holds different bytes (stored sha256 {sha256_of(existing)}, this export "
            f"{digest}); a release is immutable once exported, so export under a new release identifier "
            "rather than replacing the object an artifact may already cite"
        )
    export = ExpertLabelExport(
        release=release,
        part_path=part_path,
        receipt_path=expert_label_receipt_path(release, prefix=prefix),
        label_count=label_count,
        sha256=digest,
        byte_count=len(payload),
        written=existing is None,
    )
    if existing is not None:
        return export
    store.write_ml_object(payload, relative_path=part_path, content_type=PARQUET_CONTENT_TYPE)
    store.write_ml_object(
        _receipt_payload(export, exported_at=exported_at or datetime.now(UTC)),
        relative_path=export.receipt_path,
        content_type=RECEIPT_CONTENT_TYPE,
    )
    return export


async def read_release_labels(session: AsyncSession, *, release: str) -> tuple[Mapping[str, Any], ...]:
    """Read one release's labels, asking for one row more than the ceiling so an overrun is visible."""
    rows = await fetch_rows(
        session,
        _SELECT_LABELS,
        {"release_key": validate_release_identifier(release), "limit": MAX_EXPERT_LABELS + 1},
    )
    return tuple(rows)


async def export_release(
    session: AsyncSession,
    store: ObjectStore,
    *,
    release: str,
    prefix: str = EXPERT_LABEL_PREFIX,
    apply: bool,
) -> dict[str, object]:
    """Read, assemble and (unless this is a dry run) write one release; returns the operator receipt."""
    rows = await read_release_labels(session, release=release)
    table = build_expert_label_table(rows, release=release)
    payload = encode_expert_label_table(table)
    if not apply:
        return {
            "event": "plantgeo_expert_label_export",
            "release": release,
            "outcome": "dry_run",
            "part_path": expert_label_part_path(release, prefix=prefix),
            "label_count": table.num_rows,
            "sha256": sha256_of(payload),
            "byte_count": len(payload),
        }
    export = write_expert_label_export(store, payload, release=release, label_count=table.num_rows, prefix=prefix)
    return {
        "event": "plantgeo_expert_label_export",
        "release": export.release,
        "outcome": "written" if export.written else "already_present",
        "part_path": export.part_path,
        "receipt_path": export.receipt_path,
        "label_count": export.label_count,
        "sha256": export.sha256,
        "byte_count": export.byte_count,
    }


async def _process(release: str, *, prefix: str, apply: bool) -> dict[str, object]:
    """Open one read-only session and one store, and run the export inside them."""
    try:
        async with ingest_session() as session:
            receipt = await export_release(
                session,
                ObjectStore.from_settings(),
                release=release,
                prefix=prefix,
                apply=apply,
            )
            await session.rollback()
    except SQLAlchemyError as error:
        raise ExpertLabelExportRefusal(
            f"the expert label plane could not be read for release {release!r}; nothing was written"
        ) from error
    return receipt


@click.command("export-expert-labels")
@click.option("--release", required=True, help="The expert_label_release.release_key to export.")
@click.option(
    "--prefix",
    default=EXPERT_LABEL_PREFIX,
    show_default=True,
    help="Object-key prefix under ml/; a scratch prefix is how a rehearsal avoids the real one.",
)
@click.option(
    "--apply",
    "apply_",
    is_flag=True,
    help="Actually write the export. Without it the command is a dry run and writes nothing.",
)
def export_expert_labels(release: str, prefix: str, apply_: bool) -> None:
    """Export one reviewed expert-label release to ml/labels/expert/<release>/part-0000.parquet.

    DRY RUN BY DEFAULT: writing requires `--apply`. The export is immutable once written -- a later
    export of the same release under different bytes is refused outright, and an ML artifact may
    already pin its training set by this object's digest -- so the default has to be the reversible
    one. A dry run does everything but the two puts: it reads the release, assembles and encodes the
    pinned table, and prints the sha256 and byte count the apply run would write, so an operator can
    compare digests before committing to bytes that cannot be replaced.
    """
    receipt = asyncio.run(_process(release, prefix=prefix, apply=apply_))
    click.echo(json.dumps(receipt, sort_keys=True))
