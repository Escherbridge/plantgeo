"""D1 item 3: the archive record -- the command that would be run, and the digest that is owed.

THIS MODULE RUNS NOTHING. It builds the exact `pg_dump`/`\\copy` invocation, the R2 key the artifact
would land under, and an explicitly EMPTY sha256 slot marked owed. A missing archived snapshot is a
hard stop in the track's own tripwires, so the packet says so in the verdict rather than leaving a
blank a reader might mistake for a pass.

`pg_dump` DOES NOT DUMP A MATERIALIZED VIEW'S ROWS. It emits the definition and a `REFRESH`; the
contents are regenerated from base relations that this very track is deleting. So a matview archived
with `pg_dump --table` alone would preserve a recipe whose ingredients are gone -- an archive that
looks complete and restores nothing. Every matview here gets a definition dump AND a `\\copy` of its
rows, and the note says why both exist.

A ROW-DELETE ARCHIVES A PREDICATE, NOT A TABLE. `geo.features` keeps `interventions` forever, so its
archive must be the seven environmental layers' rows and nothing else -- dumping the whole table would
copy live community data into a retirement prefix. The row filter is required for that form and its
absence is refused, not defaulted.

NO DSN IS EVER INTERPOLATED. Commands reference `"$LOCAL_SOURCE_LOADER_DATABASE_URL"` by name, so a
packet committed to `conductor/evidence/` can never carry a credential.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from agri_data_service.retirement.ledger import DropForm, ObjectKind

if TYPE_CHECKING:
    from datetime import date

#: Where every retirement artifact lands. One prefix per track, so a bucket lifecycle rule can name
#: the whole retirement corpus without touching a lane's published Parquet.
ARCHIVE_PREFIX: Final = "retirement/environmental_postgres_retirement_20260904"

#: The environment variable the archive commands read the DSN from. Named, never expanded here.
DEFAULT_DSN_ENVIRONMENT_VARIABLE: Final = "LOCAL_SOURCE_LOADER_DATABASE_URL"

_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


class ArchiveError(RuntimeError):
    """Raised when an archive record cannot be built truthfully."""


class ArchiveForm(StrEnum):
    """How the relation's contents can actually be captured."""

    #: An ordinary table: `pg_dump --table` captures definition and rows together.
    PG_DUMP_TABLE = "pg_dump_table"
    #: A filtered subset of a table that survives the drop.
    COPY_ROW_SUBSET = "copy_row_subset"
    #: A materialized view: definition via `pg_dump`, rows via `\copy`, because `pg_dump` omits them.
    DEFINITION_PLUS_COPY = "definition_plus_copy"
    #: A plain view: it owns no rows; only its definition can be archived.
    DEFINITION_ONLY = "definition_only"


@dataclass(frozen=True, slots=True)
class ArchiveCommand:
    """One shell command the operator runs, and what it produces."""

    purpose: str
    command: str
    artifact: str


@dataclass(frozen=True, slots=True)
class ArchiveRecord:
    """The archived-snapshot section of a drop packet."""

    relation: str
    form: ArchiveForm
    commands: tuple[ArchiveCommand, ...]
    bucket: str
    object_keys: tuple[str, ...]
    upload_commands: tuple[str, ...]
    digest_command: str
    sha256: str | None
    notes: tuple[str, ...]

    @property
    def sha256_state(self) -> str:
        """`owed` until a real digest of a real artifact is recorded; never a placeholder."""
        return "recorded" if self.sha256 is not None else "owed"

    @property
    def satisfied(self) -> bool:
        """True only when a digest has been recorded, which means the dump actually ran."""
        return self.sha256 is not None

    def to_json_dict(self) -> dict[str, object]:
        """Render the archive section exactly as the packet prints it."""
        return {
            "form": str(self.form),
            "bucket": self.bucket,
            "object_keys": list(self.object_keys),
            "commands": [
                {"purpose": command.purpose, "command": command.command, "artifact": command.artifact}
                for command in self.commands
            ],
            "upload_commands": list(self.upload_commands),
            "digest_command": self.digest_command,
            "sha256": self.sha256,
            "sha256_state": self.sha256_state,
            "notes": list(self.notes),
        }


