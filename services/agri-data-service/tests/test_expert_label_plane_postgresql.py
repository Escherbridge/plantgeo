"""Database invariants of the expert label plane: envelope vocabulary, review states, immutability.

Uses its own head-agnostic fixture rather than `conftest.agri_db_dsn`: this suite must run
against a database carrying `20260814_0022`, and pinning a revision string here would couple it
to whichever revision another lane lands next. The two protections that matter are kept -- the
persistent `plantgeo` warehouse is refused, and a database without the plane fails loudly.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import TYPE_CHECKING

import psycopg2
import pytest

from agri_data_service.db.sql_queries import load_query_sql

if TYPE_CHECKING:
    from collections.abc import Iterator

# psycopg2 uses `%(name)s` bind params, not SQLAlchemy's `:name`; the query file has no `::`
# casts to collide with this substitution (confirmed by reading it), only the three named binds.
_TRAINABLE_LABELS_SQL = re.sub(r":(\w+)", r"%(\1)s", load_query_sql("execution/select_trainable_expert_labels.sql"))

PROTECTED_DATABASE_NAME = "plantgeo"
_SKIP_REASON = (
    "set AGRI_TEST_DATABASE_URL to a disposable database migrated through 20260814_0022 "
    "(never the persistent 'plantgeo' warehouse)"
)

pytestmark = pytest.mark.agri_db


@pytest.fixture
def label_plane_connection() -> Iterator[psycopg2.extensions.connection]:
    """A rolled-back connection to a disposable database that carries the label plane."""
    dsn = os.environ.get("AGRI_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip(_SKIP_REASON)
    connection = psycopg2.connect(dsn)
    connection.autocommit = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            row = cursor.fetchone()
            assert row is not None
            if row[0] == PROTECTED_DATABASE_NAME:
                pytest.fail(f"refusing to run against the persistent {PROTECTED_DATABASE_NAME!r} warehouse")
            cursor.execute("SELECT to_regclass('agri.expert_label')")
            present = cursor.fetchone()
            assert present is not None
            if present[0] is None:
                pytest.fail(
                    "AGRI_TEST_DATABASE_URL database has no agri.expert_label; run `alembic upgrade head` against it"
                )
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _seed_release_and_source(cursor: psycopg2.extensions.cursor) -> tuple[uuid.UUID, uuid.UUID]:
    release_key = f"test-release-{uuid.uuid4()}"
    cursor.execute(
        """
        INSERT INTO agri.expert_label_release (
            release_key, harvest_document_uri, harvest_document_checksum, harvested_at,
            label_count, draft_count, agent_reviewed_count, approved_count, rejected_count,
            slice_summary, review_tier, release_checksum, loader_code_checksum
        ) VALUES (%s, 'file://test', %s, now(), 1, 1, 0, 0, 0, '{}'::jsonb,
                  'agent_reviewed_pending_owner_signature', %s, %s)
        RETURNING id
        """,
        (release_key, "a" * 64, "b" * 64, "c" * 64),
    )
    release_row = cursor.fetchone()
    assert release_row is not None
    cursor.execute(
        """
        INSERT INTO agri.expert_label_source (
            source_key, doi, title, publication_year, journal_or_publisher, license_posture, source_checksum
        ) VALUES (%s, '10.0000/test', 'A cited work', 2024, 'A journal', 'structured_facts', %s)
        RETURNING id
        """,
        (f"doi:test-{uuid.uuid4()}", "d" * 64),
    )
    source_row = cursor.fetchone()
    assert source_row is not None
    return uuid.UUID(str(release_row[0])), uuid.UUID(str(source_row[0]))


def _insert_label(  # noqa: PLR0913
    cursor: psycopg2.extensions.cursor,
    release_id: uuid.UUID,
    source_id: uuid.UUID,
    *,
    envelope: str = '{"mean_annual_precipitation_mm": {"min": 250, "max": 350}}',
    refuted: bool = False,
    outcome: str = "fit",
    label_kind: str = "species_fit",
) -> uuid.UUID:
    cursor.execute(
        """
        INSERT INTO agri.expert_label (
            label_key, release_id, source_id, label_kind, subject, subject_normalized, outcome,
            condition_envelope, envelope_checksum, rationale, supporting_quote, confidence,
            confidence_weight, harvest_slice, citation_check_refuted, citation_check_doi_resolves,
            citation_check_reason, review_state, label_checksum
        ) VALUES (%s, %s, %s, %s, 'Pinus ponderosa', 'pinus ponderosa', %s, %s::jsonb, %s,
                  'because the source says so', 'a short attributed locator', 'medium', 0.6,
                  'species-trees-shrubs', %s, true, 'checked', 'draft', %s)
        RETURNING id
        """,
        (
            f"label-{uuid.uuid4()}",
            str(release_id),
            str(source_id),
            label_kind,
            outcome,
            envelope,
            "e" * 64,
            refuted,
            "f" * 64,
        ),
    )
    row = cursor.fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def test_envelope_vocabulary_is_enforced_by_a_check_constraint(
    label_plane_connection: psycopg2.extensions.connection,
) -> None:
    with label_plane_connection.cursor() as cursor:
        release_id, source_id = _seed_release_and_source(cursor)
        with pytest.raises(psycopg2.errors.CheckViolation):
            _insert_label(cursor, release_id, source_id, envelope='{"annual_snowfall_cm": 40}')


def test_an_empty_envelope_cannot_be_stored(
    label_plane_connection: psycopg2.extensions.connection,
) -> None:
    with label_plane_connection.cursor() as cursor:
        release_id, source_id = _seed_release_and_source(cursor)
        with pytest.raises(psycopg2.errors.CheckViolation):
            _insert_label(cursor, release_id, source_id, envelope="{}")


def test_an_outcome_must_match_its_label_kind(
    label_plane_connection: psycopg2.extensions.connection,
) -> None:
    with label_plane_connection.cursor() as cursor:
        release_id, source_id = _seed_release_and_source(cursor)
        with pytest.raises(psycopg2.errors.CheckViolation):
            _insert_label(cursor, release_id, source_id, outcome="effective")


def test_a_refuted_label_can_never_become_agent_reviewed(
    label_plane_connection: psycopg2.extensions.connection,
) -> None:
    with label_plane_connection.cursor() as cursor:
        release_id, source_id = _seed_release_and_source(cursor)
        label_id = _insert_label(cursor, release_id, source_id, refuted=True)
        with pytest.raises((psycopg2.errors.CheckViolation, psycopg2.errors.RaiseException)):
            cursor.execute(
                "UPDATE agri.expert_label SET review_state='agent_reviewed', reviewed_by='t', "
                "reviewed_at=now() WHERE id=%s",
                (str(label_id),),
            )


def test_approval_requires_an_owner_signature_reference(
    label_plane_connection: psycopg2.extensions.connection,
) -> None:
    with label_plane_connection.cursor() as cursor:
        release_id, source_id = _seed_release_and_source(cursor)
        label_id = _insert_label(cursor, release_id, source_id)
        cursor.execute(
            "UPDATE agri.expert_label SET review_state='agent_reviewed', reviewed_by='verifier', "
            "reviewed_at=now() WHERE id=%s",
            (str(label_id),),
        )
        with pytest.raises((psycopg2.errors.CheckViolation, psycopg2.errors.RaiseException)):
            cursor.execute("UPDATE agri.expert_label SET review_state='approved' WHERE id=%s", (str(label_id),))


def test_a_reviewed_label_is_immutable_in_content(
    label_plane_connection: psycopg2.extensions.connection,
) -> None:
    with label_plane_connection.cursor() as cursor:
        release_id, source_id = _seed_release_and_source(cursor)
        label_id = _insert_label(cursor, release_id, source_id)
        cursor.execute(
            "UPDATE agri.expert_label SET review_state='agent_reviewed', reviewed_by='verifier', "
            "reviewed_at=now() WHERE id=%s",
            (str(label_id),),
        )
        with pytest.raises(psycopg2.errors.RaiseException, match="immutable"):
            cursor.execute("UPDATE agri.expert_label SET outcome='unfit' WHERE id=%s", (str(label_id),))


def test_a_reviewed_label_cannot_be_deleted(
    label_plane_connection: psycopg2.extensions.connection,
) -> None:
    with label_plane_connection.cursor() as cursor:
        release_id, source_id = _seed_release_and_source(cursor)
        label_id = _insert_label(cursor, release_id, source_id)
        cursor.execute(
            "UPDATE agri.expert_label SET review_state='agent_reviewed', reviewed_by='verifier', "
            "reviewed_at=now() WHERE id=%s",
            (str(label_id),),
        )
        with pytest.raises(psycopg2.errors.RaiseException):
            cursor.execute("DELETE FROM agri.expert_label WHERE id=%s", (str(label_id),))


def test_a_draft_label_may_still_be_corrected_and_deleted(
    label_plane_connection: psycopg2.extensions.connection,
) -> None:
    with label_plane_connection.cursor() as cursor:
        release_id, source_id = _seed_release_and_source(cursor)
        label_id = _insert_label(cursor, release_id, source_id)
        cursor.execute("UPDATE agri.expert_label SET outcome='unfit' WHERE id=%s", (str(label_id),))
        cursor.execute("DELETE FROM agri.expert_label WHERE id=%s", (str(label_id),))
        assert cursor.rowcount == 1


def test_the_review_state_machine_refuses_a_skipped_transition(
    label_plane_connection: psycopg2.extensions.connection,
) -> None:
    with label_plane_connection.cursor() as cursor:
        release_id, source_id = _seed_release_and_source(cursor)
        label_id = _insert_label(cursor, release_id, source_id)
        with pytest.raises((psycopg2.errors.CheckViolation, psycopg2.errors.RaiseException)):
            cursor.execute(
                "UPDATE agri.expert_label SET review_state='approved', reviewed_by='x', "
                "reviewed_at=now(), owner_signature_reference='forged' WHERE id=%s",
                (str(label_id),),
            )


def test_a_training_receipt_cannot_claim_publication(
    label_plane_connection: psycopg2.extensions.connection,
) -> None:
    with label_plane_connection.cursor() as cursor:
        release_id, _source_id = _seed_release_and_source(cursor)
        with pytest.raises(psycopg2.errors.CheckViolation):
            cursor.execute(
                """
                INSERT INTO agri.recommendation_training_receipt (
                    training_key, model_name, model_kind, label_release_id, label_review_tier,
                    feature_schema_version, label_count, training_instance_count, source_count,
                    artifact_checksum, evaluation_metrics, evaluation_checksum, parameter_checksum,
                    training_code_checksum, publication_authorized, started_at, completed_at
                ) VALUES (%s, 'm', 'species_fit', %s, 'agent_reviewed_pending_owner_signature',
                          'agri_covariates_v1', 1, 1, 1, %s, '{}'::jsonb, %s, %s, %s, true, now(), now())
                """,
                (f"receipt-{uuid.uuid4()}", str(release_id), "a" * 64, "b" * 64, "c" * 64, "d" * 64),
            )


def test_the_0013_causal_plane_is_untouched_by_this_plane(
    label_plane_connection: psycopg2.extensions.connection,
) -> None:
    with label_plane_connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM agri.strategy_label_episode")
        episodes = cursor.fetchone()
        cursor.execute("SELECT count(*) FROM agri.strategy_label_release")
        releases = cursor.fetchone()
        cursor.execute(
            """
            SELECT count(*) FROM pg_constraint c
             JOIN pg_class t ON t.oid = c.conrelid
             JOIN pg_class r ON r.oid = c.confrelid
             JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname='agri' AND c.contype='f'
              AND t.relname LIKE 'expert_label%%'
              AND r.relname LIKE 'strategy_%%'
            """
        )
        crossing = cursor.fetchone()
    assert episodes is not None and episodes[0] == 0  # noqa: PT018
    assert releases is not None and releases[0] == 0  # noqa: PT018
    assert crossing is not None and crossing[0] == 0  # noqa: PT018


def _insert_label_as_approved(
    cursor: psycopg2.extensions.cursor,
    release_id: uuid.UUID,
    source_id: uuid.UUID,
    *,
    refuted: bool,
    owner_signature_reference: str | None,
) -> None:
    """Attempt a bare INSERT straight into review_state='approved' -- the path the review
    guard's INSERT branch and the two CHECKs on `expert_label` must each independently refuse.
    Legitimate rows never take this path: every loader inserts at 'draft' and only the guarded
    UPDATE transition in advance_expert_label_review.sql can move a row forward from there.
    """
    cursor.execute(
        """
        INSERT INTO agri.expert_label (
            label_key, release_id, source_id, label_kind, subject, subject_normalized, outcome,
            condition_envelope, envelope_checksum, rationale, supporting_quote, confidence,
            confidence_weight, harvest_slice, citation_check_refuted, citation_check_doi_resolves,
            citation_check_reason, review_state, reviewed_by, reviewed_at,
            owner_signature_reference, label_checksum
        ) VALUES (%s, %s, %s, 'species_fit', 'Pinus ponderosa', 'pinus ponderosa', 'fit',
                  '{"mean_annual_precipitation_mm": {"min": 250, "max": 350}}'::jsonb, %s,
                  'because the source says so', 'a short attributed locator', 'medium', 0.6,
                  'species-trees-shrubs', %s, true, 'checked', 'approved', 'attacker', now(),
                  %s, %s)
        """,
        (
            f"label-{uuid.uuid4()}",
            str(release_id),
            str(source_id),
            "e" * 64,
            refuted,
            owner_signature_reference,
            "f" * 64,
        ),
    )


def test_a_direct_insert_of_a_refuted_label_as_approved_is_rejected(
    label_plane_connection: psycopg2.extensions.connection,
) -> None:
    """End-to-end: with every guard live, a bare INSERT can never mint a refuted-but-approved row."""
    with label_plane_connection.cursor() as cursor:
        release_id, source_id = _seed_release_and_source(cursor)
        with pytest.raises((psycopg2.errors.CheckViolation, psycopg2.errors.RaiseException)):
            _insert_label_as_approved(
                cursor, release_id, source_id, refuted=True, owner_signature_reference="a-real-signature"
            )


def test_a_direct_insert_with_an_empty_string_signature_as_approved_is_rejected(
    label_plane_connection: psycopg2.extensions.connection,
) -> None:
    """End-to-end: an empty-but-not-NULL signature must not satisfy 'owner-countersigned'."""
    with label_plane_connection.cursor() as cursor:
        release_id, source_id = _seed_release_and_source(cursor)
        with pytest.raises((psycopg2.errors.CheckViolation, psycopg2.errors.RaiseException)):
            _insert_label_as_approved(cursor, release_id, source_id, refuted=False, owner_signature_reference="")


def test_the_agent_review_not_refuted_check_covers_approved_on_its_own(
    label_plane_connection: psycopg2.extensions.connection,
) -> None:
    """Isolate ck_expert_label_agent_review_not_refuted from the INSERT trigger: disable the
    trigger and prove the widened CHECK alone still refuses 'approved' + refuted."""
    with label_plane_connection.cursor() as cursor:
        release_id, source_id = _seed_release_and_source(cursor)
        cursor.execute("ALTER TABLE agri.expert_label DISABLE TRIGGER expert_label_review_guard")
        with pytest.raises(psycopg2.errors.CheckViolation):
            _insert_label_as_approved(
                cursor, release_id, source_id, refuted=True, owner_signature_reference="a-real-signature"
            )


def test_the_approval_signature_check_rejects_empty_string_on_its_own(
    label_plane_connection: psycopg2.extensions.connection,
) -> None:
    """Isolate ck_expert_label_approval_needs_owner_signature from the INSERT trigger: disable
    the trigger and prove the tightened CHECK alone refuses an empty-string signature."""
    with label_plane_connection.cursor() as cursor:
        release_id, source_id = _seed_release_and_source(cursor)
        cursor.execute("ALTER TABLE agri.expert_label DISABLE TRIGGER expert_label_review_guard")
        with pytest.raises(psycopg2.errors.CheckViolation):
            _insert_label_as_approved(cursor, release_id, source_id, refuted=False, owner_signature_reference="")


def test_the_trainable_labels_query_excludes_a_refuted_row(
    label_plane_connection: psycopg2.extensions.connection,
) -> None:
    """Defense-in-depth proof for select_trainable_expert_labels.sql's own filter: a refuted row
    can never legitimately reach 'agent_reviewed' (the trigger and the CHECK both refuse it), so
    this test lifts both gates inside this rolled-back transaction to admit one anyway, and
    confirms the query -- not just the schema -- still excludes it.
    """
    with label_plane_connection.cursor() as cursor:
        release_id, source_id = _seed_release_and_source(cursor)
        cursor.execute("SELECT release_key FROM agri.expert_label_release WHERE id = %s", (str(release_id),))
        release_key_row = cursor.fetchone()
        assert release_key_row is not None
        release_key = release_key_row[0]

        trainable_id = _insert_label(cursor, release_id, source_id, refuted=False)
        cursor.execute(
            "UPDATE agri.expert_label SET review_state='agent_reviewed', reviewed_by='verifier', "
            "reviewed_at=now() WHERE id=%s",
            (str(trainable_id),),
        )

        cursor.execute("ALTER TABLE agri.expert_label DISABLE TRIGGER expert_label_review_guard")
        cursor.execute("ALTER TABLE agri.expert_label DROP CONSTRAINT ck_expert_label_agent_review_not_refuted")
        refuted_id = _insert_label(cursor, release_id, source_id, refuted=True)
        cursor.execute(
            "UPDATE agri.expert_label SET review_state='agent_reviewed', reviewed_by='verifier', "
            "reviewed_at=now() WHERE id=%s",
            (str(refuted_id),),
        )

        cursor.execute(
            _TRAINABLE_LABELS_SQL,
            {"release_key": release_key, "label_kind": "species_fit", "row_limit": 10},
        )
        rows = cursor.fetchall()

    label_ids = {str(row[0]) for row in rows}
    assert str(trainable_id) in label_ids
    assert str(refuted_id) not in label_ids
