"""Database contract tests for the `20260814_0021` derived-signal lineage plane.

Each rejection test writes a row that genuinely breaks one rule and asserts PostgreSQL refuses it.
The enforcement under test is declarative (composite foreign keys plus CHECK constraints), so these
also prove the DAG properties hold on a path no trigger could be disabled around.

Needs `AGRI_TEST_DATABASE_URL` pointed at a disposable database migrated past `20260814_0021`.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final
from uuid import uuid4

import psycopg2
import pytest
from psycopg2 import errors

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.agri_db

AGRI_TEST_DATABASE_URL_ENV: Final = "AGRI_TEST_DATABASE_URL"
PROTECTED_DATABASE_NAME: Final = "plantgeo"
BASE_TIME: Final = datetime(2025, 1, 1, tzinfo=UTC)
DIGEST_A: Final = "a" * 64
DIGEST_B: Final = "b" * 64
# The disposable database is not necessarily empty -- a real persistence run writes the same
# lattice series -- so every row this module writes carries a per-process series prefix and every
# count is scoped to it.
SERIES_PREFIX: Final = f"test:{uuid4().hex[:12]}"
DEFAULT_SERIES_KEY: Final = f"{SERIES_PREFIX}|wind_speed|surface"

_NEW_RELATIONS: Final[tuple[str, ...]] = (
    "forecast_signal_definition",
    "forecast_derived_signal_value",
    "forecast_signal_lineage_edge",
    "forecast_candidate_evaluation",
    "forecast_candidate_evaluation_origin",
)

_PUBLICATION_RELATIONS: Final[tuple[str, ...]] = (
    "forecast_publication",
    "forecast_publication_item",
    "forecast_receipt",
    "forecast_value",
    "forecast_run",
)


@pytest.fixture
def lineage_connection() -> Iterator[psycopg2.extensions.connection]:
    """A rolled-back connection to a disposable database carrying the 0021 lineage plane.

    Deliberately local rather than the shared `agri_db_connection` fixture: that fixture pins one
    `EXPECTED_ALEMBIC_HEAD` string, and this plane must be testable while sibling revisions are
    landing. The precondition asserted here is the one that matters -- the relations exist.
    """
    dsn = os.environ.get(AGRI_TEST_DATABASE_URL_ENV)
    if not dsn:
        pytest.skip(
            f"set {AGRI_TEST_DATABASE_URL_ENV} to a disposable database migrated past 20260814_0021 "
            f"(never the persistent {PROTECTED_DATABASE_NAME!r} warehouse)"
        )
    connection = psycopg2.connect(dsn)
    connection.autocommit = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            row = cursor.fetchone()
            assert row is not None
            if row[0] == PROTECTED_DATABASE_NAME:
                pytest.fail(f"refusing to run against the persistent {PROTECTED_DATABASE_NAME!r} warehouse")
            cursor.execute("SELECT to_regclass('agri.forecast_derived_signal_value')")
            present = cursor.fetchone()
            assert present is not None
            if present[0] is None:
                pytest.fail("the target database has not applied 20260814_0021; run `alembic upgrade head`")
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _insert_definition(
    connection: psycopg2.extensions.connection,
    *,
    max_dependency_depth: int = 8,
) -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO agri.forecast_signal_definition (
                signal_key, signal_version, unit, spatial_support_key, temporal_grain,
                recipe_key, recipe_checksum, parent_schema, max_dependency_depth, definition_checksum
            ) VALUES (
                %s, 'v1', 'm/s', 'surface', interval '1 day',
                'residual_feedback_recipe', %s, '[]'::jsonb, %s, %s
            ) RETURNING id
            """,
            (f"residual_feedback_{uuid4().hex[:12]}", DIGEST_A, max_dependency_depth, DIGEST_B),
        )
        row = cursor.fetchone()
        assert row is not None
        return str(row[0])


