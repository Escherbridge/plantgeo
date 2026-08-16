"""The matview-refresh lane: pure-unit coverage with no database, mirroring test_strategy_mv_refresh.py.

The seam is `AsyncSession.execute`; a small fake session answers each statement by inspecting its SQL
text, dispatching on the `-- <marker>` line the loaded `.sql` files carry (`select_matview_refresh_state`,
`upsert_matview_refresh_state`, `select_materialized_view_populated`, the watermark queries' own inline
markers) or, for statements this lane builds dynamically per view name (REFRESH, the row-count estimate,
the existence check), on the column alias each one selects.

Timing-sensitive tests freeze `datetime.now` by monkeypatching the NAME `matview_refresh.datetime` to a
`datetime.datetime` subclass with an overridden `now()` classmethod -- `datetime.datetime` itself is an
immutable C type and refuses `setattr` on its own `now`, so the subclass-and-rebind-the-module-name
technique is the only one that works. Two tests need it (their eligibility math depends on elapsed time
against a real clock); the rest use specs with no prior state at all, where `_eligibility` short-circuits
on "never refreshed" before `now` is ever consulted, so they need no clock control.
"""

# ruff: noqa: PLR2004 -- every assertion below names a literal statement count, row count, or seconds
# figure; a named constant per literal would read no clearer than the number next to its assertion.

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy.exc import OperationalError

from agri_data_service.jobs import matview_refresh as mv_module
from agri_data_service.jobs.dispatch import LANE_DISPATCH
from agri_data_service.jobs.matview_refresh import (
    MATVIEW_REFRESH_DEFINITION_NAME,
    MATVIEW_REFRESH_HANDLER_TOKEN,
    MATVIEW_REFRESH_LEASE_SECONDS,
    MATVIEW_REFRESH_QUALIFIED_NAMES,
    MATVIEW_REFRESH_RUN_KEY,
    MATVIEW_REFRESH_SPECS,
    MATVIEW_REFRESH_TIME_BUDGET_SECONDS,
    MatviewRefreshContextError,
    MatviewRefreshLaneContext,
    MatviewRefreshReport,
    MatviewRefreshResult,
    _budget_skipped_result,
    _cursor_completed_views,
    _eligibility,
    _estimate_seconds,
    _PriorRefreshState,
    _refresh_one_matview,
    _remaining_budget_seconds,
    _watermark_signature,
    _work_item_shard_key,
    current_matview_refresh_context,
    matview_refresh_context,
    matview_refresh_definition_spec,
    matview_refresh_handler,
    trigger_matview_refresh,
)
from agri_data_service.jobs.registry import JOB_HANDLERS, JobInvocation

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# The hand-spelled 11-name list this lane must cover -- asserted against, never generated from, the
# module's own MATVIEW_REFRESH_SPECS, so the two cannot drift together and still pass. Nine new
# matviews drizzle/0029 creates plus two adopted (geo.watershed_rollup, agri.mv_forecast_ml_daily_serving).
EXPECTED_MATVIEW_NAMES: frozenset[str] = frozenset(
    {
        "geo.mv_feature_observation_day",
        "geo.mv_signal_observation_day",
        "geo.mv_drought_observation_day",
        "geo.mv_signal_cell_daily",
        "geo.mv_drought_release_index",
        "geo.mv_layer_feature_stats",
        "geo.mv_layer_hourly_activity",
        "geo.mv_soil_survey_grid",
        "geo.mv_soil_survey_union",
        "geo.watershed_rollup",
        "agri.mv_forecast_ml_daily_serving",
    }
)

