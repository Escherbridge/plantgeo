"""The G1 hold ladder's pure rules (Wave O GL-6 folded into f1-executor; spec §4.9.3; WQ-1, WQ-2).

`lane_incidents.py` decides, the executor writes. These are table-driven over the decisions themselves:
which ladder a hold climbs, when a probe may fire, when probation ends and how many attempts a watched
bucket gets. The flows that drive them through `run_executor_tick` are `test_hold_probes.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest

from agri_data_service.execution.job_executor_service import hold_ladders_from_environment
from agri_data_service.execution.lane_incidents import (
    CODE_PROBE_HOURS_VARIABLE,
    HoldLadders,
    HoldProgress,
    after_probe,
    judge_probation,
    parse_code_probe_hours,
    probe_is_due,
    watch_max_attempts,
)

ENTERED: Final = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)
#: The rung each settled-probe case starts from.
STARTING_RUNG: Final = 3


@pytest.mark.parametrize("exit_class", ["upstream", "infra"])
def test_upstream_and_infra_use_the_upstream_ladder(exit_class: str) -> None:
    ladders = HoldLadders()
    delays = [ladders.probe_delay(exit_class, rung) for rung in range(8)]
    assert delays == [timedelta(hours=hours) for hours in (1, 2, 4, 8, 16, 24, 24, 24)], "then daily"
    # A code hold of the same age climbs the longer code ladder instead.
    assert [ladders.probe_delay("code", rung) for rung in range(4)] == [
        timedelta(hours=hours) for hours in (6, 12, 24, 24)
    ]
    # One hour after entering rung 0 the upstream probe is due; the code probe is not.
    assert probe_is_due(
        ladders,
        exit_class=exit_class,
        rung=0,
        entered_rung_at=ENTERED,
        now=ENTERED + timedelta(hours=1),
        newer_bucket_exists=True,
    )
    assert not probe_is_due(
        ladders,
        exit_class="code",
        rung=0,
        entered_rung_at=ENTERED,
        now=ENTERED + timedelta(hours=1),
        newer_bucket_exists=True,
    )


@pytest.mark.parametrize(
    ("raw", "code_hours", "garbled"),
    [
        pytest.param(None, (6.0, 12.0, 24.0), False, id="unset-is-the-default"),
        pytest.param("", None, False, id="empty-is-operator-only"),
        pytest.param("   ", None, False, id="blank-is-operator-only"),
        pytest.param("6h,12h", None, True, id="garbled-is-operator-only"),
        pytest.param("0,6", None, True, id="non-positive-is-operator-only"),
        pytest.param("3, 9", (3.0, 9.0), False, id="a-custom-ladder"),
    ],
)
def test_code_probe_hours_empty_is_operator_only(
    raw: str | None, code_hours: tuple[float, ...] | None, garbled: bool
) -> None:
    assert parse_code_probe_hours(raw) == (code_hours, garbled)
    environment = {} if raw is None else {CODE_PROBE_HOURS_VARIABLE: raw}
    ladders = hold_ladders_from_environment(environment)
    long_after = ENTERED + timedelta(days=30)
    code_probes = probe_is_due(
        ladders, exit_class="code", rung=0, entered_rung_at=ENTERED, now=long_after, newer_bucket_exists=True
    )
    assert code_probes is (code_hours is not None), "operator-only code holds never probe, however long they wait"
    # The upstream ladder is independent of CODE_PROBE_HOURS.
    assert probe_is_due(
        ladders, exit_class="upstream", rung=0, entered_rung_at=ENTERED, now=long_after, newer_bucket_exists=True
    )


@pytest.mark.parametrize(
    ("clean_buckets", "age", "verdict"),
    [
        pytest.param(0, timedelta(hours=1), "continue", id="just-started"),
        pytest.param(1, timedelta(hours=47), "continue", id="one-clean-bucket-inside-48h"),
        pytest.param(2, timedelta(hours=1), "resolve", id="two-conclusive-clean-buckets"),
        pytest.param(0, timedelta(hours=48), "expired", id="48h-without-a-failure-or-proof"),
        pytest.param(2, timedelta(hours=48), "resolve", id="proof-beats-expiry"),
    ],
)
def test_probation_resolves_after_two_conclusive_or_48h(clean_buckets: int, age: timedelta, verdict: str) -> None:
    assert judge_probation(clean_buckets=clean_buckets, last_change_at=ENTERED, now=ENTERED + age) == verdict


@pytest.mark.parametrize(
    ("resolved_ago", "episodes_7d", "attempts"),
    [
        pytest.param(timedelta(hours=1), 1, 2, id="watched-after-one-episode"),
        pytest.param(timedelta(hours=1), 2, 1, id="flapping-chain-watches-with-one-attempt"),
        pytest.param(timedelta(hours=1), 3, 1, id="more-flapping-still-one"),
        pytest.param(timedelta(hours=24), 1, None, id="the-watch-ends-at-24h"),
    ],
)
def test_watch_attempts_drop_to_one_when_flapping(
    resolved_ago: timedelta, episodes_7d: int, attempts: int | None
) -> None:
    now = ENTERED + timedelta(days=2)
    assert watch_max_attempts(now - resolved_ago, episodes_7d=episodes_7d, now=now) == attempts
    assert watch_max_attempts(None, episodes_7d=episodes_7d, now=now) is None, "never resolved: no watch"


@pytest.mark.parametrize(
    ("outcome", "inconclusive_probes", "state", "rung", "reason"),
    [
        pytest.param("passed", 2, "probation", 3, None, id="a-clean-probe-starts-probation"),
        pytest.param("failed", 0, "held", 4, None, id="a-failed-probe-climbs-one-rung"),
        pytest.param("inconclusive", 0, "held:1", 3, None, id="a-lost-probe-keeps-the-rung"),
        pytest.param("inconclusive", 3, "held", 4, "lost_repeatedly", id="the-fourth-lost-probe-counts-as-failed"),
    ],
)
def test_a_settled_probe_moves_the_ladder(
    outcome: str, inconclusive_probes: int, state: str, rung: int, reason: str | None
) -> None:
    step = after_probe(outcome, rung=STARTING_RUNG, inconclusive_probes=inconclusive_probes)  # type: ignore[arg-type]
    assert (step.progress.encoded(), step.rung, step.reason) == (state, rung, reason)
    assert HoldProgress.parse(step.progress.encoded()) == step.progress, "the row's `state` round-trips"
    assert step.adopt_probe_class is (rung == STARTING_RUNG + 1), "only a counted failure replaces the hold's class"