def _artifact_slug(relation: str) -> str:
    """Render a relation as a filesystem- and key-safe stem."""
    return relation.replace(".", "__")


def _validate_sha256(value: str | None) -> str | None:
    """Accept a real 64-character lowercase hex digest or nothing at all.

    The track's tripwire is "never emit a digest that was not computed from the object it describes".
    A malformed value is refused here so `TBD`, `pending` or a truncated paste can never be recorded
    as if it were proof.
    """
    if value is None:
        return None
    candidate = value.strip()
    if not _SHA256_PATTERN.match(candidate):
        raise ArchiveError(
            f"{value!r} is not a sha256 digest (64 lowercase hex characters). Leave it unset and the packet "
            "records the digest as owed; a placeholder would read as proof"
        )
    return candidate


def _archive_form(*, object_kind: ObjectKind, drop_form: DropForm) -> ArchiveForm:
    """Pick the capture strategy from what the object IS and how much of it is leaving."""
    if drop_form is DropForm.ROW_DELETE:
        return ArchiveForm.COPY_ROW_SUBSET
    if object_kind is ObjectKind.MATERIALIZED_VIEW:
        return ArchiveForm.DEFINITION_PLUS_COPY
    if object_kind is ObjectKind.VIEW:
        return ArchiveForm.DEFINITION_ONLY
    return ArchiveForm.PG_DUMP_TABLE


def _schema_dump_command(relation: str, *, dsn_variable: str, artifact: str) -> ArchiveCommand:
    """Build the definition-only dump every form carries, so the restore target's shape is pinned."""
    return ArchiveCommand(
        purpose="pin the object definition and column order at archive time",
        command=(
            f'pg_dump "${dsn_variable}" --no-owner --no-privileges --schema-only '
            f"--table='{relation}' --file='{artifact}'"
        ),
        artifact=artifact,
    )


def _copy_command(*, select: str, artifact: str, dsn_variable: str, purpose: str) -> ArchiveCommand:
    """Build a client-side `\\copy` of one SELECT to a local CSV, header included."""
    return ArchiveCommand(
        purpose=purpose,
        command=(
            f'psql "${dsn_variable}" --no-psqlrc --quiet '
            f"-c \"\\copy ({select}) TO '{artifact}' WITH (FORMAT csv, HEADER true)\""
        ),
        artifact=artifact,
    )


