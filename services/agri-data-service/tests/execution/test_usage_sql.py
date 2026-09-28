"""GL-4's two owned SQL files hold their documentation header, their declared bind parameters and
exactly one statement each; `tests/test_sql_tree_conventions.py` already proves the header, phantom-
bind, marker-safety and single-load-site rules for every file under `sql/` including these, so this
suite checks what is specific to o4: the exact bind-parameter set each file's Python loader expects,
and that a statement never smuggles in a second top-level `;`-separated statement (plan 0W.4,
`test_usage_sql_headers_bind_params_and_one_statement_each`)."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy

from agri_data_service.execution.gap_repair_contract import REPAIR_LANE_SUFFIX

_SQL_ROOT = Path(__file__).resolve().parents[2] / "src" / "agri_data_service" / "sql" / "execution"

_EXPECTED_BINDS: dict[str, frozenset[str]] = {
    "select_provider_usage.sql": frozenset({"since", "until", "lane_ids", "pool"}),
    "select_provider_month_to_date.sql": frozenset({"pool", "now", "logical_caps", "weighted_pools"}),
    "select_open_incidents.sql": frozenset(),
}


def _body_only(text: str) -> str:
    """The file with every `--` comment line blanked out."""
    return "\n".join("" if line.lstrip().startswith("--") else line for line in text.splitlines())


@pytest.mark.parametrize("filename", sorted(_EXPECTED_BINDS))
def test_usage_sql_headers_bind_params_and_one_statement_each(filename: str) -> None:
    path = _SQL_ROOT / filename
    raw = path.read_text(encoding="utf-8")
    head = "\n".join(raw.splitlines()[:24])
    assert "-- Purpose:" in head, f"{filename}: missing '-- Purpose:' within the header window"
    assert "-- Loaded by:" in head, f"{filename}: missing '-- Loaded by:' within the header window"

    body = _body_only(raw)
    binds = set(sqlalchemy.text(body)._bindparams)
    assert binds == _EXPECTED_BINDS[filename], (
        f"{filename}: bind parameters {sorted(binds)} do not match the documented/loaded set "
        f"{sorted(_EXPECTED_BINDS[filename])}"
    )

    # One statement per file (sql/AGENTS.md "Layout and loading"): none of these CTE-and-SELECT
    # statements has any legitimate reason to contain a top-level statement separator.
    assert ";" not in body, f"{filename}: a semicolon in the statement body suggests more than one statement"


def test_every_owned_sql_file_is_present() -> None:
    for filename in _EXPECTED_BINDS:
        assert (_SQL_ROOT / filename).is_file(), f"expected {filename} under {_SQL_ROOT}"


def test_provider_usage_statement_never_raises_jsonb_each_on_a_json_null_hosts_value() -> None:
    """F1, the row-level statement's own copy of the same guard select_provider_month_to_date.sql
    needs -- see that file's test of the same name for the underlying jsonb `null` mechanics."""
    text = (_SQL_ROOT / "select_provider_usage.sql").read_text(encoding="utf-8")
    assert "jsonb_typeof(windowed.metrics -> 'usage' -> 'hosts') = 'object'" in text
    assert "COALESCE(windowed.metrics -> 'usage' -> 'hosts'" not in text


def test_provider_usage_statement_excludes_running_and_deferred() -> None:
    """F5: section 2's row-level feed must agree with section 1 on which attempts are "closed" --
    a `deferred` (parked/interrupted) attempt has not reached a terminal outcome for its shard, and
    the spec's charging table excludes it, same as select_provider_month_to_date.sql's `scoped` CTE."""
    text = (_SQL_ROOT / "select_provider_usage.sql").read_text(encoding="utf-8")
    assert "attempt.status NOT IN ('running', 'deferred')" in text


def test_provider_usage_statement_buckets_started_on_by_the_utc_calendar_day() -> None:
    """F3: `--by day` must not depend on the session `TimeZone` GUC; a bare `started_at::date` does,
    because it truncates in the SESSION zone, not UTC."""
    text = (_SQL_ROOT / "select_provider_usage.sql").read_text(encoding="utf-8")
    assert "(windowed.started_at AT TIME ZONE 'UTC')::date AS started_on" in text
    assert "windowed.started_at::date" not in text


def test_repair_lane_suffix_is_stripped_by_both_row_level_statements() -> None:
    """ "Repair definitions roll up to their lane by stripping REPAIR_LANE_SUFFIX" (plan 0W.4) --
    both statements that read `job_definition.name` derive `lane_id` with the same
    `regexp_replace(..., ':gap-repair$', '')` expression, so a forward run and its repair land on
    one grouping key."""
    assert REPAIR_LANE_SUFFIX == ":gap-repair"
    for filename in ("select_provider_usage.sql", "select_provider_month_to_date.sql"):
        text = (_SQL_ROOT / filename).read_text(encoding="utf-8")
        # The leading backslash escapes the literal colon so SQLAlchemy's text() never mistakes it
        # for a bind parameter (see the SQL file's own "Escaping a literal colon" header note); the
        # backslash is stripped at compile time, so PostgreSQL still only ever sees ":gap-repair$".
        assert "regexp_replace(definition.name, '\\:gap-repair$', '')" in text, (
            f"{filename}: expected the REPAIR_LANE_SUFFIX-stripping lane_id expression"
        )


