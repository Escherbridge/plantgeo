"""The declarative job shapes: what each outcome case requires, how backoff grows, and how a token resolves."""

# ruff: noqa: PLR2004, ARG001

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from agri_data_service.jobs.registry import (
    DEFAULT_RETRY_POLICY,
    EMPTY_JSON_OBJECT,
    DuplicateJobHandlerError,
    JobDefinitionSpec,
    JobHandlerOutcome,
    JobHandlerRegistry,
    JobInvocation,
    JobSpecificationError,
    JobWorkItemSpec,
    RetryPolicy,
    UnknownJobHandlerError,
    job_handler,
)


async def _never_called_heartbeat() -> bool:
    """Stand in for the worker's heartbeat in a test that never invokes it."""
    return True


def _invocation(**overrides: object) -> JobInvocation:
    """Build a plausible invocation so a test can vary exactly one field."""
    fields: dict[str, object] = {
        "shard_key": "firms:2003-04-01",
        "kind": "history_window",
        "payload": EMPTY_JSON_OBJECT,
        "cursor": None,
        "parameters": EMPTY_JSON_OBJECT,
        "attempt_number": 1,
        "max_attempts": 5,
        "progress_fraction": 0.0,
        "seconds_remaining": 200.0,
        "heartbeat": _never_called_heartbeat,
    }
    fields.update(overrides)
    return JobInvocation(**fields)  # type: ignore[arg-type]


def test_a_progressed_outcome_without_a_cursor_is_refused_because_it_could_never_resume() -> None:
    with pytest.raises(JobSpecificationError, match="cursor"):
        JobHandlerOutcome(kind="progressed", progress_fraction=0.4)


def test_a_progressed_outcome_carries_the_cursor_its_next_slice_resumes_from() -> None:
    outcome = JobHandlerOutcome.progressed({"day": "2003-04-06"}, progress_fraction=0.25)
    assert outcome.kind == "progressed"
    assert outcome.cursor == {"day": "2003-04-06"}
    assert outcome.progress_fraction == 0.25


def test_a_completed_outcome_reports_full_progress_without_being_told() -> None:
    outcome = JobHandlerOutcome.completed()
    assert outcome.kind == "completed"
    assert outcome.progress_fraction == 1.0
    assert outcome.cursor is None


def test_a_failed_outcome_must_name_both_its_class_and_its_reason() -> None:
    with pytest.raises(JobSpecificationError, match="failure class"):
        JobHandlerOutcome(kind="failed", reason="upstream returned 503")
    with pytest.raises(JobSpecificationError, match="reason"):
        JobHandlerOutcome(kind="failed", failure_class="UpstreamHttpError")


def test_a_deferred_outcome_must_say_when_it_becomes_workable_again() -> None:
    with pytest.raises(JobSpecificationError, match="workable again"):
        JobHandlerOutcome(kind="deferred", reason="upstream publishes on Thursdays")


def test_a_deferred_outcome_refuses_a_naive_resume_time() -> None:
    with pytest.raises(JobSpecificationError, match="timezone"):
        JobHandlerOutcome.deferred(datetime(2026, 8, 8, 14, 0), "not published yet")  # noqa: DTZ001 - the case under test


def test_a_deferred_outcome_is_not_a_failure_and_carries_no_failure_class() -> None:
    resume_at = datetime(2026, 8, 13, 14, 0, tzinfo=UTC)
    outcome = JobHandlerOutcome.deferred(resume_at, "USDM publishes on Thursdays")
    assert outcome.kind == "deferred"
    assert outcome.resume_at == resume_at
    assert outcome.failure_class is None


def test_a_deferred_outcome_may_carry_the_cursor_the_chunks_it_already_walked_resume_from() -> None:
    outcome = JobHandlerOutcome.deferred(
        datetime(2026, 8, 8, 6, 0, tzinfo=UTC),
        "upstream said come back at 06:00",
        cursor={"chunk": 5},
        progress_fraction=0.8,
        metrics={"chunks_walked": 4},
    )
    assert outcome.cursor == {"chunk": 5}
    assert outcome.metrics == {"chunks_walked": 4}


def test_a_yielded_outcome_is_not_a_failure_and_states_why_it_stopped_without_being_told() -> None:
    outcome = JobHandlerOutcome.yielded(cursor={"chunk": 3}, progress_fraction=0.6)
    assert outcome.kind == "yielded"
    assert outcome.failure_class is None
    assert outcome.cursor == {"chunk": 3}
    assert (outcome.reason or "").strip() != ""