def _insert_value(  # noqa: PLR0913
    connection: psycopg2.extensions.connection,
    definition_id: str,
    *,
    depth: int,
    origin_offset_days: int,
    valid_offset_days: int,
    availability_offset_days: int,
    max_dependency_depth: int = 8,
    series_key: str = DEFAULT_SERIES_KEY,
    value: float | None = 1.5,
    checksum_override: str | None = None,
) -> int:
    origin = BASE_TIME + timedelta(days=origin_offset_days)
    valid = BASE_TIME + timedelta(days=valid_offset_days)
    availability = BASE_TIME + timedelta(days=availability_offset_days)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO agri.forecast_derived_signal_value (
                signal_definition_id, max_dependency_depth, series_key, lineage_depth,
                origin_cutoff_time, valid_time, availability_time, signal_value,
                input_release_checksum, recipe_checksum, value_checksum
            ) VALUES (
                %(definition_id)s, %(max_depth)s, %(series_key)s, %(depth)s,
                %(origin)s, %(valid)s, %(availability)s, %(value)s,
                %(input_checksum)s, %(recipe_checksum)s,
                COALESCE(%(override)s, agri.forecast_derived_signal_value_checksum(
                    %(definition_id)s, %(series_key)s, %(origin)s, %(valid)s, %(availability)s,
                    %(depth)s, %(value)s, %(input_checksum)s, %(recipe_checksum)s
                ))
            ) RETURNING id
            """,
            {
                "definition_id": definition_id,
                "max_depth": max_dependency_depth,
                "series_key": series_key,
                "depth": depth,
                "origin": origin,
                "valid": valid,
                "availability": availability,
                "value": value,
                "input_checksum": DIGEST_A,
                "recipe_checksum": DIGEST_A,
                "override": checksum_override,
            },
        )
        row = cursor.fetchone()
        assert row is not None
        return int(row[0])


def _insert_edge(
    connection: psycopg2.extensions.connection,
    child_id: int,
    parent_id: int,
    *,
    parent_role: str = "feedback_parent",
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO agri.forecast_signal_lineage_edge (
                child_value_id, child_origin_cutoff_time, child_valid_time,
                child_availability_time, child_lineage_depth,
                parent_value_id, parent_origin_cutoff_time, parent_valid_time,
                parent_availability_time, parent_lineage_depth, parent_role
            )
            SELECT
                child.id, child.origin_cutoff_time, child.valid_time,
                child.availability_time, child.lineage_depth,
                parent.id, parent.origin_cutoff_time, parent.valid_time,
                parent.availability_time, parent.lineage_depth, %s
            FROM agri.forecast_derived_signal_value AS child
            CROSS JOIN agri.forecast_derived_signal_value AS parent
            WHERE child.id = %s AND parent.id = %s
            """,
            (parent_role, child_id, parent_id),
        )


def test_a_valid_three_deep_chain_is_accepted(lineage_connection: psycopg2.extensions.connection) -> None:
    definition_id = _insert_definition(lineage_connection)
    ids = [
        _insert_value(
            lineage_connection,
            definition_id,
            depth=depth,
            origin_offset_days=depth * 10,
            valid_offset_days=depth * 10 - 1,
            availability_offset_days=depth * 10,
        )
        for depth in range(3)
    ]
    for depth in range(1, 3):
        _insert_edge(lineage_connection, ids[depth], ids[depth - 1])
    with lineage_connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM agri.forecast_signal_lineage_edge AS edge "
            "JOIN agri.forecast_derived_signal_value AS child ON child.id = edge.child_value_id "
            "WHERE child.series_key LIKE %s",
            (f"{SERIES_PREFIX}|%",),
        )
        row = cursor.fetchone()
    assert row is not None
    assert row[0] == 2  # noqa: PLR2004


def test_a_parent_available_after_the_child_origin_is_rejected(
    lineage_connection: psycopg2.extensions.connection,
) -> None:
    definition_id = _insert_definition(lineage_connection)
    parent = _insert_value(
        lineage_connection,
        definition_id,
        depth=0,
        origin_offset_days=0,
        valid_offset_days=-1,
        availability_offset_days=40,
    )
    child = _insert_value(
        lineage_connection,
        definition_id,
        depth=1,
        origin_offset_days=20,
        valid_offset_days=19,
        availability_offset_days=50,
    )
    with pytest.raises(errors.CheckViolation, match="parent_available_at_child_origin"):
        _insert_edge(lineage_connection, child, parent)


