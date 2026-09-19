"""The database-free half of the sibling's `test_covariates_v2_schema.py`, re-homed with the module.

That file's other cases drove `agri.covariate_feature_schema()` through psycopg2 and died with the
Postgres lane (decision D5). These three assert the pure Python the ML service actually owns: the
as-of regime each schema version records, the ingress refusal, and Hargreaves-Samani reference ET.
"""

from __future__ import annotations

import pytest

from plantgeo_ml_service.method.ml.covariates_v2 import (
    AS_OF_MODE_BY_SCHEMA_VERSION,
    SCHEMA_VERSION_V1,
    SCHEMA_VERSION_V2,
    CovariateReadError,
    hargreaves_reference_evapotranspiration_mm,
    require_supported_schema_version,
)

#: Boise, the pilot cell's latitude; the summer/winter contrast below is a northern-hemisphere one.
PILOT_LATITUDE_DEGREES = 43.0
MIDSUMMER_DAY_OF_YEAR = 182
MIDWINTER_DAY_OF_YEAR = 1


def test_as_of_mode_is_recorded_per_schema_version() -> None:
    """v1's global as-of is a known deviation; a receipt must never carry the number without it."""
    assert AS_OF_MODE_BY_SCHEMA_VERSION[SCHEMA_VERSION_V1] == "global"
    assert AS_OF_MODE_BY_SCHEMA_VERSION[SCHEMA_VERSION_V2].startswith("per_issue_date")


def test_an_unsupported_schema_version_is_refused_at_ingress() -> None:
    with pytest.raises(CovariateReadError, match="unknown feature schema version"):
        require_supported_schema_version("agri_covariates_v3")


def test_a_supported_schema_version_is_returned_unchanged() -> None:
    assert require_supported_schema_version(SCHEMA_VERSION_V2) == SCHEMA_VERSION_V2


def test_hargreaves_is_positive_in_summer_and_refuses_inverted_temperatures() -> None:
    summer = hargreaves_reference_evapotranspiration_mm(
        latitude_degrees=PILOT_LATITUDE_DEGREES,
        day_of_year=MIDSUMMER_DAY_OF_YEAR,
        temperature_min_c=12.0,
        temperature_max_c=32.0,
    )
    winter = hargreaves_reference_evapotranspiration_mm(
        latitude_degrees=PILOT_LATITUDE_DEGREES,
        day_of_year=MIDWINTER_DAY_OF_YEAR,
        temperature_min_c=-6.0,
        temperature_max_c=2.0,
    )

    assert summer > winter > 0.0

    with pytest.raises(CovariateReadError):
        hargreaves_reference_evapotranspiration_mm(
            latitude_degrees=PILOT_LATITUDE_DEGREES,
            day_of_year=MIDWINTER_DAY_OF_YEAR,
            temperature_min_c=10.0,
            temperature_max_c=2.0,
        )
