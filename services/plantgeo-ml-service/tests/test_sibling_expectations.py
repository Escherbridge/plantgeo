"""The receipt digests only this service's tree; a cross-service expectation can break silently.

2026-09-19 incident: an ML-only commit changed the fire-risk provenance `quantile` column from
numeric to string. This service's own suite stayed green because nothing here reads agri's copy of
that schema. agri's suite went red on `tests/parquet/test_ml_schema_parity.py`, and neither
service's QUALITY_RECEIPT noticed, because each digests only its own tree (see
`conductor/tracks/plantgeo_ml_service_20260918/metadata.json` -> "incidents"[0]).

This test runs agri's cross-service expectations -- the schema-parity tests and the
`forecast_module` binding tests in `tests/parquet/test_lane_contract.py` -- against the *current*
ML-service tree, in a subprocess, with agri as the working directory. A change here that breaks
agri's expectations of this service now fails in THIS sweep, before it lands.

Selected by exact node id, not `-k`: several of agri's own parity test names (for example
`test_the_parity_check_runs_rather_than_skips_in_a_monorepo_checkout`) do not contain the substring
"ml", so a `-k "ml or forecast_module"` filter silently drops them. Naming every node id is the only
way to run exactly agri's cross-service claims without also running its whole suite (which exercises
agri's own database and ingest code this service must never depend on).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

#: `tests/` -> `plantgeo-ml-service` -> `services` -> repository root. Mirrors `parity_cases.py`'s
#: `REPOSITORY_ROOT`, which lives at the same depth from a different file under the same `tests/`.
_REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
_SIBLING_DIR: Final = _REPOSITORY_ROOT / "services" / "agri-data-service"
_SIBLING_SOURCE_MARKER: Final = _SIBLING_DIR / "src" / "agri_data_service" / "foundation" / "parquet" / "paths.py"
_SIBLING_VENV: Final = _SIBLING_DIR / ".venv"

#: Every agri test that reads this service's modules by path or import, named exactly. See the
#: module docstring for why this is a fixed list rather than a `-k` expression.
_SIBLING_NODE_IDS: Final = (
    "tests/parquet/test_ml_schema_parity.py::test_the_fire_risk_schema_is_the_ml_services_own_object_field_for_field",
    "tests/parquet/test_ml_schema_parity.py::test_the_fire_risk_copies_agree_column_by_column_so_a_failure_names_the_field",
    "tests/parquet/test_ml_schema_parity.py::test_the_weather_forecast_schema_is_the_ml_services_own_nineteen_columns",
    "tests/parquet/test_ml_schema_parity.py::test_the_weather_forecast_copies_agree_column_by_column_so_a_failure_names_the_field",
    "tests/parquet/test_ml_schema_parity.py::test_the_expert_label_export_writes_the_shape_the_ml_service_pins_for_reading",
    "tests/parquet/test_ml_schema_parity.py::test_the_expert_label_sort_column_exists_on_both_sides_so_the_exported_order_is_readable",
    "tests/parquet/test_ml_schema_parity.py::test_the_parity_check_runs_rather_than_skips_in_a_monorepo_checkout",
    "tests/parquet/test_lane_contract.py::test_every_forecast_module_claim_names_a_real_ml_module",
    "tests/parquet/test_lane_contract.py::test_a_lane_claiming_no_forecaster_has_no_module_lying_in_wait_under_its_slug",
    "tests/parquet/test_lane_contract.py::test_the_two_lanes_the_ml_service_writes_are_registered_with_the_right_claims",
)

#: Escape hatch for a deliberately sibling-less run (never the Docker image: that build runs a
#: digest gate, not pytest, so it never imports this file at all). Set ONLY by a CI lane that is
#: intentionally checking out this service alone. Never set this in a checkout that has the sibling
#: tree -- doing so hides exactly the class of break this guard exists to catch.
_ABSENT_OK_VAR: Final = "PLANTGEO_ML_SIBLING_TESTS"
_ABSENT_OK_VALUE: Final = "absent-ok"


def _absent_ok() -> bool:
    return os.environ.get(_ABSENT_OK_VAR) == _ABSENT_OK_VALUE


def test_the_sibling_cross_service_expectations_still_pass_against_this_tree() -> None:
    """Run agri's schema-parity and forecast_module tests against the current ML-service tree.

    Fails loudly (not skips) when the sibling tree or its `.venv` is missing, unless
    `PLANTGEO_ML_SIBLING_TESTS=absent-ok` is set -- and even then, prints a line naming that the
    parity check was NOT run, so a green summary cannot be read as "parity verified" by silence.
    """
    if not _SIBLING_SOURCE_MARKER.is_file():
        if _absent_ok():
            message = (
                f"PLANTGEO_ML_SIBLING_TESTS=absent-ok honoured: sibling parity NOT verified "
                f"(no agri-data-service source at {_SIBLING_SOURCE_MARKER})"
            )
            print(message)
            pytest.skip(message)
        pytest.fail(
            f"no agri-data-service source at {_SIBLING_SOURCE_MARKER}; the cross-service schema-parity "
            f"and forecast_module guard cannot run. This is expected ONLY inside the Docker image, which "
            f"runs a digest gate rather than pytest and never imports this test file. In any other "
            f"checkout, restore the sibling tree, or set {_ABSENT_OK_VAR}={_ABSENT_OK_VALUE} if this is a "
            f"deliberately sibling-less CI lane (never do this in a checkout that has the sibling)."
        )

    if not _SIBLING_VENV.is_dir():
        if _absent_ok():
            message = (
                f"PLANTGEO_ML_SIBLING_TESTS=absent-ok honoured: sibling parity NOT verified "
                f"(no agri-data-service .venv at {_SIBLING_VENV})"
            )
            print(message)
            pytest.skip(message)
        pytest.fail(
            f"agri-data-service source is present but its .venv is missing at {_SIBLING_VENV}; running "
            f"`uv run` there would sync/install rather than use a pinned environment, which this guard "
            f"must not do. Run `uv sync --locked --all-extras` in {_SIBLING_DIR} once, or set "
            f"{_ABSENT_OK_VAR}={_ABSENT_OK_VALUE} if this is a deliberately sibling-less CI lane."
        )

    command = [
        "uv",
        "run",
        "--no-sync",
        "pytest",
        "-q",
        *_SIBLING_NODE_IDS,
    ]
    try:
        result = subprocess.run(
            command,
            cwd=_SIBLING_DIR,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired as error:  # pragma: no cover - defensive, not expected in practice
        pytest.fail(f"agri-data-service sibling tests did not finish within 600s: {error}")
        return

    if result.returncode != 0:
        summary_lines = "\n".join(result.stdout.strip().splitlines()[-40:])
        pytest.fail(
            "agri-data-service's cross-service tests fail against THIS ML-service tree "
            f"(exit {result.returncode}); a change here broke a sibling expectation. "
            f"Command: {' '.join(command)} (cwd={_SIBLING_DIR})\n"
            f"--- sibling pytest summary (last 40 lines of stdout) ---\n{summary_lines}\n"
            f"--- stderr ---\n{result.stderr.strip()}"
        )


if __name__ == "__main__":  # pragma: no cover - convenience for a manual run
    sys.exit(pytest.main([__file__, "-q"]))