def test_a_parent_cutoff_not_strictly_earlier_is_rejected(
    lineage_connection: psycopg2.extensions.connection,
) -> None:
    definition_id = _insert_definition(lineage_connection)
    parent = _insert_value(
        lineage_connection,
        definition_id,
        depth=0,
        origin_offset_days=20,
        valid_offset_days=19,
        availability_offset_days=20,
        series_key=f"{SERIES_PREFIX}|series-a",
    )
    child = _insert_value(
        lineage_connection,
        definition_id,
        depth=1,
        origin_offset_days=20,
        valid_offset_days=19,
        availability_offset_days=20,
        series_key=f"{SERIES_PREFIX}|series-b",
    )
    with pytest.raises(errors.CheckViolation, match="parent_cutoff_earlier"):
        _insert_edge(lineage_connection, child, parent)


def test_a_parent_valid_time_after_the_child_cutoff_is_rejected(
    lineage_connection: psycopg2.extensions.connection,
) -> None:
    definition_id = _insert_definition(lineage_connection)
    parent = _insert_value(
        lineage_connection,
        definition_id,
        depth=0,
        origin_offset_days=0,
        valid_offset_days=30,
        availability_offset_days=0,
        series_key=f"{SERIES_PREFIX}|series-a",
    )
    child = _insert_value(
        lineage_connection,
        definition_id,
        depth=1,
        origin_offset_days=20,
        valid_offset_days=19,
        availability_offset_days=20,
        series_key=f"{SERIES_PREFIX}|series-b",
    )
    with pytest.raises(errors.CheckViolation, match="parent_valid_time_earlier"):
        _insert_edge(lineage_connection, child, parent)


def test_a_child_cannot_be_available_before_its_parent_by_any_route(
    lineage_connection: psycopg2.extensions.connection,
) -> None:
    """Three routes to a child preceding its parent, three separate refusals.

    The value-level `availability_time >= origin_cutoff_time` closes the first; the edge's
    `child_not_available_before_parent` closes the second; and a parent that becomes available after
    the child's origin but before the child's own availability is caught by
    `parent_available_at_child_origin` -- the case the redundant-looking constraint cannot see.
    """
    definition_id = _insert_definition(lineage_connection)
    with pytest.raises(errors.CheckViolation, match="available_after_origin"):
        _insert_value(
            lineage_connection,
            definition_id,
            depth=1,
            origin_offset_days=20,
            valid_offset_days=19,
            availability_offset_days=10,
            series_key=f"{SERIES_PREFIX}|series-early-child",
        )
    lineage_connection.rollback()

    definition_id = _insert_definition(lineage_connection)
    late_parent = _insert_value(
        lineage_connection,
        definition_id,
        depth=0,
        origin_offset_days=0,
        valid_offset_days=-1,
        availability_offset_days=40,
        series_key=f"{SERIES_PREFIX}|series-late-parent",
    )
    child = _insert_value(
        lineage_connection,
        definition_id,
        depth=1,
        origin_offset_days=20,
        valid_offset_days=19,
        availability_offset_days=20,
        series_key=f"{SERIES_PREFIX}|series-child",
    )
    with pytest.raises(errors.CheckViolation, match="child_not_available_before_parent"):
        _insert_edge(lineage_connection, child, late_parent)
    lineage_connection.rollback()

    definition_id = _insert_definition(lineage_connection)
    parent = _insert_value(
        lineage_connection,
        definition_id,
        depth=0,
        origin_offset_days=0,
        valid_offset_days=-1,
        availability_offset_days=25,
        series_key=f"{SERIES_PREFIX}|series-parent",
    )
    child = _insert_value(
        lineage_connection,
        definition_id,
        depth=1,
        origin_offset_days=20,
        valid_offset_days=19,
        availability_offset_days=30,
        series_key=f"{SERIES_PREFIX}|series-child",
    )
    with pytest.raises(errors.CheckViolation, match="parent_available_at_child_origin"):
        _insert_edge(lineage_connection, child, parent)