def test_month_to_date_statement_excludes_running_and_deferred_and_respects_the_epoch() -> None:
    """Read-time exclusions the spec's charging table requires (FR-34): running/deferred attempts
    never count, and nothing before the metering epoch -- or before any epoch exists at all -- is
    priced (`test_running_attempts_are_excluded`, `test_pre_epoch_and_unspawned_attempts_are_never_
    charged`). `epoch.epoch_at IS NOT NULL` (not `IS NULL OR ...`) is deliberate: with no epoch
    established yet, nothing has ever been "priced since the epoch"."""
    text = (_SQL_ROOT / "select_provider_month_to_date.sql").read_text(encoding="utf-8")
    assert "status NOT IN ('running', 'deferred')" in text
    assert "metrics ? 'spawned'" in text, "the epoch CTE must be keyed off attempts o5a's fold actually stamped"
    assert "epoch.epoch_at IS NOT NULL" in text
    assert "attempt.started_at >= epoch.epoch_at" in text


def test_month_to_date_statement_bounds_the_month_to_utc_and_never_spills_into_a_later_month() -> None:
    """F3: `date_trunc(..., 'UTC')` pins the boundary regardless of the session `TimeZone` GUC, and
    the upper bound keeps a PAST `now` (the closed-month receipt, WQ-6) from summing every later
    month too."""
    text = (_SQL_ROOT / "select_provider_month_to_date.sql").read_text(encoding="utf-8")
    assert "date_trunc('month', CAST(:now AS timestamptz), 'UTC')" in text
    assert "+ interval '1 month'" in text


def test_month_to_date_statement_never_raises_jsonb_each_on_a_json_null_hosts_value() -> None:
    """F1: `usage.hosts` is JSON `null` (not SQL NULL) for every `not_spawned`/`reported`/`suspect`
    attempt (`job_executor_service.py::_not_spawned_usage`), and jsonb_each raises on anything that
    is not a JSON object. `jsonb_typeof(...) = 'object'` is the only guard that actually catches
    that case; a bare `COALESCE(..., '{}'::jsonb)` does not, because JSON `null` is not SQL NULL."""
    text = (_SQL_ROOT / "select_provider_month_to_date.sql").read_text(encoding="utf-8")
    assert text.count("jsonb_typeof(") >= 2, (  # noqa: PLR2004 - two guard sites: touched_pool and attribution
        "both the touched_pool EXISTS and the attribution CASE must guard"
    )
    assert "COALESCE(attempt.metrics -> 'usage' -> 'hosts'" not in text
    assert "scoped.metrics -> 'usage' -> 'hosts' IS NOT NULL" not in text


def test_month_to_date_statement_scopes_the_basis_split_to_the_requested_pool() -> None:
    """F2: every FILTER in the basis split (`metered_count`, `reported_count`, `suspect_basis_count`,
    `not_spawned_count`) must be scoped to `attributed.attributed_to_pool`, matching `charged`/
    `suspect` -- otherwise a `--pool open-meteo-paid` report's counts include every other pool's and
    every unweighted lane's attempts too."""
    text = (_SQL_ROOT / "select_provider_month_to_date.sql").read_text(encoding="utf-8")
    for basis in ("metered", "reported", "suspect", "not_spawned"):
        assert f"'charged_basis' = '{basis}' AND attributed.attributed_to_pool" in text, (
            f"the {basis} FILTER must require attributed.attributed_to_pool"
        )


def test_month_to_date_statement_prices_a_lost_attempt_at_its_lane_logical_cap() -> None:
    """`test_lost_after_epoch_is_suspect_at_the_logical_cap`: a `status = 'lost'` attempt with no
    usage fold at all is priced from `:logical_caps`, never from its own (nonexistent) metrics."""
    text = (_SQL_ROOT / "select_provider_month_to_date.sql").read_text(encoding="utf-8")
    assert "status = 'lost'" in text
    assert "NOT (attributed.metrics ? 'spawned')" in text
    assert "CAST(:logical_caps AS jsonb) ->> attributed.lane_id" in text


def test_month_to_date_statement_keeps_charged_and_suspect_as_separate_columns() -> None:
    """`test_suspect_basis_is_separate_from_charged`: the statement never folds the two into one
    figure -- a suspect basis counts toward the gap-fill ceiling only, never the charged/stop line."""
    text = (_SQL_ROOT / "select_provider_month_to_date.sql").read_text(encoding="utf-8")
    assert "AS charged" in text
    assert "AS suspect" in text