def test_a_yielded_outcome_that_pins_a_resume_time_is_refused_because_that_is_a_deferral() -> None:
    # A yield's resume time is "the next tick" by definition; conflating the two hides which of the
    # clock and upstream actually stalled.
    with pytest.raises(JobSpecificationError, match="defer instead"):
        JobHandlerOutcome(kind="yielded", resume_at=datetime(2026, 8, 8, 6, 0, tzinfo=UTC))


def test_a_handler_can_weigh_its_next_step_against_the_budget_left_in_this_slice() -> None:
    invocation = _invocation(seconds_remaining=200.0)
    assert invocation.has_budget_for(199.9) is True
    assert invocation.has_budget_for(200.0) is True
    assert invocation.has_budget_for(200.1) is False
    assert _invocation(seconds_remaining=0.0).has_budget_for(1.0) is False


def test_a_progress_fraction_outside_zero_to_one_is_refused_before_it_reaches_the_check_constraint() -> None:
    with pytest.raises(JobSpecificationError, match="between 0 and 1"):
        JobHandlerOutcome.progressed({"day": "2003-04-06"}, progress_fraction=1.5)


def test_an_unknown_outcome_kind_is_refused_rather_than_silently_treated_as_a_failure() -> None:
    with pytest.raises(JobSpecificationError, match="unknown job outcome kind"):
        JobHandlerOutcome(kind="finished")  # type: ignore[arg-type]


def test_the_backoff_schedule_doubles_from_the_initial_delay_and_stops_at_the_ceiling() -> None:
    policy = RetryPolicy(initial_backoff_seconds=30.0, backoff_multiplier=2.0, maximum_backoff_seconds=300.0)
    schedule = [policy.backoff_seconds(attempt) for attempt in range(1, 7)]
    assert schedule == [30.0, 60.0, 120.0, 240.0, 300.0, 300.0]


def test_the_first_failure_waits_the_initial_delay_not_the_multiplied_one() -> None:
    assert DEFAULT_RETRY_POLICY.backoff_seconds(1) == 30.0


def test_a_backoff_for_attempt_zero_is_refused_because_attempt_numbers_start_at_one() -> None:
    with pytest.raises(JobSpecificationError, match="at least 1"):
        DEFAULT_RETRY_POLICY.backoff_seconds(0)


def test_a_shrinking_or_unbounded_retry_policy_is_refused() -> None:
    with pytest.raises(JobSpecificationError, match="backoff_multiplier"):
        RetryPolicy(backoff_multiplier=0.5)
    with pytest.raises(JobSpecificationError, match="maximum_backoff_seconds"):
        RetryPolicy(initial_backoff_seconds=600.0, maximum_backoff_seconds=60.0)


def test_a_stored_retry_policy_round_trips_through_jsonb() -> None:
    policy = RetryPolicy(initial_backoff_seconds=15.0, backoff_multiplier=3.0, maximum_backoff_seconds=900.0)
    assert RetryPolicy.from_json(policy.to_json()) == policy


def test_an_empty_or_absent_retry_policy_blob_falls_back_to_the_default() -> None:
    assert RetryPolicy.from_json(None) == DEFAULT_RETRY_POLICY
    assert RetryPolicy.from_json({}) == DEFAULT_RETRY_POLICY


def test_a_retry_policy_blob_holding_a_non_number_is_refused_rather_than_coerced() -> None:
    with pytest.raises(JobSpecificationError, match="must be a number"):
        RetryPolicy.from_json({"initial_backoff_seconds": "thirty"})


def test_a_definition_whose_lease_is_shorter_than_its_budget_is_refused_as_a_self_inflicted_fence_loss() -> None:
    with pytest.raises(JobSpecificationError, match="lease_seconds must exceed"):
        JobDefinitionSpec(
            name="agri.backfill.firms",
            version="1",
            handler="agri.backfill.firms",
            lease_seconds=120,
            time_budget_seconds=240,
        )


def test_a_definition_refuses_a_non_positive_attempt_or_lease_budget() -> None:
    with pytest.raises(JobSpecificationError, match="max_attempts"):
        JobDefinitionSpec(name="lane", version="1", handler="lane", max_attempts=0)
    with pytest.raises(JobSpecificationError, match="time_budget_seconds"):
        JobDefinitionSpec(name="lane", version="1", handler="lane", time_budget_seconds=0)