def test_a_cycle_is_structurally_impossible(lineage_connection: psycopg2.extensions.connection) -> None:
    """A back-edge cannot satisfy `child_depth = parent_depth + 1` and the strict cutoff order."""
    definition_id = _insert_definition(lineage_connection)
    first = _insert_value(
        lineage_connection,
        definition_id,
        depth=0,
        origin_offset_days=0,
        valid_offset_days=-1,
        availability_offset_days=0,
        series_key=f"{SERIES_PREFIX}|series-a",
    )
    second = _insert_value(
        lineage_connection,
        definition_id,
        depth=1,
        origin_offset_days=10,
        valid_offset_days=9,
        availability_offset_days=10,
        series_key=f"{SERIES_PREFIX}|series-b",
    )
    _insert_edge(lineage_connection, second, first)
    with pytest.raises(errors.CheckViolation):
        _insert_edge(lineage_connection, first, second)


def test_a_self_referencing_edge_is_rejected(lineage_connection: psycopg2.extensions.connection) -> None:
    definition_id = _insert_definition(lineage_connection)
    value_id = _insert_value(
        lineage_connection,
        definition_id,
        depth=1,
        origin_offset_days=10,
        valid_offset_days=9,
        availability_offset_days=10,
    )
    with pytest.raises(errors.CheckViolation):
        _insert_edge(lineage_connection, value_id, value_id)


def test_an_edge_that_skips_a_depth_step_is_rejected(
    lineage_connection: psycopg2.extensions.connection,
) -> None:
    definition_id = _insert_definition(lineage_connection)
    parent = _insert_value(
        lineage_connection,
        definition_id,
        depth=0,
        origin_offset_days=0,
        valid_offset_days=-1,
        availability_offset_days=0,
        series_key=f"{SERIES_PREFIX}|series-a",
    )
    child = _insert_value(
        lineage_connection,
        definition_id,
        depth=3,
        origin_offset_days=20,
        valid_offset_days=19,
        availability_offset_days=20,
        series_key=f"{SERIES_PREFIX}|series-b",
    )
    with pytest.raises(errors.CheckViolation, match="depth_step"):
        _insert_edge(lineage_connection, child, parent)


def test_a_value_deeper_than_its_declared_bound_is_rejected(
    lineage_connection: psycopg2.extensions.connection,
) -> None:
    definition_id = _insert_definition(lineage_connection, max_dependency_depth=2)
    with pytest.raises(errors.CheckViolation, match="depth_bound"):
        _insert_value(
            lineage_connection,
            definition_id,
            depth=3,
            origin_offset_days=30,
            valid_offset_days=29,
            availability_offset_days=30,
            max_dependency_depth=2,
        )


def test_a_value_claiming_a_bound_its_definition_does_not_declare_is_rejected(
    lineage_connection: psycopg2.extensions.connection,
) -> None:
    """The denormalized bound is composite-foreign-keyed, so it cannot disagree with the definition."""
    definition_id = _insert_definition(lineage_connection, max_dependency_depth=2)
    with pytest.raises(errors.ForeignKeyViolation, match="fk_forecast_derived_signal_value_definition"):
        _insert_value(
            lineage_connection,
            definition_id,
            depth=3,
            origin_offset_days=30,
            valid_offset_days=29,
            availability_offset_days=30,
            max_dependency_depth=8,
        )


def test_a_value_whose_checksum_does_not_reproduce_is_rejected(
    lineage_connection: psycopg2.extensions.connection,
) -> None:
    definition_id = _insert_definition(lineage_connection)
    with pytest.raises(errors.CheckViolation, match="reproducible_checksum"):
        _insert_value(
            lineage_connection,
            definition_id,
            depth=0,
            origin_offset_days=0,
            valid_offset_days=-1,
            availability_offset_days=0,
            checksum_override="c" * 64,
        )


def test_availability_before_the_origin_cutoff_is_rejected(
    lineage_connection: psycopg2.extensions.connection,
) -> None:
    definition_id = _insert_definition(lineage_connection)
    with pytest.raises(errors.CheckViolation, match="available_after_origin"):
        _insert_value(
            lineage_connection,
            definition_id,
            depth=0,
            origin_offset_days=20,
            valid_offset_days=19,
            availability_offset_days=10,
        )