_WATERMARK_MARKERS: dict[str, Mapping[str, object]] = {
    "matview_refresh_watermark_features_updated_at": {"watermark": "2026-08-01T00:00:00+00:00"},
    "matview_refresh_watermark_features_updated_at_hourly": {
        "watermark": "2026-08-01T00:00:00+00:00",
        "current_hour": "2026-08-15T12:00:00+00:00",
    },
    "matview_refresh_watermark_drought_areas": {"watermark": "2026-08-01", "row_count": 10},
    "matview_refresh_watermark_drought_areas_live_edge": {
        "watermark": "2026-08-01",
        "row_count": 10,
        "current_utc_day": "2026-08-15",
    },
    "matview_refresh_watermark_source_release": {"watermark": "2026-08-01T00:00:00+00:00"},
    "matview_refresh_watermark_soil_survey_coverage": {"watermark": "2026-08-01T00:00:00+00:00"},
    "matview_refresh_watermark_watershed_features": {"watermark": "2026-08-01T00:00:00+00:00"},
    "matview_refresh_watermark_forecast_publication": {"watermark": "2026-08-01T00:00:00+00:00"},
}


def _watermark_markers_longest_first() -> list[tuple[str, Mapping[str, object]]]:
    """The marker table ordered so a marker is never matched by one of its own prefixes.

    Two of the eight are clock-bearing variants of another -- `..._features_updated_at_hourly`
    CONTAINS `..._features_updated_at`, and `..._drought_areas_live_edge` contains
    `..._drought_areas`. A substring scan in declaration order would answer the variant with the
    base row, which is invisible while both defaults agree and silently wrong the moment a test
    overrides one of them.
    """
    return sorted(_WATERMARK_MARKERS.items(), key=lambda item: len(item[0]), reverse=True)


def _default_watermark_row_for(spec: Any) -> Mapping[str, object]:
    """The default row the fake session answers this spec's own watermark query with."""
    sql_text = str(spec.watermark_sql)
    for marker, row in _watermark_markers_longest_first():
        if marker in sql_text:
            return row
    raise AssertionError(f"no default watermark row scripted for {spec.qualified_name}")


def _fresh_prior_state_rows(refreshed_at: datetime) -> list[dict[str, object]]:
    """Every spec reported as successfully refreshed, against the default watermark answer, at `refreshed_at`."""
    return [
        {
            "view_name": spec.qualified_name,
            "source_watermark": _watermark_signature(_default_watermark_row_for(spec)),
            "refreshed_at": refreshed_at,
            "duration_ms": 500,
            "row_count": 10,
            "outcome": "refreshed_concurrently",
        }
        for spec in MATVIEW_REFRESH_SPECS
    ]


class _FrozenDateTime(datetime):
    """A `datetime` stand-in whose `.now()` returns a fixed instant.

    `datetime.datetime` is an immutable C type -- `monkeypatch.setattr(datetime, "now", ...)` raises
    `TypeError: cannot set 'now' attribute of immutable type`. Rebinding the NAME `datetime` inside
    `matview_refresh`'s own module namespace to this subclass is what actually works, because the
    handler resolves `datetime.now(UTC)` through that module-global lookup at call time.
    """

    # A real default, not a bare annotation: `monkeypatch.setattr(_FrozenDateTime, "fixed_now", ...)`
    # requires the attribute to already exist (its default `raising=True` refuses to create one).
    fixed_now: datetime = datetime(1970, 1, 1, tzinfo=UTC)

    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        return cls.fixed_now if tz is None else cls.fixed_now.astimezone(tz)


class FakeResult:
    """The narrow slice of SQLAlchemy's Result this lane actually uses."""

    def __init__(self, rows: Sequence[Mapping[str, object]]) -> None:
        self._rows = list(rows)

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> Mapping[str, object] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Mapping[str, object]]:
        return self._rows