def build_archive_record(  # noqa: PLR0913 - each argument is one independent coordinate of the archive record
    *,
    relation: str,
    object_kind: ObjectKind,
    drop_form: DropForm,
    as_of: date,
    row_filter_sql: str | None = None,
    bucket: str | None = None,
    sha256: str | None = None,
    object_key: str | None = None,
    dsn_variable: str = DEFAULT_DSN_ENVIRONMENT_VARIABLE,
) -> ArchiveRecord:
    """Build the archive section. Produces commands; runs none of them.

    `sha256` is the digest of an archive the OPERATOR already took. Left unset -- which is the whole
    of this tool's dry-run life -- the record says `owed` and `packet.py` turns that into a blocker.
    """
    digest = _validate_sha256(sha256)
    form = _archive_form(object_kind=object_kind, drop_form=drop_form)
    slug = _artifact_slug(relation)
    stamp = as_of.isoformat()
    definition_artifact = f"{slug}-definition-{stamp}.sql"
    rows_artifact = f"{slug}-rows-{stamp}.csv"
    dump_artifact = f"{slug}-{stamp}.dump"
    notes: list[str] = []
    commands: list[ArchiveCommand] = []

    if form is ArchiveForm.PG_DUMP_TABLE:
        commands.append(
            ArchiveCommand(
                purpose="archive the table's definition and every row in one restorable artifact",
                command=(
                    f'pg_dump "${dsn_variable}" --no-owner --no-privileges --format=custom '
                    f"--table='{relation}' --file='{dump_artifact}'"
                ),
                artifact=dump_artifact,
            )
        )
    elif form is ArchiveForm.COPY_ROW_SUBSET:
        if not row_filter_sql:
            raise ArchiveError(
                f"a row-delete archive of {relation} needs the predicate it deletes by; without one the "
                "archive would either dump the whole table (copying rows that are staying) or nothing"
            )
        commands.append(_schema_dump_command(relation, dsn_variable=dsn_variable, artifact=definition_artifact))
        commands.append(
            _copy_command(
                select=f"SELECT * FROM {relation} WHERE {row_filter_sql}",
                artifact=rows_artifact,
                dsn_variable=dsn_variable,
                purpose="archive exactly the rows the drop deletes, and no others",
            )
        )
        notes.append(
            "the predicate is the archive's whole scope: rows outside it stay in PostgreSQL and must not "
            "appear in a retirement artifact"
        )
        notes.append(
            f"restore is `\\copy {relation} FROM '{rows_artifact}' WITH (FORMAT csv, HEADER true)` against the "
            f"column order pinned in {definition_artifact}"
        )
    elif form is ArchiveForm.DEFINITION_PLUS_COPY:
        commands.append(_schema_dump_command(relation, dsn_variable=dsn_variable, artifact=definition_artifact))
        commands.append(
            _copy_command(
                select=f"SELECT * FROM {relation}",
                artifact=rows_artifact,
                dsn_variable=dsn_variable,
                purpose="archive the materialized rows, which pg_dump does not emit",
            )
        )
        notes.append(
            "pg_dump writes a materialized view's DEFINITION and a REFRESH, never its contents. This track "
            "is deleting the base relations that REFRESH would read, so the definition alone restores "
            "nothing -- the \\copy is the actual archive"
        )
    else:
        commands.append(_schema_dump_command(relation, dsn_variable=dsn_variable, artifact=definition_artifact))
        notes.append(
            "a plain view owns no rows; its contents live in its base relations, each of which carries its "
            "own packet. Only the definition is archivable and only the definition is claimed here"
        )

    resolved_bucket = bucket or "${OBJECT_STORE_BUCKET}"
    keys = tuple(f"{ARCHIVE_PREFIX}/{relation}/{stamp}/{command.artifact}" for command in commands)
    if object_key is not None:
        # One override can only ever name one artifact. Letting it stand for a two-artifact archive
        # would silently drop the second from the packet's key list -- and the dropped one is the
        # `\copy` that holds the actual rows.
        if len(commands) != 1:
            raise ArchiveError(
                f"--archive-key names one object, but a {form} archive of {relation} produces "
                f"{len(commands)} artifacts: {', '.join(command.artifact for command in commands)}"
            )
        keys = (object_key,)
    uploads = tuple(
        f"aws s3 cp '{command.artifact}' 's3://{resolved_bucket}/{key}' --endpoint-url \"$OBJECT_STORE_ENDPOINT_URL\""
        for command, key in zip(commands, keys, strict=True)
    )
    notes.append("no command in this record has been run; the sha256 is owed until one is")
    return ArchiveRecord(
        relation=relation,
        form=form,
        commands=tuple(commands),
        bucket=resolved_bucket,
        object_keys=keys,
        upload_commands=uploads,
        digest_command="sha256sum " + " ".join(f"'{command.artifact}'" for command in commands),
        sha256=digest,
        notes=tuple(notes),
    )


__all__ = [
    "ARCHIVE_PREFIX",
    "DEFAULT_DSN_ENVIRONMENT_VARIABLE",
    "ArchiveCommand",
    "ArchiveError",
    "ArchiveForm",
    "ArchiveRecord",
    "build_archive_record",
]