def test_a_derived_signal_value_is_immutable(lineage_connection: psycopg2.extensions.connection) -> None:
    definition_id = _insert_definition(lineage_connection)
    value_id = _insert_value(
        lineage_connection,
        definition_id,
        depth=0,
        origin_offset_days=0,
        valid_offset_days=-1,
        availability_offset_days=0,
    )
    with lineage_connection.cursor() as cursor, pytest.raises(psycopg2.errors.RaiseException):
        cursor.execute("UPDATE agri.forecast_derived_signal_value SET signal_value = 99.0 WHERE id = %s", (value_id,))
    lineage_connection.rollback()
    definition_id = _insert_definition(lineage_connection)
    value_id = _insert_value(
        lineage_connection,
        definition_id,
        depth=0,
        origin_offset_days=0,
        valid_offset_days=-1,
        availability_offset_days=0,
    )
    with lineage_connection.cursor() as cursor, pytest.raises(psycopg2.errors.RaiseException):
        cursor.execute("DELETE FROM agri.forecast_derived_signal_value WHERE id = %s", (value_id,))


def test_a_signal_definition_may_not_claim_publication_authority(
    lineage_connection: psycopg2.extensions.connection,
) -> None:
    with lineage_connection.cursor() as cursor, pytest.raises(errors.CheckViolation, match="never_published"):
        cursor.execute(
            """
            INSERT INTO agri.forecast_signal_definition (
                signal_key, signal_version, unit, spatial_support_key, temporal_grain,
                recipe_key, recipe_checksum, max_dependency_depth, definition_checksum,
                publication_authorized
            ) VALUES ('published_attempt', 'v1', 'm/s', 'surface', interval '1 day',
                      'recipe', %s, 4, %s, true)
            """,
            (DIGEST_A, DIGEST_B),
        )


def test_no_new_relation_references_a_publication_or_receipt_relation(
    lineage_connection: psycopg2.extensions.connection,
) -> None:
    """The evaluation-only guarantee is the absence of a foreign key, not a WHERE clause."""
    with lineage_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT child.relname, parent.relname, constraint_row.conname
            FROM pg_constraint AS constraint_row
            JOIN pg_class AS child ON child.oid = constraint_row.conrelid
            JOIN pg_class AS parent ON parent.oid = constraint_row.confrelid
            WHERE constraint_row.contype = 'f'
              AND child.relname = ANY(%s)
              AND parent.relname = ANY(%s)
            """,
            (list(_NEW_RELATIONS), list(_PUBLICATION_RELATIONS)),
        )
        offenders = cursor.fetchall()
    assert offenders == []


def test_no_view_or_matview_depends_on_the_new_relations(
    lineage_connection: psycopg2.extensions.connection,
) -> None:
    with lineage_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT dependent.relname
            FROM pg_depend AS depend
            JOIN pg_rewrite AS rewrite ON rewrite.oid = depend.objid
            JOIN pg_class AS dependent ON dependent.oid = rewrite.ev_class
            JOIN pg_class AS source ON source.oid = depend.refobjid
            WHERE source.relname = ANY(%s)
              AND dependent.relkind IN ('v', 'm')
              AND dependent.relname <> source.relname
            """,
            (list(_NEW_RELATIONS),),
        )
        dependents = cursor.fetchall()
    assert dependents == []