class FakeSession:
    """A minimal AsyncSession stand-in scripted with view shape, watermark answers, and prior state."""

    def __init__(  # noqa: PLR0913 - one keyword per scriptable axis of this lane's behaviour
        self,
        *,
        existing_views: frozenset[str] | None = None,
        populated_views: frozenset[str] | None = None,
        unique_index_views: frozenset[str] | None = None,
        fail_on: frozenset[str] = frozenset(),
        watermark_overrides: Mapping[str, Mapping[str, object]] | None = None,
        prior_state_rows: Sequence[Mapping[str, object]] = (),
        row_count_estimates: Mapping[str, int] | None = None,
    ) -> None:
        all_views = MATVIEW_REFRESH_QUALIFIED_NAMES
        self._existing_views = all_views if existing_views is None else existing_views
        self._populated_views = all_views if populated_views is None else populated_views
        self._unique_index_views = all_views if unique_index_views is None else unique_index_views
        self._fail_on = fail_on
        self._watermark_overrides = dict(watermark_overrides or {})
        self._prior_state_rows = list(prior_state_rows)
        self._row_count_estimates = dict(row_count_estimates or {})
        self.statements: list[tuple[str, dict[str, object]]] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(  # noqa: PLR0911 - one return per statement shape the lane issues; a dispatch
        # table keyed on a substring test would be the same nine branches with a layer of indirection
        # over them, and the branch bodies would stop reading next to the SQL they answer.
        self,
        statement: object,
        parameters: Mapping[str, object] | None = None,
    ) -> FakeResult:
        sql = str(statement)
        params = dict(parameters or {})
        self.statements.append((sql, params))

        if "select_matview_refresh_state" in sql:
            return FakeResult(list(self._prior_state_rows))
        if "upsert_matview_refresh_state" in sql:
            return FakeResult([dict(params)])
        if "SET LOCAL" in sql:
            # Catches both this lane's own three per-refresh settings (which start with SET LOCAL
            # directly) and jobs.lease.apply_statement_timeout's statement, which carries a leading
            # `-- statement_timeout` marker comment before the SET LOCAL text.
            return FakeResult([])
        if sql.startswith("REFRESH MATERIALIZED VIEW"):
            target = sql.rsplit(" ", 1)[-1]
            if target in self._fail_on:
                raise OperationalError("REFRESH failed", {}, Exception("boom"))
            return FakeResult([])
        if "view_exists" in sql:
            qualified_name = params["qualified_name"]
            return FakeResult([{"view_exists": qualified_name in self._existing_views}])
        if "reltuples" in sql:
            qualified_name = params["qualified_name"]
            return FakeResult([{"row_count_estimate": self._row_count_estimates.get(qualified_name, 100)}])
        if "is_populated" in sql:
            qualified_name = f"{params['schema_name']}.{params['view_name']}"
            return FakeResult([{"is_populated": qualified_name in self._populated_views}])
        if "has_unique_index" in sql:
            qualified_name = f"{params['schema_name']}.{params['view_name']}"
            return FakeResult([{"has_unique_index": qualified_name in self._unique_index_views}])
        for marker, default_row in _watermark_markers_longest_first():
            if marker in sql:
                return FakeResult([self._watermark_overrides.get(marker, default_row)])
        raise AssertionError(f"FakeSession has no answer scripted for: {sql[:160]!r}")

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    def refresh_statements(self) -> list[str]:
        return [sql for sql, _ in self.statements if sql.startswith("REFRESH MATERIALIZED VIEW")]


class _FakeClock:
    """A monotonic clock a test drives deterministically, advancing by `step` on every read."""

    def __init__(self, *, start: float = 0.0, step: float = 0.0) -> None:
        self._value = start
        self._step = step

    def monotonic(self) -> float:
        value = self._value
        self._value += self._step
        return value


def _fake_invocation(
    *,
    seconds_remaining: float = MATVIEW_REFRESH_TIME_BUDGET_SECONDS,
    cursor: Mapping[str, object] | None = None,
) -> JobInvocation:
    async def _heartbeat() -> bool:
        return True

    return JobInvocation(
        shard_key="matview-refresh:test",
        kind="matview_refresh_batch",
        payload={},
        cursor=cursor,
        parameters={},
        attempt_number=1,
        max_attempts=3,
        progress_fraction=0.0,
        seconds_remaining=seconds_remaining,
        heartbeat=_heartbeat,
    )


# --- The spec table itself: the denominator this lane must cover, hand-spelled against it. ---


def test_the_spec_table_covers_exactly_the_hand_spelled_eleven_views() -> None:
    assert MATVIEW_REFRESH_QUALIFIED_NAMES == EXPECTED_MATVIEW_NAMES
    assert len(MATVIEW_REFRESH_SPECS) == len(EXPECTED_MATVIEW_NAMES) == 11


