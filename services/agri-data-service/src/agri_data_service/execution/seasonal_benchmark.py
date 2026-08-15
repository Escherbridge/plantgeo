"""Score the pre-registered candidate ladder over a frozen export and apply the pre-registered gates.

This module opens no database session at all. Its only input is a verified export directory; its only
output is a results document. That is the structural reason a scoring run cannot alter a governed row.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Final

import numpy as np

from agri_data_service.execution.seasonal_evaluation_export import (
    load_observations_csv,
    read_manifest,
    verify_export,
)
from agri_data_service.method.ml.seasonal_candidates import (
    DailySeries,
    build_daily_series,
    default_candidates,
)
from agri_data_service.method.ml.seasonal_evaluation import (
    BOOTSTRAP_DRAW_COUNT,
    BOOTSTRAP_SEED,
    HORIZON_STEPS,
    MINIMUM_HISTORY_DAYS,
    NOMINAL_INTERVAL_COVERAGE,
    ORIGIN_STRIDE_DAYS,
    AbstentionCheck,
    BootstrapInterval,
    CandidateRun,
    MetricSummary,
    RollingOriginPlan,
    ScoredOrigin,
    bootstrap_skill_interval,
    build_rolling_origin_plan,
    evaluate_abstention,
    pass_fraction,
    run_candidate,
    skill_versus_persistence,
    slice_by_season,
    summarize,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

# Pre-registered in conductor/tracks/seasonal_forecast_feedback_20260726/preregistration-2026-08-14.md.
TARGET_SIGNAL_NAMES: Final[tuple[str, ...]] = ("wind_speed", "air_temperature_mean")
TARGET_SUPPORT_KEY: Final = "surface"
HOLDOUT_BOUNDARY: Final = date(2025, 8, 6)
PERSISTENCE_CANDIDATE_NAME: Final = "persistence_v1"

COVERAGE_TOLERANCE: Final = 0.10
SEASONAL_SKILL_FLOOR: Final = -0.10


class SeasonalBenchmarkError(RuntimeError):
    """The frozen export cannot support the pre-registered evaluation."""


@dataclass(frozen=True)
class SeriesResult:
    """One candidate's result on one target series."""

    series_key: str
    candidate_name: str
    development: MetricSummary
    final_holdout: MetricSummary
    development_skill: float | None
    final_holdout_skill: float | None
    final_holdout_skill_interval: BootstrapInterval | None
    final_holdout_pass_fraction: float | None
    seasonal_skill: tuple[tuple[str, float | None, int], ...]
    abstention: AbstentionCheck
    leakage_checked_calls: int
    skipped_origins: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CandidateDecision:
    """One accept/reject/abstain decision for a candidate family, with the gates that produced it."""

    candidate_name: str
    decision: str
    failed_gates: tuple[str, ...]
    pooled_final_holdout: MetricSummary
    pooled_skill: float | None
    pooled_skill_interval: BootstrapInterval | None
    pooled_coverage: float | None
    worst_seasonal_skill: tuple[str, float] | None


@dataclass(frozen=True)
class SignalGroupSummary:
    """Post-hoc diagnostic: the same metrics pooled within one signal, so units are not mixed.

    The pre-registered decision rule pools all eight target series, which mixes m/s with C and is
    therefore dominated by the temperature series. That is a defect of the pre-registration, not a
    finding, and it is fixed by reporting rather than by changing the rule after seeing the numbers.
    """

    signal_name: str
    candidate_name: str
    summary: MetricSummary
    skill: float | None
    skill_interval: BootstrapInterval | None


@dataclass(frozen=True)
class BenchmarkResults:
    """Everything one benchmark run produced, ready to render or persist."""

    export_key: str
    manifest_checksum: str
    evaluated_at: datetime
    targets: tuple[str, ...]
    candidate_names: tuple[str, ...]
    origin_plan: RollingOriginPlan
    series_results: tuple[SeriesResult, ...]
    decisions: tuple[CandidateDecision, ...]
    signal_groups: tuple[SignalGroupSummary, ...]
    abstentions: tuple[str, ...]
    total_leakage_checked_calls: int
    scored_by_series_candidate: Mapping[tuple[str, str], tuple[ScoredOrigin, ...]]