def test_the_lineage_audit_reports_real_depth_and_no_cycle(
    lineage_connection: psycopg2.extensions.connection,
) -> None:
    definition_id = _insert_definition(lineage_connection)
    ids = [
        _insert_value(
            lineage_connection,
            definition_id,
            depth=depth,
            origin_offset_days=depth * 10,
            valid_offset_days=depth * 10 - 1,
            availability_offset_days=depth * 10,
        )
        for depth in range(4)
    ]
    for depth in range(1, 4):
        _insert_edge(lineage_connection, ids[depth], ids[depth - 1])
    with lineage_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT ancestor_count, traversed_depth, declared_max_depth, depth_bound_exceeded,
                   cycle_detected, availability_violation_count
            FROM agri.forecast_signal_lineage_audit(%s)
            """,
            (ids[3],),
        )
        row = cursor.fetchone()
    assert row is not None
    ancestor_count, traversed_depth, declared_max_depth, bound_exceeded, cycle, violations = row
    assert ancestor_count == 3  # noqa: PLR2004
    assert traversed_depth == 3  # noqa: PLR2004
    assert declared_max_depth == 8  # noqa: PLR2004
    assert bound_exceeded is False
    assert cycle is False
    assert violations == 0


def _validated_snapshot(connection: psycopg2.extensions.connection, window_end: datetime) -> str:
    suffix = uuid4().hex[:12]
    manifest_checksum = f"{suffix}{'0' * (64 - len(suffix))}"
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO agri.release_set (logical_key, as_of_time, manifest_checksum, state, validated_at)
            VALUES (%s, %s, %s, 'validated', %s) RETURNING id
            """,
            (f"seasonal-lineage-{suffix}", BASE_TIME, manifest_checksum, BASE_TIME),
        )
        release_row = cursor.fetchone()
        assert release_row is not None
        cursor.execute(
            """
            INSERT INTO agri.job_definition (name, version, handler, queue_name)
            VALUES (%s, 'v1', 'execution.seasonal_lineage_contract_test', 'evaluation') RETURNING id
            """,
            (f"seasonal-lineage-{suffix}",),
        )
        definition_row = cursor.fetchone()
        assert definition_row is not None
        cursor.execute(
            """
            INSERT INTO agri.job_run (
                job_definition_id, release_set_id, logical_run_key, scheduled_for,
                status, started_at, completed_at
            ) VALUES (%s, %s, %s, %s, 'succeeded', %s, %s) RETURNING id
            """,
            (
                definition_row[0],
                release_row[0],
                f"seasonal-lineage-{suffix}",
                BASE_TIME,
                BASE_TIME,
                BASE_TIME,
            ),
        )
        run_row = cursor.fetchone()
        assert run_row is not None
        # `require_initial_forecast_state` insists a snapshot enters as `draft`; the promotion to
        # `validated` is a separate write, which is exactly the shape the CLI performs post-0018.
        cursor.execute(
            """
            INSERT INTO agri.forecast_feature_snapshot (
                snapshot_key, job_run_id, release_set_id, input_release_checksum,
                feature_recipe_version, feature_code_checksum, feature_checksum,
                training_window_start, training_window_end, row_count, status
            ) VALUES (%s, %s, %s, %s, 'seasonal-v1', %s, %s, %s, %s, 1, 'draft')
            RETURNING id
            """,
            (
                f"seasonal-lineage-{suffix}",
                run_row[0],
                release_row[0],
                manifest_checksum,
                DIGEST_A,
                DIGEST_B,
                BASE_TIME,
                window_end,
            ),
        )
        snapshot_row = cursor.fetchone()
        assert snapshot_row is not None
        cursor.execute(
            "UPDATE agri.forecast_feature_snapshot SET status = 'validated', validated_at = %s WHERE id = %s",
            (BASE_TIME, snapshot_row[0]),
        )
        return str(snapshot_row[0])


def test_the_snapshot_gate_admits_only_values_available_by_the_snapshot_window_end(
    lineage_connection: psycopg2.extensions.connection,
) -> None:
    definition_id = _insert_definition(lineage_connection)
    early = _insert_value(
        lineage_connection,
        definition_id,
        depth=0,
        origin_offset_days=0,
        valid_offset_days=-1,
        availability_offset_days=5,
        series_key=f"{SERIES_PREFIX}|series-early",
    )
    late = _insert_value(
        lineage_connection,
        definition_id,
        depth=0,
        origin_offset_days=0,
        valid_offset_days=-1,
        availability_offset_days=50,
        series_key=f"{SERIES_PREFIX}|series-late",
    )
    snapshot_id = _validated_snapshot(lineage_connection, BASE_TIME + timedelta(days=10))
    with lineage_connection.cursor() as cursor:
        cursor.execute(
            "SELECT derived_value_id FROM agri.forecast_derived_signal_snapshot_eligible(%s) ORDER BY 1",
            (snapshot_id,),
        )
        eligible = [row[0] for row in cursor.fetchall()]
    assert early in eligible
    assert late not in eligible