def test_every_spec_name_is_unique() -> None:
    names = [spec.qualified_name for spec in MATVIEW_REFRESH_SPECS]
    assert len(names) == len(set(names))


def test_the_three_strategy_recommendation_views_are_not_on_this_lane() -> None:
    """This lane must never widen to cover the guardrailed lane's own views (jobs/strategy_mv_refresh.py)."""
    assert not any("strategy_recommendations" in name for name in MATVIEW_REFRESH_QUALIFIED_NAMES)


def test_the_heaviest_view_carries_the_highest_priority_number_and_widest_watermark_bounds() -> None:
    by_name = {spec.qualified_name: spec for spec in MATVIEW_REFRESH_SPECS}
    heavy = by_name["geo.mv_signal_cell_daily"]
    assert heavy.priority == max(spec.priority for spec in MATVIEW_REFRESH_SPECS)
    assert heavy.min_interval_seconds == 86_400
    assert heavy.max_staleness_seconds == 259_200


def test_no_spec_carries_a_geometry_producing_watermark_query() -> None:
    """geo.mv_drought_release_index's watermark must never select geom -- 495 MB of TOAST behind 1,040 rows."""
    for spec in MATVIEW_REFRESH_SPECS:
        assert "geom" not in str(spec.watermark_sql).lower()


def test_definition_spec_lease_exceeds_time_budget_and_matches_the_handler_token() -> None:
    spec = matview_refresh_definition_spec()
    assert spec.name == MATVIEW_REFRESH_DEFINITION_NAME
    assert spec.handler == MATVIEW_REFRESH_HANDLER_TOKEN
    assert spec.lease_seconds == MATVIEW_REFRESH_LEASE_SECONDS
    assert spec.time_budget_seconds == MATVIEW_REFRESH_TIME_BUDGET_SECONDS
    assert spec.lease_seconds > spec.time_budget_seconds
    assert set(spec.parameters["views"]) == EXPECTED_MATVIEW_NAMES


def test_handler_token_is_registered_at_import_time() -> None:
    assert MATVIEW_REFRESH_HANDLER_TOKEN in JOB_HANDLERS


def test_this_lane_publishes_itself_to_the_dispatcher_so_the_route_needs_no_branch_for_it() -> None:
    lane = LANE_DISPATCH.lane_for(MATVIEW_REFRESH_DEFINITION_NAME)
    assert lane.handler_token == MATVIEW_REFRESH_HANDLER_TOKEN
    assert lane.trigger is trigger_matview_refresh


# --- _work_item_shard_key: the persistent-run mechanism, not a rotating bucket ---


def test_work_item_shard_key_is_unique_per_microsecond_not_bucketed_to_a_window() -> None:
    first = datetime(2026, 8, 15, 12, 0, 0, 1, tzinfo=UTC)
    second = datetime(2026, 8, 15, 12, 0, 0, 2, tzinfo=UTC)
    assert _work_item_shard_key(first) != _work_item_shard_key(second)


# --- _eligibility: the watermark gate, in pure logic ---


def test_a_view_with_no_prior_state_is_always_eligible() -> None:
    spec = MATVIEW_REFRESH_SPECS[0]
    eligible, reason = _eligibility(spec, None, "some-watermark", now=datetime(2026, 8, 15, tzinfo=UTC))
    assert eligible
    assert "never" in reason


def test_an_unchanged_watermark_within_max_staleness_is_skipped() -> None:
    spec = next(s for s in MATVIEW_REFRESH_SPECS if s.qualified_name == "geo.mv_layer_feature_stats")
    now = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)
    prior = _PriorRefreshState(
        source_watermark="w1", refreshed_at=now - timedelta(seconds=spec.max_staleness_seconds - 1), duration_ms=100
    )
    eligible, reason = _eligibility(spec, prior, "w1", now=now)
    assert not eligible
    assert reason == "watermark unchanged"