def build_target_series(export_dir: Path) -> tuple[DailySeries, ...]:
    """Lay the frozen export's target signals onto dense daily grids, one per (cell, signal)."""
    observations = load_observations_csv(export_dir)
    grouped: dict[tuple[str, str, str], dict[date, float]] = {}
    units: dict[tuple[str, str, str], str] = {}
    for observation in observations:
        if observation.signal_name not in TARGET_SIGNAL_NAMES or observation.support_key != TARGET_SUPPORT_KEY:
            continue
        if observation.normalized_value is None or not observation.is_observed:
            continue
        key = (observation.cell_key, observation.signal_name, observation.support_key)
        grouped.setdefault(key, {})[observation.observed_date] = observation.normalized_value
        units[key] = observation.normalized_unit
    if not grouped:
        raise SeasonalBenchmarkError("the frozen export holds none of the pre-registered target signals")
    return tuple(
        build_daily_series(f"{cell}|{signal}|{support}", units[(cell, signal, support)], values)
        for (cell, signal, support), values in sorted(grouped.items())
    )


def _seasonal_skill(
    candidate: Sequence[ScoredOrigin],
    persistence: Sequence[ScoredOrigin],
) -> tuple[tuple[str, float | None, int], ...]:
    candidate_seasons = slice_by_season(candidate)
    persistence_seasons = slice_by_season(persistence)
    rows: list[tuple[str, float | None, int]] = []
    for season in sorted(set(candidate_seasons) | set(persistence_seasons)):
        candidate_slice = candidate_seasons.get(season, ())
        persistence_slice = persistence_seasons.get(season, ())
        summary = summarize(candidate_slice)
        rows.append(
            (
                season,
                skill_versus_persistence(summary, summarize(persistence_slice)),
                summary.scored_day_count,
            )
        )
    return tuple(rows)


def evaluate_series(
    series: DailySeries,
    plan: RollingOriginPlan,
    runs: dict[str, CandidateRun],
) -> tuple[SeriesResult, ...]:
    """Turn one series' candidate runs into per-candidate results against the persistence baseline."""
    baseline = runs[PERSISTENCE_CANDIDATE_NAME]
    results: list[SeriesResult] = []
    for candidate_name, run in runs.items():
        development = summarize(run.by_fold("development"))
        holdout = summarize(run.by_fold("final_holdout"))
        results.append(
            SeriesResult(
                series_key=series.series_key,
                candidate_name=candidate_name,
                development=development,
                final_holdout=holdout,
                development_skill=skill_versus_persistence(development, summarize(baseline.by_fold("development"))),
                final_holdout_skill=skill_versus_persistence(holdout, summarize(baseline.by_fold("final_holdout"))),
                final_holdout_skill_interval=bootstrap_skill_interval(
                    run.by_fold("final_holdout"), baseline.by_fold("final_holdout")
                ),
                final_holdout_pass_fraction=pass_fraction(
                    run.by_fold("final_holdout"), baseline.by_fold("final_holdout")
                ),
                seasonal_skill=_seasonal_skill(run.by_fold("final_holdout"), baseline.by_fold("final_holdout")),
                abstention=evaluate_abstention(run, plan),
                leakage_checked_calls=run.leakage_checked_calls,
                skipped_origins=tuple((origin.isoformat(), reason) for origin, reason in run.skipped_origins),
            )
        )
    return tuple(results)