def test_the_snapshot_gate_admits_nothing_for_a_draft_snapshot(
    lineage_connection: psycopg2.extensions.connection,
) -> None:
    definition_id = _insert_definition(lineage_connection)
    _insert_value(
        lineage_connection,
        definition_id,
        depth=0,
        origin_offset_days=0,
        valid_offset_days=-1,
        availability_offset_days=1,
    )
    snapshot_id = _validated_snapshot(lineage_connection, BASE_TIME + timedelta(days=10))
    with lineage_connection.cursor() as cursor:
        cursor.execute(
            "UPDATE agri.forecast_feature_snapshot SET status = 'draft', validated_at = NULL WHERE id = %s",
            (snapshot_id,),
        )
        cursor.execute("SELECT count(*) FROM agri.forecast_derived_signal_snapshot_eligible(%s)", (snapshot_id,))
        row = cursor.fetchone()
    assert row is not None
    assert row[0] == 0


def test_the_snapshot_gate_refuses_a_descendant_whose_ancestor_is_not_yet_available(
    lineage_connection: psycopg2.extensions.connection,
) -> None:
    definition_id = _insert_definition(lineage_connection)
    parent = _insert_value(
        lineage_connection,
        definition_id,
        depth=0,
        origin_offset_days=0,
        valid_offset_days=-1,
        availability_offset_days=5,
        series_key=f"{SERIES_PREFIX}|series-parent",
    )
    child = _insert_value(
        lineage_connection,
        definition_id,
        depth=1,
        origin_offset_days=20,
        valid_offset_days=19,
        availability_offset_days=40,
        series_key=f"{SERIES_PREFIX}|series-child",
    )
    _insert_edge(lineage_connection, child, parent)
    snapshot_id = _validated_snapshot(lineage_connection, BASE_TIME + timedelta(days=30))
    with lineage_connection.cursor() as cursor:
        cursor.execute(
            "SELECT eligible.derived_value_id "
            "FROM agri.forecast_derived_signal_snapshot_eligible(%s) AS eligible "
            "JOIN agri.forecast_derived_signal_value AS value "
            "ON value.id = eligible.derived_value_id "
            "WHERE value.series_key LIKE %s ORDER BY 1",
            (snapshot_id, f"{SERIES_PREFIX}|%"),
        )
        eligible = [row[0] for row in cursor.fetchall()]
    assert eligible == [parent]
    assert child not in eligible


def test_a_candidate_evaluation_receipt_checksum_must_reproduce(
    lineage_connection: psycopg2.extensions.connection,
) -> None:
    with lineage_connection.cursor() as cursor, pytest.raises(errors.CheckViolation, match="reproducible_receipt"):
        cursor.execute(
            """
            INSERT INTO agri.forecast_candidate_evaluation (
                evaluation_key, series_key, candidate_family, candidate_version, simulation_seed,
                export_manifest_checksum, horizon_steps, development_origin_count,
                final_holdout_origin_count, decision, decision_reason, receipt_checksum
            ) VALUES ('eval-bad', 'series-a', 'persistence', 'v1', 20260814, %s, 30, 16, 11,
                      'reject', 'wrong digest', %s)
            """,
            (DIGEST_A, "d" * 64),
        )


def test_a_candidate_evaluation_with_a_reproduced_receipt_is_accepted(
    lineage_connection: psycopg2.extensions.connection,
) -> None:
    with lineage_connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO agri.forecast_candidate_evaluation (
                evaluation_key, series_key, candidate_family, candidate_version, simulation_seed,
                export_manifest_checksum, horizon_steps, development_origin_count,
                final_holdout_origin_count, decision, decision_reason, receipt_checksum
            ) VALUES (
                'eval-good', 'series-a', 'persistence', 'v1', 20260814, %(manifest)s, 30, 16, 11,
                'baseline', 'skill denominator', agri.forecast_candidate_evaluation_receipt_checksum(
                    'eval-good', 'series-a', 'persistence', 'v1', '{}'::jsonb, 20260814,
                    %(manifest)s, 30, 16, 11, 'baseline'
                )
            ) RETURNING id
            """,
            {"manifest": DIGEST_A},
        )
        row = cursor.fetchone()
    assert row is not None