def test_an_unchanged_watermark_past_max_staleness_is_eligible_anyway() -> None:
    spec = next(s for s in MATVIEW_REFRESH_SPECS if s.qualified_name == "geo.mv_layer_feature_stats")
    now = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)
    prior = _PriorRefreshState(
        source_watermark="w1", refreshed_at=now - timedelta(seconds=spec.max_staleness_seconds + 1), duration_ms=100
    )
    eligible, reason = _eligibility(spec, prior, "w1", now=now)
    assert eligible
    assert "stale" in reason


def test_a_changed_watermark_inside_min_interval_is_rate_limited() -> None:
    spec = next(s for s in MATVIEW_REFRESH_SPECS if s.qualified_name == "geo.mv_layer_feature_stats")
    now = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)
    prior = _PriorRefreshState(
        source_watermark="w1", refreshed_at=now - timedelta(seconds=spec.min_interval_seconds - 1), duration_ms=100
    )
    eligible, reason = _eligibility(spec, prior, "w2", now=now)
    assert not eligible
    assert "min_interval_seconds" in reason


def test_a_changed_watermark_past_min_interval_is_eligible() -> None:
    spec = next(s for s in MATVIEW_REFRESH_SPECS if s.qualified_name == "geo.mv_layer_feature_stats")
    now = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)
    prior = _PriorRefreshState(
        source_watermark="w1", refreshed_at=now - timedelta(seconds=spec.min_interval_seconds + 1), duration_ms=100
    )
    eligible, reason = _eligibility(spec, prior, "w2", now=now)
    assert eligible
    assert reason == "watermark changed"


# --- _estimate_seconds / _watermark_signature / _cursor_completed_views: small pure helpers ---


def test_estimate_uses_a_positive_prior_duration() -> None:
    spec = MATVIEW_REFRESH_SPECS[0]
    prior = _PriorRefreshState(source_watermark="w", refreshed_at=None, duration_ms=4_500)
    assert _estimate_seconds(spec, prior) == pytest.approx(4.5)


def test_estimate_falls_back_to_the_spec_default_with_no_prior_duration() -> None:
    spec = MATVIEW_REFRESH_SPECS[0]
    assert _estimate_seconds(spec, None) == spec.default_estimate_seconds
    zeroed = _PriorRefreshState(source_watermark="w", refreshed_at=None, duration_ms=0)
    assert _estimate_seconds(spec, zeroed) == spec.default_estimate_seconds


def test_watermark_signature_is_order_independent_and_none_reads_as_empty() -> None:
    assert _watermark_signature({"a": 1, "b": 2}) == _watermark_signature({"b": 2, "a": 1})
    assert _watermark_signature(None) == _watermark_signature({})


def test_cursor_completed_views_reads_a_list_and_tolerates_garbage() -> None:
    assert _cursor_completed_views(None) == ()
    assert _cursor_completed_views({"completed_views": ["a", "b"]}) == ("a", "b")
    assert _cursor_completed_views({"completed_views": "not-a-list"}) == ()
    assert _cursor_completed_views({}) == ()


def test_remaining_budget_seconds_subtracts_elapsed_wall_clock_from_the_frozen_snapshot() -> None:
    invocation = _fake_invocation(seconds_remaining=100.0)
    assert _remaining_budget_seconds(invocation, handler_started=0.0, monotonic_now=0.0) == 100.0
    assert _remaining_budget_seconds(invocation, handler_started=0.0, monotonic_now=40.0) == 60.0
    assert _remaining_budget_seconds(invocation, handler_started=0.0, monotonic_now=1_000.0) == 0.0


def test_budget_skipped_result_carries_no_cost() -> None:
    result = _budget_skipped_result(MATVIEW_REFRESH_SPECS[0])
    assert result.status == "skipped_budget"
    assert result.elapsed_seconds == 0.0
    assert result.row_count is None


# --- MatviewRefreshReport: failure semantics and the metrics shape ---