def _decide(
    candidate_name: str,
    pooled: Sequence[ScoredOrigin],
    pooled_baseline: Sequence[ScoredOrigin],
    abstained: bool,
) -> CandidateDecision:
    summary = summarize(pooled)
    skill = skill_versus_persistence(summary, summarize(pooled_baseline))
    interval = bootstrap_skill_interval(pooled, pooled_baseline)
    seasonal = _seasonal_skill(pooled, pooled_baseline)
    scored_seasons = [(season, value) for season, value, count in seasonal if value is not None and count > 0]
    worst = min(scored_seasons, key=lambda row: row[1]) if scored_seasons else None

    failed: list[str] = []
    if candidate_name == PERSISTENCE_CANDIDATE_NAME:
        return CandidateDecision(
            candidate_name=candidate_name,
            decision="baseline",
            failed_gates=(),
            pooled_final_holdout=summary,
            pooled_skill=skill,
            pooled_skill_interval=interval,
            pooled_coverage=summary.interval_coverage,
            worst_seasonal_skill=worst,
        )
    if skill is None or skill <= 0.0:
        failed.append(f"gate1_skill_not_positive(skill={skill})")
    if interval is None:
        failed.append("gate1_no_bootstrap_interval")
    elif interval.lower <= 0.0:
        failed.append(f"gate1_skill_interval_lower={interval.lower:.4f}<=0")
    coverage = summary.interval_coverage
    if coverage is None:
        failed.append("gate2_no_interval_produced")
    elif not (
        NOMINAL_INTERVAL_COVERAGE - COVERAGE_TOLERANCE <= coverage <= NOMINAL_INTERVAL_COVERAGE + COVERAGE_TOLERANCE
    ):
        failed.append(f"gate2_interval_coverage={coverage:.4f}_outside_0.70_0.90")
    if worst is not None and worst[1] < SEASONAL_SKILL_FLOOR:
        failed.append(f"gate3_seasonal_skill_{worst[0]}={worst[1]:.4f}<{SEASONAL_SKILL_FLOOR}")

    decision = "abstain" if abstained else ("accept" if not failed else "reject")
    return CandidateDecision(
        candidate_name=candidate_name,
        decision=decision,
        failed_gates=tuple(failed),
        pooled_final_holdout=summary,
        pooled_skill=skill,
        pooled_skill_interval=interval,
        pooled_coverage=coverage,
        worst_seasonal_skill=worst,
    )


def run_benchmark(export_dir: Path, *, evaluated_at: datetime | None = None) -> BenchmarkResults:
    """Verify the export, score every candidate at every pre-registered origin, and apply the gates."""
    verify_export(export_dir)
    manifest = read_manifest(export_dir)
    target_series = build_target_series(export_dir)
    candidates = default_candidates(horizon_steps=HORIZON_STEPS)
    names = tuple(candidate.name for candidate in candidates)
    if PERSISTENCE_CANDIDATE_NAME not in names:
        raise SeasonalBenchmarkError("the ladder must contain the persistence baseline the skill metric divides by")

    plan: RollingOriginPlan | None = None
    series_results: list[SeriesResult] = []
    pooled: dict[str, list[ScoredOrigin]] = {name: [] for name in names}
    abstained_names: set[str] = set()
    abstentions: list[str] = []
    leakage_calls = 0
    scored_by_series_candidate: dict[tuple[str, str], tuple[ScoredOrigin, ...]] = {}

    for series in target_series:
        observed = np.flatnonzero(~np.isnan(series.values))
        if observed.size == 0:
            raise SeasonalBenchmarkError(f"{series.series_key} carries no observation in the frozen export")
        series_plan = build_rolling_origin_plan(
            history_start=series.start_date + timedelta(days=int(observed[0])),
            last_observed=series.start_date + timedelta(days=int(observed[-1])),
            holdout_boundary=HOLDOUT_BOUNDARY,
            minimum_history_days=MINIMUM_HISTORY_DAYS,
            horizon_steps=HORIZON_STEPS,
            stride_days=ORIGIN_STRIDE_DAYS,
        )
        plan = plan or series_plan
        runs = {candidate.name: run_candidate(series, candidate, series_plan) for candidate in candidates}
        for result in evaluate_series(series, series_plan, runs):
            series_results.append(result)
            leakage_calls += result.leakage_checked_calls
            if result.abstention.must_abstain:
                abstained_names.add(result.candidate_name)
                abstentions.append(
                    f"{result.series_key}|{result.candidate_name}: {'; '.join(result.abstention.reasons)}"
                )
        for name in names:
            pooled[name].extend(runs[name].by_fold("final_holdout"))
            scored_by_series_candidate[(series.series_key, name)] = runs[name].scored_origins

    if plan is None:
        raise SeasonalBenchmarkError("no target series produced an origin plan")

    decisions = tuple(
        _decide(name, pooled[name], pooled[PERSISTENCE_CANDIDATE_NAME], name in abstained_names) for name in names
    )
    signal_groups: list[SignalGroupSummary] = []
    for signal_name in TARGET_SIGNAL_NAMES:
        baseline_slice = [
            scored for scored in pooled[PERSISTENCE_CANDIDATE_NAME] if scored.series_key.split("|")[1] == signal_name
        ]
        for name in names:
            candidate_slice = [scored for scored in pooled[name] if scored.series_key.split("|")[1] == signal_name]
            summary = summarize(candidate_slice)
            signal_groups.append(
                SignalGroupSummary(
                    signal_name=signal_name,
                    candidate_name=name,
                    summary=summary,
                    skill=skill_versus_persistence(summary, summarize(baseline_slice)),
                    skill_interval=bootstrap_skill_interval(candidate_slice, baseline_slice),
                )
            )
    return BenchmarkResults(
        export_key=manifest.export_key,
        manifest_checksum=manifest.manifest_checksum,
        evaluated_at=evaluated_at or datetime.fromisoformat("2026-08-14T00:00:00+00:00"),
        targets=tuple(series.series_key for series in target_series),
        candidate_names=names,
        origin_plan=plan,
        series_results=tuple(series_results),
        decisions=decisions,
        signal_groups=tuple(signal_groups),
        abstentions=tuple(abstentions),
        total_leakage_checked_calls=leakage_calls,
        scored_by_series_candidate=scored_by_series_candidate,
    )


