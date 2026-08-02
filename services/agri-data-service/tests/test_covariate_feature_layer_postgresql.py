"""PostgreSQL contract proof for the 0016 assignment-time covariate/feature layer."""

import hashlib
import os
from datetime import UTC, datetime, timedelta

import psycopg2
import pytest

SCHEMA_VERSION = "agri_covariates_v1"
EXPECTED_FEATURE_COUNT = 40
EXPECTED_METEOROLOGY_SIGNALS = (
    "air_temperature_max",
    "air_temperature_mean",
    "air_temperature_min",
    "dew_point_temperature",
    "precipitation",
    "relative_humidity",
    "wind_speed",
)
EXPECTED_FIRST_FEATURES = (
    "air_temperature_max_lag_1",
    "air_temperature_max_lag_2",
    "air_temperature_max_lag_3",
    "air_temperature_max_roll_mean_7",
    "air_temperature_max_roll_mean_28",
)
EXPECTED_TAIL_FEATURES = (
    "drought_severity_class_lag_1",
    "drought_severity_class_lag_7",
    "drought_severity_imputed_lag_1",
    "day_of_year_sin",
    "day_of_year_cos",
)
FEATURE_COLUMNS = (
    "cell_id",
    "observed_date",
    "feature_index",
    "feature_name",
    "feature_kind",
    "feature_value",
    "input_count",
    "expected_input_count",
    "is_imputed",
    "source_release_count",
    "data_available_at",
)
FEATURE_COLUMN_TYPES = (
    "uuid",
    "date",
    "integer",
    "text",
    "text",
    "double precision",
    "integer",
    "integer",
    "boolean",
    "integer",
    "timestamp with time zone",
)
MANIFEST_COLUMNS = (
    "schema_version",
    "cell_id",
    "window_start",
    "window_end",
    "as_of_time",
    "feature_count",
    "day_count",
    "emitted_row_count",
    "complete_row_count",
    "partial_row_count",
    "imputed_row_count",
    "complete_day_count",
    "feature_names",
    "declared_gaps",
    "source_release_ids",
    "max_data_available_at",
    "manifest_checksum",
)
FORECAST_PLANE_ROLES = (
    "plantgeo_forecast_reader",
    "plantgeo_forecast_writer",
    "plantgeo_forecast_publisher",
    "plantgeo_forecast_mv_refresher",
)
COVARIATE_FUNCTION_SIGNATURES = (
    "agri.covariate_feature_schema(character varying)",
    "agri.covariate_declared_gap(character varying)",
    "agri.covariate_daily_features(uuid,timestamptz,timestamptz,timestamptz,character varying)",
    "agri.covariate_vector_manifest(uuid,timestamptz,timestamptz,timestamptz,character varying)",
)
FORBIDDEN_SERVING_OBJECTS = (
    "v_forecast_series_serving",
    "forecast_publication",
    "forecast_publication_item",
    "forecast_receipt",
)
# Fixture history: 70 daily wind rows so a clean 28-day rolling window exists well
# clear of the deliberately skipped / late / unobserved probe days.
FIXTURE_DAY_COUNT = 70
FIXTURE_WINDOW_FIRST_DAY = 21
FIXTURE_SKIPPED_DAY = 20
FIXTURE_LATE_DAY = 65
FIXTURE_UNOBSERVED_DAY = 66
FIXTURE_CLEAN_ROLL_DAY = 60
LAG_1_INDEX_WIND = 31
ROLL_MEAN_7_INDEX_WIND = 34
ROLL_MEAN_28_INDEX_WIND = 35
ROLL_MEAN_7_WINDOW = 7
ROLL_MEAN_28_WINDOW = 28
ROLL_MEAN_7_SURVIVING_INPUTS = 6
FLOAT_TOLERANCE = 1e-9
DUPLICATE_RELEASE_COUNT = 2