def test_report_has_failures_only_on_a_real_failure() -> None:
    ok = MatviewRefreshResult("v", "refreshed_concurrently", True, 1.0, 10)
    skip = MatviewRefreshResult("v2", "skipped_unchanged", False, 0.0, None)
    assert not MatviewRefreshReport(results=(ok, skip)).has_failures


def test_report_has_failures_when_every_attempted_view_is_missing() -> None:
    missing = MatviewRefreshResult("v", "skipped_missing", False, 0.0, None)
    report = MatviewRefreshReport(results=(missing,))
    assert report.has_failures
    assert "v" in report.failure_summary()


def test_report_is_not_failing_when_only_skipped_unchanged_or_skipped_budget() -> None:
    skip1 = MatviewRefreshResult("v1", "skipped_unchanged", False, 0.0, None)
    skip2 = MatviewRefreshResult("v2", "skipped_budget", False, 0.0, None)
    assert not MatviewRefreshReport(results=(skip1, skip2)).has_failures


def test_report_to_metrics_carries_every_view_and_its_detail() -> None:
    result = MatviewRefreshResult("v", "failed", False, 2.5, None, detail="OperationalError")
    metrics = MatviewRefreshReport(results=(result,)).to_metrics()
    assert metrics["views"] == [
        {
            "view": "v",
            "status": "failed",
            "used_concurrently": False,
            "elapsed_seconds": 2.5,
            "row_count": None,
            "detail": "OperationalError",
        }
    ]


# --- _refresh_one_matview: existence, self-heal, unique-index fallback, failure ---


@pytest.mark.asyncio
async def test_a_missing_view_is_reported_skipped_missing_without_touching_the_others() -> None:
    spec = MATVIEW_REFRESH_SPECS[0]
    session = FakeSession(existing_views=frozenset())
    result = await _refresh_one_matview(session, spec)
    assert result.status == "skipped_missing"
    assert session.refresh_statements() == []


@pytest.mark.asyncio
async def test_an_unpopulated_view_self_heals_with_a_non_concurrent_refresh() -> None:
    spec = MATVIEW_REFRESH_SPECS[0]
    session = FakeSession(populated_views=frozenset())
    result = await _refresh_one_matview(session, spec)
    assert result.status == "self_healed_unpopulated"
    assert result.used_concurrently is False
    assert session.refresh_statements() == [f"REFRESH MATERIALIZED VIEW {spec.qualified_name}"]


@pytest.mark.asyncio
async def test_a_populated_view_with_a_unique_index_refreshes_concurrently() -> None:
    spec = MATVIEW_REFRESH_SPECS[0]
    session = FakeSession()
    result = await _refresh_one_matview(session, spec)
    assert result.status == "refreshed_concurrently"
    assert result.used_concurrently is True
    assert session.refresh_statements() == [f"REFRESH MATERIALIZED VIEW CONCURRENTLY {spec.qualified_name}"]
    assert result.row_count == 100


@pytest.mark.asyncio
async def test_a_missing_unique_index_falls_back_to_a_full_refresh() -> None:
    spec = MATVIEW_REFRESH_SPECS[0]
    session = FakeSession(unique_index_views=frozenset())
    result = await _refresh_one_matview(session, spec)
    assert result.status == "refreshed_full"
    assert result.used_concurrently is False
    assert session.refresh_statements() == [f"REFRESH MATERIALIZED VIEW {spec.qualified_name}"]


@pytest.mark.asyncio
async def test_a_failed_refresh_rolls_back_and_reports_failed() -> None:
    spec = MATVIEW_REFRESH_SPECS[0]
    session = FakeSession(fail_on=frozenset({spec.qualified_name}))
    result = await _refresh_one_matview(session, spec)
    assert result.status == "failed"
    assert result.detail == "OperationalError"
    assert session.rollbacks == 1
    assert session.commits == 0


# --- The context binding ---


def test_current_context_refuses_when_unbound() -> None:
    with pytest.raises(MatviewRefreshContextError):
        current_matview_refresh_context()


@pytest.mark.asyncio
async def test_handler_refuses_to_run_with_no_bound_context() -> None:
    with pytest.raises(MatviewRefreshContextError):
        await matview_refresh_handler(_fake_invocation())