def _metric_json(summary: MetricSummary) -> dict[str, object]:
    return {
        "origin_count": summary.origin_count,
        "scored_day_count": summary.scored_day_count,
        "mae": summary.mae,
        "rmse": summary.rmse,
        "bias": summary.bias,
        "mape": summary.mape,
        "mape_reason": summary.mape_reason,
        "interval_coverage": summary.interval_coverage,
    }


def _interval_json(interval: BootstrapInterval | None) -> dict[str, object] | None:
    if interval is None:
        return None
    return {
        "point": interval.point,
        "lower": interval.lower,
        "upper": interval.upper,
        "draw_count": interval.draw_count,
        "block_count": interval.block_count,
    }


def results_to_json(results: BenchmarkResults) -> str:
    """Render the whole run as canonical JSON, sorted and separator-pinned so it is diffable."""
    document = {
        "export_key": results.export_key,
        "manifest_checksum": results.manifest_checksum,
        "evaluated_at": results.evaluated_at.isoformat(),
        "bootstrap": {"draws": BOOTSTRAP_DRAW_COUNT, "seed": BOOTSTRAP_SEED},
        "origin_plan": {
            "history_start": results.origin_plan.history_start.isoformat(),
            "last_observed": results.origin_plan.last_observed.isoformat(),
            "minimum_history_days": results.origin_plan.minimum_history_days,
            "horizon_steps": results.origin_plan.horizon_steps,
            "stride_days": results.origin_plan.stride_days,
            "holdout_boundary": results.origin_plan.holdout_boundary.isoformat(),
            "origin_count": len(results.origin_plan.origins),
            "development_origin_count": len(results.origin_plan.by_fold("development")),
            "final_holdout_origin_count": len(results.origin_plan.by_fold("final_holdout")),
        },
        "targets": list(results.targets),
        "candidates": list(results.candidate_names),
        "leakage_checked_calls": results.total_leakage_checked_calls,
        "abstentions": list(results.abstentions),
        "series_results": [
            {
                "series_key": result.series_key,
                "candidate_name": result.candidate_name,
                "development": _metric_json(result.development),
                "final_holdout": _metric_json(result.final_holdout),
                "development_skill": result.development_skill,
                "final_holdout_skill": result.final_holdout_skill,
                "final_holdout_skill_interval": _interval_json(result.final_holdout_skill_interval),
                "final_holdout_pass_fraction": result.final_holdout_pass_fraction,
                "seasonal_skill": [
                    {"season": season, "skill": skill, "scored_day_count": count}
                    for season, skill, count in result.seasonal_skill
                ],
                "abstention_reasons": list(result.abstention.reasons),
                "skipped_origins": [{"origin": origin, "reason": reason} for origin, reason in result.skipped_origins],
            }
            for result in results.series_results
        ],
        "signal_groups_post_hoc_diagnostic": [
            {
                "signal_name": group.signal_name,
                "candidate_name": group.candidate_name,
                "final_holdout": _metric_json(group.summary),
                "skill": group.skill,
                "skill_interval": _interval_json(group.skill_interval),
            }
            for group in results.signal_groups
        ],
        "decisions": [
            {
                "candidate_name": decision.candidate_name,
                "decision": decision.decision,
                "failed_gates": list(decision.failed_gates),
                "pooled_final_holdout": _metric_json(decision.pooled_final_holdout),
                "pooled_skill": decision.pooled_skill,
                "pooled_skill_interval": _interval_json(decision.pooled_skill_interval),
                "pooled_interval_coverage": decision.pooled_coverage,
                "worst_seasonal_skill": (
                    None
                    if decision.worst_seasonal_skill is None
                    else {"season": decision.worst_seasonal_skill[0], "skill": decision.worst_seasonal_skill[1]}
                ),
            }
            for decision in results.decisions
        ],
    }
    return json.dumps(document, sort_keys=True, indent=2) + "\n"