def test_a_definition_refuses_an_empty_name_version_or_handler_token() -> None:
    for field_name, values in (
        ("name", ("   ", "1", "lane")),
        ("version", ("lane", "  ", "lane")),
        ("handler", ("lane", "1", "")),
    ):
        with pytest.raises(JobSpecificationError, match=field_name):
            JobDefinitionSpec(name=values[0], version=values[1], handler=values[2])


def test_a_definition_that_fills_in_only_its_three_required_fields_inherits_the_table_defaults() -> None:
    spec = JobDefinitionSpec(name="agri.backfill.firms", version="2026-08-07", handler="agri.backfill.firms")
    assert (spec.queue_name, spec.max_attempts, spec.lease_seconds, spec.time_budget_seconds) == (
        "default",
        5,
        300,
        240,
    )
    assert spec.retry_policy == DEFAULT_RETRY_POLICY


def test_a_work_item_spec_refuses_an_empty_shard_key_because_it_is_the_idempotency_key() -> None:
    with pytest.raises(JobSpecificationError, match="shard_key"):
        JobWorkItemSpec(shard_key="  ", kind="history_window")


def test_a_work_item_spec_refuses_a_naive_availability_time() -> None:
    with pytest.raises(JobSpecificationError, match="timezone"):
        JobWorkItemSpec(
            shard_key="firms:2003-04-01",
            kind="history_window",
            available_at=datetime(2026, 8, 8),  # noqa: DTZ001 - the missing zone is the case under test.
        )


def test_an_unregistered_handler_token_names_what_is_registered_rather_than_returning_none() -> None:
    registry = JobHandlerRegistry()

    async def landed(invocation: JobInvocation) -> JobHandlerOutcome:
        return JobHandlerOutcome.completed()

    registry.register("agri.backfill.firms", landed)
    with pytest.raises(UnknownJobHandlerError, match=re.escape("agri.backfill.firms")):
        registry.handler_for("agri.backfill.usdm")


def test_an_empty_registry_says_so_instead_of_naming_nothing() -> None:
    with pytest.raises(UnknownJobHandlerError, match="registered tokens are nothing"):
        JobHandlerRegistry().handler_for("agri.backfill.firms")


def test_two_lanes_claiming_one_token_is_refused_so_the_winner_is_never_import_order_dependent() -> None:
    registry = JobHandlerRegistry()

    async def first(invocation: JobInvocation) -> JobHandlerOutcome:
        return JobHandlerOutcome.completed()

    async def second(invocation: JobInvocation) -> JobHandlerOutcome:
        return JobHandlerOutcome.completed()

    registry.register("agri.backfill.firms", first)
    with pytest.raises(DuplicateJobHandlerError, match="already registered"):
        registry.register("agri.backfill.firms", second)


def test_registering_the_same_callable_twice_is_a_no_op_so_a_re_imported_module_does_not_explode() -> None:
    registry = JobHandlerRegistry()

    async def landed(invocation: JobInvocation) -> JobHandlerOutcome:
        return JobHandlerOutcome.completed()

    registry.register("agri.backfill.firms", landed)
    registry.register("agri.backfill.firms", landed)
    assert registry.tokens() == ("agri.backfill.firms",)


def test_the_decorator_binds_a_token_at_import_time_and_returns_the_handler_unchanged() -> None:
    registry = JobHandlerRegistry()

    @job_handler("agri.backfill.usdm", registry=registry)
    async def landed(invocation: JobInvocation) -> JobHandlerOutcome:
        return JobHandlerOutcome.completed()

    assert "agri.backfill.usdm" in registry
    assert registry.handler_for("agri.backfill.usdm") is landed


async def test_a_registered_handler_is_called_with_the_invocation_and_returns_its_outcome() -> None:
    registry = JobHandlerRegistry()
    seen: list[str] = []

    @job_handler("agri.backfill.firms", registry=registry)
    async def landed(invocation: JobInvocation) -> JobHandlerOutcome:
        seen.append(invocation.shard_key)
        return JobHandlerOutcome.progressed({"day": "2003-04-06"}, progress_fraction=0.5)

    outcome = await registry.handler_for("agri.backfill.firms")(_invocation())
    assert seen == ["firms:2003-04-01"]
    assert outcome.cursor == {"day": "2003-04-06"}


def test_an_invocation_on_its_last_attempt_says_so_so_a_handler_can_choose_to_give_up_cleanly() -> None:
    assert _invocation(attempt_number=5, max_attempts=5).is_final_attempt is True
    assert _invocation(attempt_number=4, max_attempts=5).is_final_attempt is False