# --- The handler: watermark-gated skip, budget yield + cursor resume, all-missing failure ---


@pytest.mark.asyncio
async def test_a_steady_state_tick_with_everything_fresh_skips_every_view(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(_FrozenDateTime, "fixed_now", now)
    monkeypatch.setattr(mv_module, "datetime", _FrozenDateTime)
    session = FakeSession(prior_state_rows=_fresh_prior_state_rows(now))

    async with matview_refresh_context(MatviewRefreshLaneContext(session=session)):
        outcome = await matview_refresh_handler(_fake_invocation())

    assert outcome.kind == "completed"
    assert session.refresh_statements() == []
    views = outcome.metrics["views"]
    assert len(views) == 11
    assert all(entry["status"] == "skipped_unchanged" for entry in views)


@pytest.mark.asyncio
async def test_a_lane_landing_data_makes_exactly_the_matching_view_eligible(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the one view sharing the changed watermark's source relation refreshes; the rest stay fresh."""
    now = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(_FrozenDateTime, "fixed_now", now)
    monkeypatch.setattr(mv_module, "datetime", _FrozenDateTime)

    forecast_name = "agri.mv_forecast_ml_daily_serving"
    forecast_spec = next(s for s in MATVIEW_REFRESH_SPECS if s.qualified_name == forecast_name)
    # Fresh enough that every OTHER view (unchanged watermark) stays under its own max_staleness --
    # the tightest is mv_layer_hourly_activity's 3,600s -- and stale enough, for the forecast view
    # alone, to clear ITS min_interval_seconds so a changed watermark is not rate-limited.
    fresh_refreshed_at = now - timedelta(minutes=5)
    stale_enough_for_forecast = now - timedelta(seconds=forecast_spec.min_interval_seconds + 60)
    prior_rows = [
        {
            "view_name": spec.qualified_name,
            "source_watermark": _watermark_signature(_default_watermark_row_for(spec)),
            "refreshed_at": stale_enough_for_forecast if spec.qualified_name == forecast_name else fresh_refreshed_at,
            "duration_ms": 500,
            "row_count": 10,
            "outcome": "refreshed_concurrently",
        }
        for spec in MATVIEW_REFRESH_SPECS
    ]
    session = FakeSession(
        prior_state_rows=prior_rows,
        watermark_overrides={
            "matview_refresh_watermark_forecast_publication": {"watermark": "2026-08-14T00:00:00+00:00"}
        },
    )

    async with matview_refresh_context(MatviewRefreshLaneContext(session=session)):
        outcome = await matview_refresh_handler(_fake_invocation())

    assert outcome.kind == "completed"
    assert session.refresh_statements() == [f"REFRESH MATERIALIZED VIEW CONCURRENTLY {forecast_name}"]


@pytest.mark.asyncio
async def test_budget_exhaustion_yields_with_a_cursor_naming_what_finished_this_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock(start=0.0, step=10.0)
    monkeypatch.setattr(mv_module.time, "monotonic", clock.monotonic)
    session = FakeSession()  # no prior state at all: every view is eligible regardless of "now"

    async with matview_refresh_context(MatviewRefreshLaneContext(session=session)):
        outcome = await matview_refresh_handler(_fake_invocation(seconds_remaining=100.0))

    assert outcome.kind == "yielded"
    assert outcome.resume_at is None
    # The three priority-0 (cheap) views finish before the 10s-per-call clock exhausts the 100s budget;
    # the first priority-1 view (estimate 20s, needs 40s of headroom) is the one that trips the gate.
    assert outcome.cursor == {
        "completed_views": [
            "geo.mv_layer_feature_stats",
            "geo.mv_layer_hourly_activity",
            "geo.mv_drought_release_index",
        ]
    }
    completed = {entry["view"] for entry in outcome.metrics["views"] if entry["status"] == "refreshed_concurrently"}
    assert completed == set(outcome.cursor["completed_views"])
    budget_skipped = {entry["view"] for entry in outcome.metrics["views"] if entry["status"] == "skipped_budget"}
    assert budget_skipped == MATVIEW_REFRESH_QUALIFIED_NAMES - completed


@pytest.mark.asyncio
async def test_resuming_from_a_cursor_skips_the_views_already_completed_this_run() -> None:
    already_completed = ["geo.mv_layer_feature_stats", "geo.mv_layer_hourly_activity", "geo.mv_drought_release_index"]
    session = FakeSession()  # no prior state: every other view is still eligible regardless of "now"

    async with matview_refresh_context(MatviewRefreshLaneContext(session=session)):
        outcome = await matview_refresh_handler(
            _fake_invocation(seconds_remaining=1_000.0, cursor={"completed_views": already_completed})
        )

    assert outcome.kind == "completed"
    refreshed_views = {sql.rsplit(" ", 1)[-1] for sql in session.refresh_statements()}
    assert refreshed_views == MATVIEW_REFRESH_QUALIFIED_NAMES - set(already_completed)


@pytest.mark.asyncio
async def test_all_attempted_views_missing_fails_the_tick() -> None:
    session = FakeSession(existing_views=frozenset())

    async with matview_refresh_context(MatviewRefreshLaneContext(session=session)):
        outcome = await matview_refresh_handler(_fake_invocation())

    assert outcome.kind == "failed"
    assert outcome.failure_class == "matview_refresh_failed"


@pytest.mark.asyncio
async def test_one_failed_view_fails_the_tick_without_blocking_the_others() -> None:
    failing = "geo.mv_layer_feature_stats"
    session = FakeSession(fail_on=frozenset({failing}))

    async with matview_refresh_context(MatviewRefreshLaneContext(session=session)):
        outcome = await matview_refresh_handler(_fake_invocation())

    assert outcome.kind == "failed"
    assert failing in (outcome.reason or "")
    statuses = {entry["view"]: entry["status"] for entry in outcome.metrics["views"]}
    assert statuses[failing] == "failed"
    assert statuses["geo.mv_layer_hourly_activity"] == "refreshed_concurrently"


# --- trigger_matview_refresh: opens the ONE persistent run, never a bucketed one ---


@pytest.mark.asyncio
async def test_trigger_reuses_the_same_persistent_run_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two ticks minutes apart must open the SAME run -- unlike strategy_mv_refresh's bucketed key."""
    seen_run_keys: list[str] = []

    async def _fake_ensure_job_definition(session: object, spec: Any) -> Any:
        del session
        return SimpleNamespace(
            id="definition-id",
            name=spec.name,
            version=spec.version,
            handler=spec.handler,
            queue_name=spec.queue_name,
            concurrency_key=None,
            max_attempts=spec.max_attempts,
            lease_seconds=spec.lease_seconds,
            time_budget_seconds=spec.time_budget_seconds,
            retry_policy=spec.retry_policy,
            parameters=spec.parameters,
        )

    async def _fake_open_job_run(
        session: object, definition: object, *, logical_run_key: str, **kwargs: object
    ) -> Any:
        del session, definition, kwargs
        seen_run_keys.append(logical_run_key)
        return SimpleNamespace(job_run_id="run-id", logical_run_key=logical_run_key, created=True)

    async def _fake_run_job_slice(session: object, **kwargs: object) -> str:
        del session, kwargs
        return "sliced"

    monkeypatch.setattr(mv_module, "ensure_job_definition", _fake_ensure_job_definition)
    monkeypatch.setattr(mv_module, "open_job_run", _fake_open_job_run)
    monkeypatch.setattr(mv_module, "run_job_slice", _fake_run_job_slice)

    session = FakeSession()
    await trigger_matview_refresh(session, requested_by="test", now=datetime(2026, 8, 15, 1, 0, 0, tzinfo=UTC))
    await trigger_matview_refresh(session, requested_by="test", now=datetime(2026, 8, 15, 2, 0, 0, tzinfo=UTC))

    assert seen_run_keys == [MATVIEW_REFRESH_RUN_KEY, MATVIEW_REFRESH_RUN_KEY]
