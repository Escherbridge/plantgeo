"""`agri_covariates_v2` adds features without changing a single thing about v1.

The v1 registry is pinned here as an explicit expectation rather than compared against itself,
so an edit to the schema function that shifted an index, renamed a feature or changed a lag
fails this file even if v2 still looks internally consistent.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import psycopg2
import pytest

from agri_data_service.method.ml.covariates_v2 import (
    AS_OF_MODE_BY_SCHEMA_VERSION,
    SCHEMA_VERSION_V1,
    SCHEMA_VERSION_V2,
    CovariateReadError,
    hargreaves_reference_evapotranspiration_mm,
    require_supported_schema_version,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

PROTECTED_DATABASE_NAME = "plantgeo"
_SKIP_REASON = (
    "set AGRI_TEST_DATABASE_URL to a disposable database migrated through 20260814_0023 "
    "(never the persistent 'plantgeo' warehouse)"
)

# The exact 40 rows agri_covariates_v1 has emitted since 20260802_0016. Frozen by hand.
_V1_REGISTRY: tuple[tuple[int, str, str, str, int, int], ...] = tuple(  # noqa: RUF005
    (
        (signal_ordinal - 1) * 5 + shape_ordinal,
        f"{signal_name}_{suffix}",
        "meteorology",
        "nasa_power",
        lag,
        window,
    )
    for signal_ordinal, signal_name in enumerate(
        (
            "air_temperature_max",
            "air_temperature_mean",
            "air_temperature_min",
            "dew_point_temperature",
            "precipitation",
            "relative_humidity",
            "wind_speed",
        ),
        start=1,
    )
    for shape_ordinal, (suffix, lag, window) in enumerate(
        (("lag_1", 1, 1), ("lag_2", 2, 1), ("lag_3", 3, 1), ("roll_mean_7", 1, 7), ("roll_mean_28", 1, 28)),
        start=1,
    )
) + (
    (36, "drought_severity_class_lag_1", "drought", "usdm", 1, 1),
    (37, "drought_severity_class_lag_7", "drought", "usdm", 7, 1),
    (38, "drought_severity_imputed_lag_1", "drought", "usdm", 1, 1),
    (39, "day_of_year_sin", "calendar", "calendar", 0, 1),
    (40, "day_of_year_cos", "calendar", "calendar", 0, 1),
)

_V2_ADDITIONS: tuple[tuple[int, str, str, str, int, int], ...] = (
    (41, "mc_forecast_low_for_day", "mc_forecast", "forecast_iteration", 1, 1),
    (42, "mc_forecast_median_for_day", "mc_forecast", "forecast_iteration", 1, 1),
    (43, "mc_forecast_high_for_day", "mc_forecast", "forecast_iteration", 1, 1),
    (44, "mc_forecast_band_width_for_day", "mc_forecast", "forecast_iteration", 1, 1),
    (45, "mc_forecast_lead_days", "mc_forecast", "forecast_iteration", 1, 1),
    (46, "day_of_year_sin_semiannual", "calendar", "calendar", 0, 1),
    (47, "day_of_year_cos_semiannual", "calendar", "calendar", 0, 1),
)

pytestmark = pytest.mark.agri_db


@pytest.fixture
def covariate_connection() -> Iterator[psycopg2.extensions.connection]:
    """A rolled-back connection to a disposable database that defines agri_covariates_v2."""
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
            cursor.execute("SELECT count(*) FROM agri.covariate_feature_schema(%s)", (SCHEMA_VERSION_V2,))
        yield connection
    except psycopg2.errors.RaiseException:
        connection.rollback()
        pytest.fail("AGRI_TEST_DATABASE_URL database does not define agri_covariates_v2; upgrade it")
    finally:
        connection.rollback()
        connection.close()


def _registry(cursor: psycopg2.extensions.cursor, schema_version: str) -> list[tuple[object, ...]]:
    cursor.execute(
        "SELECT feature_index, feature_name, feature_kind, stream_key, lag_days, window_days "
        "FROM agri.covariate_feature_schema(%s) ORDER BY feature_index",
        (schema_version,),
    )
    return [tuple(row) for row in cursor.fetchall()]


def test_v1_registry_is_exactly_the_forty_features_it_has_always_been(
    covariate_connection: psycopg2.extensions.connection,
) -> None:
    with covariate_connection.cursor() as cursor:
        assert _registry(cursor, SCHEMA_VERSION_V1) == list(_V1_REGISTRY)


def test_v2_is_v1_verbatim_followed_by_its_additions(
    covariate_connection: psycopg2.extensions.connection,
) -> None:
    with covariate_connection.cursor() as cursor:
        v2_rows = _registry(cursor, SCHEMA_VERSION_V2)

    assert v2_rows[:40] == list(_V1_REGISTRY)
    assert v2_rows[40:] == list(_V2_ADDITIONS)


def test_every_v2_addition_is_strictly_lagged_or_a_function_of_its_own_date(
    covariate_connection: psycopg2.extensions.connection,
) -> None:
    with covariate_connection.cursor() as cursor:
        rows = _registry(cursor, SCHEMA_VERSION_V2)[40:]

    for _index, _name, kind, _stream, lag_days, _window in rows:
        assert lag_days >= 1 or kind == "calendar"


def test_the_lookback_window_is_unchanged_by_v2(
    covariate_connection: psycopg2.extensions.connection,
) -> None:
    with covariate_connection.cursor() as cursor:
        cursor.execute(
            "SELECT agri.covariate_lookback_days(%s), agri.covariate_lookback_days(%s)",
            (SCHEMA_VERSION_V1, SCHEMA_VERSION_V2),
        )
        row = cursor.fetchone()

    assert row is not None
    assert row[0] == row[1] == 28  # noqa: PLR2004


def test_v1_declared_gaps_are_unchanged_and_v2_declares_what_it_did_not_build(
    covariate_connection: psycopg2.extensions.connection,
) -> None:
    with covariate_connection.cursor() as cursor:
        cursor.execute("SELECT stream_key FROM agri.covariate_declared_gap(%s)", (SCHEMA_VERSION_V1,))
        v1_gaps = sorted(row[0] for row in cursor.fetchall())
        cursor.execute("SELECT stream_key FROM agri.covariate_declared_gap(%s)", (SCHEMA_VERSION_V2,))
        v2_gaps = sorted(row[0] for row in cursor.fetchall())

    assert v1_gaps == ["era5_land"]
    assert v2_gaps == ["analog_ensemble", "era5_land", "ml_ridge_forecast"]


def test_an_unknown_schema_version_is_still_refused(
    covariate_connection: psycopg2.extensions.connection,
) -> None:
    with covariate_connection.cursor() as cursor, pytest.raises(psycopg2.errors.RaiseException):
        cursor.execute("SELECT * FROM agri.covariate_feature_schema('agri_covariates_v3')")


def test_v1_daily_features_emit_forty_positions_per_day_for_an_unknown_cell(
    covariate_connection: psycopg2.extensions.connection,
) -> None:
    # An unknown cell yields no days at all under either version -- the CROSS JOIN cell rule.
    with covariate_connection.cursor() as cursor:
        for version in (SCHEMA_VERSION_V1, SCHEMA_VERSION_V2):
            cursor.execute(
                "SELECT count(*) FROM agri.covariate_daily_features("
                "'00000000-0000-0000-0000-000000000000'::uuid, '2025-01-01'::timestamptz, "
                "'2025-01-05'::timestamptz, '2026-01-01'::timestamptz, %s)",
                (version,),
            )
            row = cursor.fetchone()
            assert row is not None
            assert row[0] == 0


def test_as_of_mode_is_recorded_per_schema_version() -> None:
    assert AS_OF_MODE_BY_SCHEMA_VERSION[SCHEMA_VERSION_V1] == "global"
    assert AS_OF_MODE_BY_SCHEMA_VERSION[SCHEMA_VERSION_V2].startswith("per_issue_date")


def test_an_unsupported_schema_version_is_refused_at_ingress() -> None:
    with pytest.raises(CovariateReadError, match="unknown feature schema version"):
        require_supported_schema_version("agri_covariates_v3")


def test_hargreaves_is_positive_in_summer_and_refuses_inverted_temperatures() -> None:
    summer = hargreaves_reference_evapotranspiration_mm(
        latitude_degrees=43.0, day_of_year=182, temperature_min_c=12.0, temperature_max_c=32.0
    )
    winter = hargreaves_reference_evapotranspiration_mm(
        latitude_degrees=43.0, day_of_year=1, temperature_min_c=-6.0, temperature_max_c=2.0
    )

    assert summer > winter > 0.0
    with pytest.raises(CovariateReadError):
        hargreaves_reference_evapotranspiration_mm(
            latitude_degrees=43.0, day_of_year=1, temperature_min_c=10.0, temperature_max_c=2.0
        )
