"""The archive record: the right capture strategy per object kind, and a digest that is never faked."""

from __future__ import annotations

from datetime import date
from typing import Final

import pytest

from agri_data_service.retirement.archive import (
    ARCHIVE_PREFIX,
    ArchiveError,
    ArchiveForm,
    build_archive_record,
)
from agri_data_service.retirement.ledger import DropForm, ObjectKind

_AS_OF: Final = date(2026, 9, 4)
_DIGEST: Final = "a" * 64


def test_a_table_drop_is_one_restorable_pg_dump() -> None:
    """Definition and rows travel together for an ordinary table."""
    record = build_archive_record(
        relation="geo.historical_fire_data",
        object_kind=ObjectKind.TABLE,
        drop_form=DropForm.TABLE_DROP,
        as_of=_AS_OF,
    )

    assert record.form is ArchiveForm.PG_DUMP_TABLE
    assert len(record.commands) == 1
    assert "--format=custom" in record.commands[0].command


def test_a_materialized_view_gets_a_copy_because_pg_dump_omits_its_rows() -> None:
    """The definition alone restores nothing once this track deletes the relations REFRESH reads."""
    record = build_archive_record(
        relation="geo.mv_soil_survey_grid",
        object_kind=ObjectKind.MATERIALIZED_VIEW,
        drop_form=DropForm.MATERIALIZED_VIEW_DROP,
        as_of=_AS_OF,
    )

    assert record.form is ArchiveForm.DEFINITION_PLUS_COPY
    assert any("\\copy" in command.command for command in record.commands)
    assert any("never its contents" in note for note in record.notes)


def test_a_plain_view_claims_only_its_definition() -> None:
    """A view owns no rows; claiming an archive of them would be a claim about its base relations."""
    record = build_archive_record(
        relation="geo.v_observation_day_census",
        object_kind=ObjectKind.VIEW,
        drop_form=DropForm.VIEW_DROP,
        as_of=_AS_OF,
    )

    assert record.form is ArchiveForm.DEFINITION_ONLY
    assert all("\\copy" not in command.command for command in record.commands)


def test_a_row_delete_without_a_predicate_is_refused() -> None:
    """Fact 1's consequence: an unfiltered dump would copy the surviving community rows too."""
    with pytest.raises(ArchiveError, match="predicate"):
        build_archive_record(
            relation="geo.features",
            object_kind=ObjectKind.TABLE,
            drop_form=DropForm.ROW_DELETE,
            as_of=_AS_OF,
        )


def test_a_row_delete_archives_exactly_the_predicate_it_deletes_by() -> None:
    """The archive and the DELETE must describe the same rows or the drop is not reversible."""
    predicate = "layer_id IN (SELECT id FROM geo.layers WHERE name IN ('sensors'))"

    record = build_archive_record(
        relation="geo.features",
        object_kind=ObjectKind.TABLE,
        drop_form=DropForm.ROW_DELETE,
        as_of=_AS_OF,
        row_filter_sql=predicate,
    )

    assert record.form is ArchiveForm.COPY_ROW_SUBSET
    assert any(predicate in command.command for command in record.commands)


def test_the_digest_is_owed_until_a_dump_actually_runs() -> None:
    """This tool never runs pg_dump, so its own packets can never claim an archive exists."""
    record = build_archive_record(
        relation="geo.mv_orphan",
        object_kind=ObjectKind.MATERIALIZED_VIEW,
        drop_form=DropForm.MATERIALIZED_VIEW_DROP,
        as_of=_AS_OF,
    )

    assert record.sha256 is None
    assert record.sha256_state == "owed"
    assert record.satisfied is False


def test_a_placeholder_can_never_be_recorded_as_a_digest() -> None:
    """The track's tripwire: never emit a digest that was not computed from the object it describes."""
    for candidate in ("TBD", "pending", "a" * 63, "A" * 64):
        with pytest.raises(ArchiveError, match="not a sha256 digest"):
            build_archive_record(
                relation="geo.mv_orphan",
                object_kind=ObjectKind.MATERIALIZED_VIEW,
                drop_form=DropForm.MATERIALIZED_VIEW_DROP,
                as_of=_AS_OF,
                sha256=candidate,
            )


def test_a_real_digest_satisfies_the_record() -> None:
    """The only route to `ready`: an operator took the dump and pasted its real sha256."""
    record = build_archive_record(
        relation="geo.mv_orphan",
        object_kind=ObjectKind.MATERIALIZED_VIEW,
        drop_form=DropForm.MATERIALIZED_VIEW_DROP,
        as_of=_AS_OF,
        sha256=_DIGEST,
    )

    assert record.satisfied is True
    assert record.sha256_state == "recorded"


def test_no_command_ever_interpolates_a_dsn() -> None:
    """A packet lands in `conductor/evidence/`; a credential in one is a credential in git."""
    record = build_archive_record(
        relation="geo.mv_orphan",
        object_kind=ObjectKind.MATERIALIZED_VIEW,
        drop_form=DropForm.MATERIALIZED_VIEW_DROP,
        as_of=_AS_OF,
    )

    for command in record.commands:
        assert '"$LOCAL_SOURCE_LOADER_DATABASE_URL"' in command.command
        assert "postgres://" not in command.command
        assert "postgresql://" not in command.command


def test_every_artifact_lands_under_the_one_retirement_prefix() -> None:
    """One prefix per track, so a lifecycle rule can name the retirement corpus and nothing else."""
    record = build_archive_record(
        relation="geo.mv_orphan",
        object_kind=ObjectKind.MATERIALIZED_VIEW,
        drop_form=DropForm.MATERIALIZED_VIEW_DROP,
        as_of=_AS_OF,
        bucket="plantgeo-parquet-9ymvp7gv",
    )

    assert record.object_keys
    assert all(key.startswith(f"{ARCHIVE_PREFIX}/geo.mv_orphan/2026-09-04/") for key in record.object_keys)
    assert all("plantgeo-parquet-9ymvp7gv" in command for command in record.upload_commands)


def test_one_key_override_cannot_stand_for_a_two_artifact_archive() -> None:
    """The dropped artifact would be the `\\copy` holding the rows -- the only part that matters."""
    with pytest.raises(ArchiveError, match="produces 2 artifacts"):
        build_archive_record(
            relation="geo.mv_orphan",
            object_kind=ObjectKind.MATERIALIZED_VIEW,
            drop_form=DropForm.MATERIALIZED_VIEW_DROP,
            as_of=_AS_OF,
            object_key="retirement/elsewhere/one.dump",
        )


def test_an_unset_bucket_renders_a_placeholder_rather_than_a_wrong_name() -> None:
    """Guessing a bucket is how an archive lands somewhere nobody looks for it."""
    record = build_archive_record(
        relation="geo.mv_orphan",
        object_kind=ObjectKind.MATERIALIZED_VIEW,
        drop_form=DropForm.MATERIALIZED_VIEW_DROP,
        as_of=_AS_OF,
    )

    assert record.bucket == "${OBJECT_STORE_BUCKET}"
