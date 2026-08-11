"""Data-readiness report across every stream, forecast plane, and ML surface.

Read-only. Run from services/agri-data-service:

    uv run python scripts/readiness.py            # human-readable report
    uv run python scripts/readiness.py --json     # machine-readable
    uv run python scripts/readiness.py --dsn postgresql://...   # explicit target

The target defaults to DATABASE_URL_SYNC from .env (the prod warehouse). Every
check is fault-isolated: a failed query reports its error and the rest of the
report still runs, so a partially migrated database yields a partial report,
never a crash. Local walk state (scheduled tasks, checkpoint DONE markers) is
best-effort and skipped off-Windows.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import asyncpg

SERVICE_ROOT = Path(__file__).resolve().parent.parent
LOCAL_RUN_ROOT = SERVICE_ROOT / ".agri-local-runs"
STATEMENT_TIMEOUT = "110s"
SCHEDULED_TASK_PREFIXES = ("PlantGeo",)
SCHTASK_CSV_MINIMUM_COLUMNS = 3

# Each check is (section, sql). Column names are verified against the live
# schema as of 20260808_0019 — signal_observation keys cells by `cell_id`,
# release_set carries `state` (not status), and covariate_feature_schema is a
# FUNCTION, not a table (see the fallback signatures below).
WAREHOUSE_CHECKS: list[tuple[str, str]] = [
    (
        "signal_streams",
        """
        select signal_name, support_key, count(*) as rows,
               min(observed_at)::date as from_day, max(observed_at)::date as to_day,
               count(distinct cell_id) as cells
        from agri.signal_observation
        group by 1, 2 order by 1, 2
        """,
    ),
    (
        # Interior time gaps per stream. Presence of ANY cell counts the day as
        # observed, so this finds stream-level holes; per-cell sparsity (normal
        # for NDVI) needs the per-cell variant, not this. `duplicate_rows`
        # exposes overlapping release windows: continuation plans that re-cover
        # already-landed days double raw row counts without adding coverage.
        "signal_stream_day_gaps",
        """
        with days as (
            select signal_name, support_key, observed_at::date as day, count(*) as day_rows
            from agri.signal_observation
            group by 1, 2, 3
        ), sequenced as (
            select signal_name, support_key, day, day_rows,
                   lag(day) over (partition by signal_name, support_key order by day) as previous_day
            from days
        )
        select signal_name, support_key,
               count(*) as observed_days,
               (max(day) - min(day) + 1) - count(*) as missing_days,
               count(*) filter (where day - previous_day > 1) as gap_count,
               max(day - previous_day - 1) as widest_gap_days,
               sum(day_rows) as total_rows
        from sequenced
        group by 1, 2
        order by missing_days desc, signal_name
        """,
    ),
    (
        # Same analysis for the dated geo layers; walks-in-flight legitimately
        # truncate from_day, so a shrinking span is progress, not a hole.
        "geo_layer_day_gaps",
        """
        with days as (
            select layer.name, substring(feature.properties->>'observedAt', 1, 10)::date as day
            from geo.features feature
            join geo.layers layer on layer.id = feature.layer_id
            where layer.name in ('fire-detections', 'vegetation', 'weather-observations',
                                 'sensors', 'evacuation-zones')
              and feature.properties->>'observedAt' is not null
            group by 1, 2
        ), sequenced as (
            select name, day, lag(day) over (partition by name order by day) as previous_day
            from days
        )
        select name,
               min(day) as from_day, max(day) as to_day,
               count(*) as observed_days,
               (max(day) - min(day) + 1) - count(*) as missing_days,
               count(*) filter (where day - previous_day > 1) as gap_count,
               max(day - previous_day - 1) as widest_gap_days
        from sequenced
        group by 1
        order by missing_days desc
        """,
    ),
    (
        "spatial_cells_by_grid",
        "select grid_name, count(*) as cells from agri.spatial_cell group by 1 order by 1",
    ),
    (
        "release_sets_by_state",
        "select state, count(*) as sets from agri.release_set group by 1 order by 1",
    ),
    (
        "forecast_observation_plane",
        """
        select count(*) as rows, min(observed_at)::date as from_day,
               max(observed_at)::date as to_day, count(distinct series_id) as series
        from agri.forecast_observation
        """,
    ),
    (
        "forecast_iterations_by_purpose",
        "select purpose, count(*) as iterations from agri.forecast_iteration group by 1 order by 1",
    ),
    (
        "iteration_values_and_actuals",
        """
        select (select count(*) from agri.forecast_iteration_value) as iteration_values,
               (select count(*) from agri.forecast_iteration_actual) as actuals
        """,
    ),
    (
        "ml_receipt_chain",
        """
        select (select count(*) from agri.forecast_training_run) as training_runs,
               (select count(*) from agri.forecast_backtest_metric) as backtest_metrics,
               (select count(*) from agri.forecast_run) as forecast_runs,
               (select count(*) from agri.forecast_receipt) as receipts,
               (select count(*) from agri.forecast_value) as published_values
        """,
    ),
    (
        "strategy_plane",
        """
        select (select count(*) from agri.strategies) as strategies,
               (select count(*) from agri.strategy_label_episode) as label_episodes,
               (select count(*) from agri.strategy_selection_receipt) as selection_receipts
        """,
    ),
    (
        "job_ledger",
        """
        select definition.name, run.status, count(*) as runs
        from agri.job_run run
        join agri.job_definition definition on definition.id = run.job_definition_id
        group by 1, 2 order by 1, 2
        """,
    ),
    (
        "geo_features_by_layer",
        """
        select layer.name, count(feature.id) as features,
               min(substring(feature.properties->>'observedAt', 1, 10)) as from_day,
               max(substring(feature.properties->>'observedAt', 1, 10)) as to_day
        from geo.layers layer
        left join geo.features feature on feature.layer_id = layer.id
        group by 1 order by 2 desc
        """,
    ),
]

# The covariate layer is served by a function whose signature has changed
# across revisions; try the known shapes and report the first that answers.
COVARIATE_SIGNATURES = [
    "select count(*) as features from agri.covariate_feature_schema()",
    "select count(*) as features from agri.covariate_feature_schema('agri_covariates_v1')",
]


def resolve_dsn(explicit: str | None) -> str:
    if explicit:
        return explicit
    env_text = (SERVICE_ROOT / ".env").read_text(encoding="utf-8")
    match = re.search(r"^DATABASE_URL_SYNC=(\S+)$", env_text, flags=re.M)
    if match is None:
        raise SystemExit("no --dsn given and DATABASE_URL_SYNC is not in .env")
    return match.group(1)


def jsonable(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


async def run_warehouse_checks(dsn: str) -> dict[str, Any]:
    report: dict[str, Any] = {}
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(f"set statement_timeout = '{STATEMENT_TIMEOUT}'")
        try:
            head = await connection.fetchval("select version_num from alembic_version")
        except Exception as exc:
            head = f"ERROR: {exc}"
        expected = expected_revision()
        report["migration"] = {
            "head": head,
            "expected": expected,
            "matches": head == expected if expected else None,
        }
        for section, sql in WAREHOUSE_CHECKS:
            try:
                rows = await connection.fetch(sql)
                report[section] = [
                    {key: jsonable(value) for key, value in row.items()} for row in rows
                ]
            except Exception as exc:
                report[section] = {"error": f"{type(exc).__name__}: {exc}"}
        # The serving matview is created WITH NO DATA and refreshed only by an
        # operator, and Postgres refuses to even PLAN a statement that scans an
        # unpopulated matview — so the population probe and the count must be
        # two separate statements, and "never populated" is a reported state.
        try:
            populated = bool(
                await connection.fetchval(
                    """
                    select cls.relispopulated
                    from pg_class cls
                    join pg_namespace ns on ns.oid = cls.relnamespace
                    where ns.nspname = 'agri' and cls.relname = 'mv_forecast_ml_daily_serving'
                    """
                )
            )
            rows = (
                await connection.fetchval("select count(*) from agri.mv_forecast_ml_daily_serving")
                if populated
                else 0
            )
            report["ml_serving_matview"] = {"populated": populated, "rows": rows}
        except Exception as exc:
            report["ml_serving_matview"] = {"error": f"{type(exc).__name__}: {exc}"}
        for sql in COVARIATE_SIGNATURES:
            try:
                report["covariate_schema"] = {
                    "feature_count": await connection.fetchval(sql),
                    "queried_with": sql,
                }
                break
            except Exception as exc:
                report["covariate_schema"] = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        await connection.close()
    return report


def expected_revision() -> str | None:
    try:
        sys.path.insert(0, str(SERVICE_ROOT / "src"))
        # Lazy on purpose: the report must still run from a checkout where the
        # package (or a dependency of the contracts module) cannot import.
        from agri_data_service.routes.health.contracts import (  # noqa: PLC0415
            EXPECTED_ALEMBIC_REVISION,
        )

        return EXPECTED_ALEMBIC_REVISION
    except Exception:
        return None


def scheduled_walk_status() -> list[dict[str, str]] | dict[str, str]:
    if sys.platform != "win32":
        return {"skipped": "scheduled-task inspection is Windows-only"}
    try:
        raw = subprocess.run(
            ["schtasks", "/query", "/fo", "csv", "/nh"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    walks = []
    for line in raw.splitlines():
        columns = [column.strip('"') for column in line.split('","')]
        if len(columns) >= SCHTASK_CSV_MINIMUM_COLUMNS and any(
            prefix in columns[0] for prefix in SCHEDULED_TASK_PREFIXES
        ):
            walks.append({"task": columns[0].lstrip('"\\'), "next_run": columns[1], "status": columns[2]})
    return walks


def checkpoint_state() -> dict[str, Any]:
    locks_directory = LOCAL_RUN_ROOT / "locks"
    if not locks_directory.exists():
        return {"skipped": f"{locks_directory} does not exist on this machine"}
    done = sorted(marker.stem for marker in locks_directory.glob("*.done"))
    held = sorted(lock.name for lock in locks_directory.glob("*.lock"))
    lanes = sorted(
        entry.name for entry in LOCAL_RUN_ROOT.iterdir()
        if entry.is_dir() and entry.name not in {"locks", "logs", "pytest-tmp"}
    )
    return {"lanes_with_checkpoints": lanes, "completed_plans": done, "held_locks": held}


def render(report: dict[str, Any]) -> str:
    lines: list[str] = []
    for section, payload in report.items():
        lines.append(f"\n== {section}")
        if isinstance(payload, list):
            lines.extend(
                "  " + "  ".join(f"{key}={value}" for key, value in entry.items())
                for entry in payload
            )
        else:
            lines.append("  " + json.dumps(payload, default=jsonable))
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", help="explicit postgresql:// DSN; defaults to .env DATABASE_URL_SYNC")
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
    arguments = parser.parse_args()

    report = await run_warehouse_checks(resolve_dsn(arguments.dsn))
    report["scheduled_walks"] = scheduled_walk_status()
    report["local_checkpoints"] = checkpoint_state()

    if arguments.json:
        print(json.dumps(report, indent=2, default=jsonable))
    else:
        print(render(report))


if __name__ == "__main__":
    asyncio.run(main())