def _checksum(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _seed_cell_and_signals(  # noqa: PLR0913 - keyword-only fixture probe days are the contract
    cursor: psycopg2.extensions.cursor,
    suffix: str,
    *,
    history_start: datetime,
    available_at: datetime,
    late_available_at: datetime,
    late_day_index: int,
    unobserved_day_index: int,
    skipped_day_index: int,
) -> tuple[str, str]:
    """Insert one analysis cell plus a NASA-shaped wind_speed history; return (cell_id, release_id)."""
    cursor.execute(
        """
        INSERT INTO agri.spatial_cell(cell_key, grid_name, resolution_m, geometry, centroid)
        VALUES (
            %s, 'covariate-test-grid', 10000,
            ST_GeomFromText(
                'POLYGON((-116.15 43.55, -116.10 43.55, -116.10 43.60, -116.15 43.60, -116.15 43.55))',
                4326
            ),
            ST_GeomFromText('POINT(-116.125 43.575)', 4326)
        )
        RETURNING id
        """,
        (f"covariate-cell-{suffix}",),
    )
    cell_id = cursor.fetchone()[0]

    cursor.execute(
        """
        INSERT INTO agri.data_source(key, name, owner, purpose, license_name, citation)
        VALUES (%s, 'Covariate fixture', 'PlantGeo test', 'rolled-back covariate validation', 'test-only', 'test-only')
        RETURNING id
        """,
        (f"covariate-{suffix}",),
    )
    data_source_id = cursor.fetchone()[0]

    def _release(label: str, data_available_at: datetime) -> str:
        cursor.execute(
            """
            INSERT INTO agri.source_release(
                data_source_id, source_version, retrieved_at, data_available_at,
                payload_checksum, schema_version, license_snapshot, validation_state, validated_at
            )
            VALUES (%s, %s, %s, %s, %s, 'fixture-v1', 'test-only', 'valid', %s)
            RETURNING id
            """,
            (
                data_source_id,
                f"{label}-{suffix}",
                data_available_at,
                data_available_at,
                _checksum(f"{suffix}:{label}"),
                data_available_at,
            ),
        )
        return cursor.fetchone()[0]

    release_id = _release("baseline", available_at)
    late_release_id = _release("late", late_available_at)

    rows = []
    for day in range(FIXTURE_DAY_COUNT):
        if day == skipped_day_index:
            continue
        observed_at = history_start + timedelta(days=day)
        is_late = day == late_day_index
        rows.append(
            (
                late_release_id if is_late else release_id,
                cell_id,
                "wind_speed",
                "WS2M",
                observed_at,
                late_available_at if is_late else available_at,
                float(day),
                float(day),
                day != unobserved_day_index,
            )
        )
    cursor.executemany(
        """
        INSERT INTO agri.signal_observation(
            source_release_id, cell_id, signal_name, source_parameter, observed_at,
            data_available_at, original_value, normalized_value, is_observed,
            original_unit, normalized_unit
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'm/s', 'm/s')
        """,
        rows,
    )
    return cell_id, release_id


def _feature_rows(
    cursor: psycopg2.extensions.cursor,
    cell_id: str,
    window_start: datetime,
    window_end: datetime,
    as_of_time: datetime,
) -> list[dict[str, object]]:
    cursor.execute(
        "SELECT * FROM agri.covariate_daily_features(%s, %s, %s, %s, %s)",
        (cell_id, window_start, window_end, as_of_time, SCHEMA_VERSION),
    )
    columns = [column.name for column in cursor.description]
    assert columns == list(FEATURE_COLUMNS)
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def test_covariate_feature_schema_pins_an_ordered_contiguous_feature_vector(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    with agri_db_connection.cursor() as cursor:
        cursor.execute(
            "SELECT feature_index, feature_name, feature_kind, stream_key, signal_name, lag_days, window_days "
            "FROM agri.covariate_feature_schema(%s)",
            (SCHEMA_VERSION,),
        )
        rows = cursor.fetchall()

        assert len(rows) == EXPECTED_FEATURE_COUNT
        indexes = [row[0] for row in rows]
        assert indexes == list(range(1, EXPECTED_FEATURE_COUNT + 1)), "feature order must be pinned and contiguous"
        names = [row[1] for row in rows]
        assert len(set(names)) == EXPECTED_FEATURE_COUNT
        assert tuple(names[:5]) == EXPECTED_FIRST_FEATURES
        assert tuple(names[-5:]) == EXPECTED_TAIL_FEATURES

        meteorology_signals = sorted({row[4] for row in rows if row[2] == "meteorology"})
        assert tuple(meteorology_signals) == tuple(sorted(EXPECTED_METEOROLOGY_SIGNALS))
        # every non-calendar feature is strictly lagged: no same-day input can reach a feature row
        assert all(row[5] >= 1 for row in rows if row[2] != "calendar")
        assert all(row[6] >= 1 for row in rows)

        cursor.execute("SAVEPOINT unknown_version")
        with pytest.raises(psycopg2.errors.RaiseException, match="unknown covariate schema version"):
            cursor.execute("SELECT * FROM agri.covariate_feature_schema('agri_covariates_v99')")
        cursor.execute("ROLLBACK TO SAVEPOINT unknown_version")


def test_covariate_declared_gap_names_era5_rather_than_emitting_empty_columns(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    with agri_db_connection.cursor() as cursor:
        cursor.execute(
            "SELECT stream_key, gap_kind, gap_reason FROM agri.covariate_declared_gap(%s)",
            (SCHEMA_VERSION,),
        )
        rows = cursor.fetchall()
        assert [row[0] for row in rows] == ["era5_land"]
        assert rows[0][1] == "credential_gated"
        assert "CDS credentials" in rows[0][2]

        cursor.execute(
            "SELECT feature_name FROM agri.covariate_feature_schema(%s) WHERE feature_name ILIKE %s",
            (SCHEMA_VERSION, "%era5%"),
        )
        assert cursor.fetchall() == [], "a declared gap must never appear as a covariate column"


def test_covariate_daily_features_column_contract_leakage_gate_and_partial_honesty(  # noqa: PLR0915
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    connection = agri_db_connection
    with connection.cursor() as cursor:
        suffix = _checksum(os.urandom(16).hex())[:12]
        history_start = datetime(2026, 1, 1, tzinfo=UTC)
        available_at = datetime(2026, 3, 1, tzinfo=UTC)
        late_available_at = datetime(2026, 3, 20, tzinfo=UTC)
        late_day_index = FIXTURE_LATE_DAY
        unobserved_day_index = FIXTURE_UNOBSERVED_DAY
        skipped_day_index = FIXTURE_SKIPPED_DAY

        cell_id, release_id = _seed_cell_and_signals(
            cursor,
            suffix,
            history_start=history_start,
            available_at=available_at,
            late_available_at=late_available_at,
            late_day_index=late_day_index,
            unobserved_day_index=unobserved_day_index,
            skipped_day_index=skipped_day_index,
        )

        window_start = history_start + timedelta(days=FIXTURE_WINDOW_FIRST_DAY)
        window_end = history_start + timedelta(days=FIXTURE_DAY_COUNT - 1)
        as_of_full = datetime(2026, 4, 1, tzinfo=UTC)
        as_of_before_late = datetime(2026, 3, 10, tzinfo=UTC)

        cursor.execute(
            """
            SELECT
                pg_typeof(cell_id)::text, pg_typeof(observed_date)::text, pg_typeof(feature_index)::text,
                pg_typeof(feature_name)::text, pg_typeof(feature_kind)::text, pg_typeof(feature_value)::text,
                pg_typeof(input_count)::text, pg_typeof(expected_input_count)::text, pg_typeof(is_imputed)::text,
                pg_typeof(source_release_count)::text, pg_typeof(data_available_at)::text
            FROM agri.covariate_daily_features(%s, %s, %s, %s, %s)
            LIMIT 1
            """,
            (cell_id, window_start, window_end, as_of_full, SCHEMA_VERSION),
        )
        assert cursor.fetchone() == FEATURE_COLUMN_TYPES

        full_rows = _feature_rows(cursor, cell_id, window_start, window_end, as_of_full)
        day_count = (window_end - window_start).days + 1
        assert len(full_rows) == day_count * EXPECTED_FEATURE_COUNT

        # stable ordering house rule: (observed_date, feature_index), and repeatable
        ordering = [(row["observed_date"], row["feature_index"]) for row in full_rows]
        assert ordering == sorted(ordering)
        assert [(row["observed_date"], row["feature_index"], row["feature_value"]) for row in full_rows] == [
            (row["observed_date"], row["feature_index"], row["feature_value"])
            for row in _feature_rows(cursor, cell_id, window_start, window_end, as_of_full)
        ]

        indexed = {(row["observed_date"], row["feature_index"]): row for row in full_rows}

        # LEAKAGE: the lag-1 feature of the day after the late observation must be invisible
        # before that observation's server-recorded data_available_at, and present after.
        late_date = (history_start + timedelta(days=late_day_index)).date()
        lag_1_key = (late_date + timedelta(days=1), LAG_1_INDEX_WIND)
        visible = indexed[lag_1_key]
        assert visible["feature_value"] == float(late_day_index)
        assert visible["input_count"] == 1
        assert visible["data_available_at"] == late_available_at
        assert visible["source_release_count"] == 1

        gated_rows = {
            (row["observed_date"], row["feature_index"]): row
            for row in _feature_rows(cursor, cell_id, window_start, window_end, as_of_before_late)
        }
        hidden = gated_rows[lag_1_key]
        assert hidden["feature_value"] is None, "a feature must not appear before its data_available_at"
        assert hidden["input_count"] == 0
        assert hidden["expected_input_count"] == 1
        assert hidden["data_available_at"] is None

        # every emitted provenance timestamp respects the availability gate
        assert all(
            row["data_available_at"] is None or row["data_available_at"] <= as_of_before_late
            for row in gated_rows.values()
        )

        # PARTIAL STAYS PARTIAL: a rolling window missing one input yields NULL, never a
        # mean over the survivors.
        skipped_date = (history_start + timedelta(days=skipped_day_index)).date()
        partial = indexed[(skipped_date + timedelta(days=1), ROLL_MEAN_7_INDEX_WIND)]
        assert partial["feature_value"] is None
        assert partial["input_count"] == ROLL_MEAN_7_SURVIVING_INPUTS
        assert partial["expected_input_count"] == ROLL_MEAN_7_WINDOW

        # IMPUTATION HONESTY: a non-observed input flags the feature rather than silently
        # contributing to a clean-looking average.
        unobserved_date = (history_start + timedelta(days=unobserved_day_index)).date()
        flagged = indexed[(unobserved_date + timedelta(days=1), LAG_1_INDEX_WIND)]
        assert flagged["is_imputed"] is True
        assert flagged["feature_value"] == float(unobserved_day_index)

        # a fully covered 28-day window over the same cell stays complete and un-flagged
        clean_date = (history_start + timedelta(days=FIXTURE_CLEAN_ROLL_DAY)).date()
        clean = indexed[(clean_date, ROLL_MEAN_28_INDEX_WIND)]
        assert clean["expected_input_count"] == ROLL_MEAN_28_WINDOW
        assert clean["input_count"] == ROLL_MEAN_28_WINDOW
        assert clean["is_imputed"] is False
        assert clean["feature_value"] is not None

        # drought is unresolvable for this fixture cell: NULL severity, never a zero class
        drought_rows = [row for row in full_rows if row["feature_kind"] == "drought"]
        assert drought_rows
        assert all(
            row["feature_value"] is None
            for row in drought_rows
            if row["feature_name"].startswith("drought_severity_class")
        )
        assert all(
            row["feature_value"] == 1.0
            for row in drought_rows
            if row["feature_name"] == "drought_severity_imputed_lag_1"
        )

        # calendar features are deterministic and carry no source lineage
        calendar_rows = [row for row in full_rows if row["feature_kind"] == "calendar"]
        assert len(calendar_rows) == day_count * 2
        assert all(row["source_release_count"] == 0 for row in calendar_rows)
        assert all(row["data_available_at"] is None for row in calendar_rows)
        assert all(row["feature_value"] is not None for row in calendar_rows)

        manifest = _manifest(cursor, cell_id, window_start, window_end, as_of_full)
        assert manifest["schema_version"] == SCHEMA_VERSION
        assert manifest["feature_count"] == EXPECTED_FEATURE_COUNT
        assert manifest["day_count"] == day_count
        assert manifest["emitted_row_count"] == day_count * EXPECTED_FEATURE_COUNT
        assert manifest["partial_row_count"] > 0
        assert manifest["complete_row_count"] + manifest["partial_row_count"] == manifest["emitted_row_count"]
        assert manifest["source_release_ids"] is not None
        assert release_id in manifest["source_release_ids"]
        assert manifest["max_data_available_at"] == late_available_at
        assert [gap["stream_key"] for gap in manifest["declared_gaps"]] == ["era5_land"]

        # the manifest checksum is a deterministic function of the gated window
        repeat = _manifest(cursor, cell_id, window_start, window_end, as_of_full)
        assert repeat["manifest_checksum"] == manifest["manifest_checksum"]
        shifted = _manifest(cursor, cell_id, window_start, window_end, as_of_before_late)
        assert shifted["manifest_checksum"] != manifest["manifest_checksum"]


def _manifest(
    cursor: psycopg2.extensions.cursor,
    cell_id: str,
    window_start: datetime,
    window_end: datetime,
    as_of_time: datetime,
) -> dict[str, object]:
    cursor.execute(
        "SELECT * FROM agri.covariate_vector_manifest(%s, %s, %s, %s, %s)",
        (cell_id, window_start, window_end, as_of_time, SCHEMA_VERSION),
    )
    columns = [column.name for column in cursor.description]
    assert columns == list(MANIFEST_COLUMNS)
    row = cursor.fetchone()
    assert row is not None
    return dict(zip(columns, row, strict=True))


def test_covariate_layer_is_evaluation_only_and_least_privilege(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    with agri_db_connection.cursor() as cursor:
        for signature in COVARIATE_FUNCTION_SIGNATURES:
            cursor.execute("SELECT to_regprocedure(%s)::oid", (signature,))
            (function_oid,) = cursor.fetchone()
            assert function_oid is not None, f"{signature} is missing"

            cursor.execute("SELECT has_function_privilege('public', %s, 'EXECUTE')", (function_oid,))
            assert cursor.fetchone()[0] is False, f"{signature} must be revoked from PUBLIC"

            for role in FORECAST_PLANE_ROLES:
                cursor.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, function_oid))
                assert cursor.fetchone()[0] is False, f"{signature} must not be granted to {role}"

            cursor.execute("SELECT pg_get_functiondef(%s)", (function_oid,))
            (definition,) = cursor.fetchone()
            for forbidden in FORBIDDEN_SERVING_OBJECTS:
                assert forbidden not in definition, f"{signature} must not touch {forbidden}"


def test_covariate_daily_features_returns_no_rows_for_an_unknown_cell(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """An unknown cell must yield an honest empty result, never a fabricated all-imputed series."""
    with agri_db_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*)
            FROM agri.covariate_daily_features(gen_random_uuid(), %s, %s, %s, %s)
            """,
            (
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 10, tzinfo=UTC),
                datetime(2026, 2, 1, tzinfo=UTC),
                SCHEMA_VERSION,
            ),
        )
        assert cursor.fetchone()[0] == 0


def test_covariate_daily_features_counts_distinct_days_not_duplicate_release_rows(
    agri_db_connection: psycopg2.extensions.connection,
) -> None:
    """A re-ingested day must not double-count into a rolling window, nor be reported complete."""
    connection = agri_db_connection
    with connection.cursor() as cursor:
        suffix = _checksum(os.urandom(16).hex())[:12]
        history_start = datetime(2026, 1, 1, tzinfo=UTC)
        first_available_at = datetime(2026, 3, 1, tzinfo=UTC)
        revised_available_at = datetime(2026, 3, 5, tzinfo=UTC)

        cursor.execute(
            """
            INSERT INTO agri.spatial_cell(cell_key, grid_name, resolution_m, geometry, centroid)
            VALUES (
                %s, 'covariate-test-grid', 10000,
                ST_GeomFromText(
                    'POLYGON((-116.15 43.55, -116.10 43.55, -116.10 43.60, -116.15 43.60, -116.15 43.55))',
                    4326
                ),
                ST_GeomFromText('POINT(-116.125 43.575)', 4326)
            )
            RETURNING id
            """,
            (f"covariate-dupe-cell-{suffix}",),
        )
        cell_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO agri.data_source(key, name, owner, purpose, license_name, citation)
            VALUES (%s, 'Covariate dupe fixture', 'PlantGeo test', 'rolled-back dedupe validation',
                    'test-only', 'test-only')
            RETURNING id
            """,
            (f"covariate-dupe-{suffix}",),
        )
        data_source_id = cursor.fetchone()[0]

        def _release(label: str, data_available_at: datetime) -> str:
            cursor.execute(
                """
                INSERT INTO agri.source_release(
                    data_source_id, source_version, retrieved_at, data_available_at,
                    payload_checksum, schema_version, license_snapshot, validation_state, validated_at
                )
                VALUES (%s, %s, %s, %s, %s, 'fixture-v1', 'test-only', 'valid', %s)
                RETURNING id
                """,
                (
                    data_source_id,
                    f"{label}-{suffix}",
                    data_available_at,
                    data_available_at,
                    _checksum(f"{suffix}:{label}"),
                    data_available_at,
                ),
            )
            return cursor.fetchone()[0]

        first_release_id = _release("first", first_available_at)
        revised_release_id = _release("revised", revised_available_at)

        def _observe(release_id: str, day: int, value: float, available_at: datetime) -> None:
            cursor.execute(
                """
                INSERT INTO agri.signal_observation(
                    source_release_id, cell_id, signal_name, source_parameter, observed_at,
                    data_available_at, original_value, normalized_value, is_observed,
                    original_unit, normalized_unit
                )
                VALUES (%s, %s, 'wind_speed', 'WS2M', %s, %s, %s, %s, true, 'm/s', 'm/s')
                """,
                (release_id, cell_id, history_start + timedelta(days=day), available_at, value, value),
            )

        # a clean 7-day lookback for probe day 7, with day 3 ingested twice: once by the
        # original release and once by a later revision carrying a different value
        for day in range(7):
            _observe(first_release_id, day, float(day), first_available_at)
        _observe(revised_release_id, 3, 100.0, revised_available_at)

        probe_date = (history_start + timedelta(days=7)).date()
        window = (probe_date, ROLL_MEAN_7_INDEX_WIND)
        rows = {
            (row["observed_date"], row["feature_index"]): row
            for row in _feature_rows(
                cursor,
                cell_id,
                history_start + timedelta(days=7),
                history_start + timedelta(days=7),
                datetime(2026, 4, 1, tzinfo=UTC),
            )
        }
        rolling = rows[window]

        # 7 distinct calendar days contribute, not 8 rows
        assert rolling["input_count"] == ROLL_MEAN_7_WINDOW
        assert rolling["expected_input_count"] == ROLL_MEAN_7_WINDOW
        # the later-available revision wins day 3, so the mean is over (0,1,2,100,4,5,6)
        expected_mean = (0.0 + 1.0 + 2.0 + 100.0 + 4.0 + 5.0 + 6.0) / ROLL_MEAN_7_WINDOW
        assert rolling["feature_value"] is not None
        assert abs(float(rolling["feature_value"]) - expected_mean) < FLOAT_TOLERANCE
        assert rolling["source_release_count"] == DUPLICATE_RELEASE_COUNT

        # before the revision is available the original day-3 value is the winner
        gated = {
            (row["observed_date"], row["feature_index"]): row
            for row in _feature_rows(
                cursor,
                cell_id,
                history_start + timedelta(days=7),
                history_start + timedelta(days=7),
                datetime(2026, 3, 3, tzinfo=UTC),
            )
        }
        original = gated[window]
        assert original["input_count"] == ROLL_MEAN_7_WINDOW
        assert abs(float(original["feature_value"]) - (0.0 + 1.0 + 2.0 + 3.0 + 4.0 + 5.0 + 6.0) / 7) < FLOAT_TOLERANCE
        assert original["source_release_count"] == 1
        assert revised_release_id != first_release_id