def write_benchmark_results(output_dir: Path, results: BenchmarkResults) -> Path:
    """Write the canonical results JSON beside its rendered table."""
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "results.json"
    target.write_text(results_to_json(results), encoding="utf-8", newline="")
    return target


def _format(value: float | None, digits: int = 4) -> str:
    return "-" if value is None or math.isnan(value) else f"{value:.{digits}f}"


def render_results_markdown(results: BenchmarkResults) -> str:
    """Render the candidate-versus-baseline table the decision record quotes."""
    lines: list[str] = [
        "| candidate | fold | origins | scored days | MAE | RMSE | bias | MAPE % | coverage "
        "| skill vs persistence | skill 95% CI |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for decision in results.decisions:
        summary = decision.pooled_final_holdout
        interval = decision.pooled_skill_interval
        lines.append(
            "| "
            + " | ".join(
                (
                    f"`{decision.candidate_name}`",
                    "final holdout (pooled)",
                    str(summary.origin_count),
                    str(summary.scored_day_count),
                    _format(summary.mae),
                    _format(summary.rmse),
                    _format(summary.bias),
                    _format(summary.mape, 2) if summary.mape is not None else f"n/a ({summary.mape_reason})",
                    _format(summary.interval_coverage, 3),
                    _format(decision.pooled_skill),
                    "-" if interval is None else f"[{interval.lower:.4f}, {interval.upper:.4f}]",
                )
            )
            + " |"
        )
    decisions = "\n".join(
        f"- `{decision.candidate_name}`: **{decision.decision}**"
        + (f" - failed {', '.join(decision.failed_gates)}" if decision.failed_gates else "")
        for decision in results.decisions
    )
    group_lines: list[str] = [
        "| signal | candidate | unit | origins | scored days | MAE | RMSE | bias | coverage | skill | skill 95% CI |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    units = {"wind_speed": "m/s", "air_temperature_mean": "C"}
    for group in results.signal_groups:
        interval = group.skill_interval
        group_lines.append(
            "| "
            + " | ".join(
                (
                    group.signal_name,
                    f"`{group.candidate_name}`",
                    units.get(group.signal_name, "?"),
                    str(group.summary.origin_count),
                    str(group.summary.scored_day_count),
                    _format(group.summary.mae),
                    _format(group.summary.rmse),
                    _format(group.summary.bias),
                    _format(group.summary.interval_coverage, 3),
                    _format(group.skill),
                    "-" if interval is None else f"[{interval.lower:.4f}, {interval.upper:.4f}]",
                )
            )
            + " |"
        )
    return (
        f"Export `{results.export_key}`, manifest `{results.manifest_checksum}`.\n\n"
        "### Pre-registered decision table (pooled over all eight target series)\n\n"
        + "\n".join(lines)
        + "\n\nThe pooled MAE mixes m/s with C and is therefore dominated by the temperature series. "
        + "That is a defect of the pre-registration, kept rather than rewritten after the fact; the "
        + "per-signal table below is the honest diagnostic and is explicitly **not** decision-bearing.\n\n"
        + decisions
        + "\n\n### Post-hoc per-signal diagnostic (not decision-bearing)\n\n"
        + "\n".join(group_lines)
        + "\n"
    )
