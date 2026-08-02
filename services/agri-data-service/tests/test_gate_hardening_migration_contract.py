"""Static contracts for revision 20260801_0014 (knowledge pin + gate hardening)."""

from pathlib import Path

SERVICE_ROOT = Path(__file__).parents[1]
MIGRATION = SERVICE_ROOT / "alembic" / "versions" / "20260801_0014_hindcast_knowledge_pin_and_gate_hardening.py"
FUNCTION_ROOT = SERVICE_ROOT / "db" / "agri" / "functions"
TABLE_ROOT = SERVICE_ROOT / "db" / "agri" / "tables"
NULL_GUARDED_FINALIZERS = (
    "finalize_forecast_hindcast_run.sql",
    "finalize_forecast_receipt.sql",
    "finalize_strategy_label_release.sql",
    "finalize_strategy_selection_receipt.sql",
)
EXPECTED_HINDCAST_CLOCK_READS = 3
CHECKSUM_GUC_PINS = (
    "SET \"TimeZone\" TO 'UTC'",
    "SET \"DateStyle\" TO 'ISO, MDY'",
    "SET extra_float_digits TO '1'",
)


def _migration() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _function(name: str) -> str:
    return (FUNCTION_ROOT / name).read_text(encoding="utf-8")


def test_revision_is_additive_and_forward_only() -> None:
    migration = _migration()

    assert 'revision = "20260801_0014"' in migration
    assert 'down_revision = "20260725_0013"' in migration
    assert "raise NotImplementedError" in migration
    assert "DROP TABLE" not in migration
    assert "DROP COLUMN" not in migration
    assert "actual_knowledge_as_of" in migration
    assert "min_interval_coverage_fraction" in migration
    assert "audit_state" in migration


def test_revision_flags_rather_than_deletes_cutoff_violations() -> None:
    migration = _migration()

    assert "agri.strategy_selection_cutoff_violation(receipt.id)" in migration
    assert "audit_state = 'cutoff_violation'" in migration
    assert "DELETE FROM agri.strategy_selection_receipt" not in migration


def test_every_finalizer_rejects_a_null_expected_checksum() -> None:
    for name in NULL_GUARDED_FINALIZERS:
        body = _function(name)
        assert "IF p_expected_checksum IS NULL OR p_expected_checksum !~ '^[0-9a-f]{64}$' THEN" in body


def test_checksum_paths_pin_their_rendering_gucs() -> None:
    for name in (
        "forecast_hindcast_receipt_checksum.sql",
        "finalize_forecast_receipt.sql",
        "finalize_forecast_hindcast_run.sql",
        "finalize_strategy_selection_receipt.sql",
    ):
        body = _function(name)
        for pin in CHECKSUM_GUC_PINS:
            assert pin in body, f"{name} is missing {pin}"
    # The hindcast receipt preimage renders step_interval::text, so its
    # IntervalStyle must stay exactly what the v2 digest was computed under.
    assert "SET \"IntervalStyle\" TO 'iso_8601'" in _function("forecast_hindcast_receipt_checksum.sql")


def test_receipt_digest_dispatch_fails_closed_on_an_unknown_version() -> None:
    checksum = _function("forecast_hindcast_receipt_checksum.sql")
    finalizer = _function("finalize_forecast_hindcast_run.sql")
    policy_contract = _function("forecast_quality_policy_contract_v2.sql")

    assert "IF digest_version NOT IN ('hindcast_v1', 'hindcast_v2', 'hindcast_v3') THEN" in checksum
    assert "unsupported hindcast receipt digest version" in checksum
    assert "hindcast receipt preimage is undefined for digest version" in checksum
    assert "unsupported hindcast receipt digest version" in finalizer
    assert "'plantgeo-forecast-hindcast-receipt-v3'" in checksum
    # The v2 policy-contract literal is factored into one function the v3 branch calls,
    # so both the checksum and the shared read-model view use the exact same array.
    assert "agri.forecast_quality_policy_contract_v2(policy)" in checksum
    assert "'plantgeo-forecast-quality-policy-v2'" in policy_contract
    # The v1 and v2 preimages must stay byte-identical for already-finalized receipts.
    assert "'plantgeo-forecast-hindcast-receipt-v2'" in checksum
    assert "'plantgeo-forecast-quality-policy-v1'" in checksum


def test_hindcast_finalization_is_pinned_to_a_stored_knowledge_horizon() -> None:
    finalizer = _function("finalize_forecast_hindcast_run.sql")
    policy_guard = _function("enforce_forecast_hindcast_finalization_policy.sql")

    assert "coalesce(\n                target.actual_knowledge_as_of," in finalizer
    assert "actual_knowledge_as_of = knowledge_as_of" in finalizer
    assert finalizer.count("clock_timestamp()") == EXPECTED_HINDCAST_CLOCK_READS
    assert "NEW.actual_knowledge_as_of" in policy_guard
    assert "clock_timestamp()" not in policy_guard
    assert "hindcast finalization must pin its actual-knowledge horizon" in policy_guard


def test_coverage_is_horizon_completeness_and_the_interval_gate_is_wired() -> None:
    finalizer = _function("finalize_forecast_hindcast_run.sql")
    policy_guard = _function("enforce_forecast_hindcast_finalization_policy.sql")

    assert "generate_series(1, target.horizon_steps)" in finalizer
    assert "computed_coverage := available_step_count::double precision / target.horizon_steps;" in finalizer
    assert "computed_interval_coverage >= policy.min_interval_coverage_fraction" in finalizer
    assert "NEW.interval_coverage_fraction >= policy.min_interval_coverage_fraction" in policy_guard


def test_selection_cutoff_rule_is_canonical_and_no_longer_inverted() -> None:
    finalizer = _function("finalize_strategy_selection_receipt.sql")
    predicate = _function("strategy_selection_cutoff_violation.sql")

    assert "iteration.cutoff_time >= receipt.data_cutoff" not in finalizer
    assert "agri.strategy_selection_cutoff_violation(receipt.id)" in finalizer
    assert "iteration.cutoff_time > receipt.data_cutoff" in predicate
    assert "agri.strategy_selection_quality_evidence(receipt.id)" in finalizer
    assert "requires a finalized quality-passed hindcast" in finalizer
    assert "cannot be finalized" in finalizer


def test_quality_evidence_uses_the_outcome_view_join_keys() -> None:
    predicate = _function("strategy_selection_quality_evidence.sql")

    assert "agri.v_forecast_hindcast_outcome" in predicate
    assert "outcome.series_id = iteration.series_id" in predicate
    assert "outcome.model_id = run.model_id" in predicate
    assert "outcome.quality_passed" in predicate
    assert "outcome.simulated_cutoff_time <= receipt.data_cutoff" in predicate
    assert "outcome.signal_available_at <= receipt.issue_time" in predicate


def test_audit_flag_is_one_way_and_outside_the_receipt_preimage() -> None:
    guard = _function("guard_strategy_selection_receipt_change.sql")
    checksum = _function("strategy_selection_receipt_checksum.sql")
    table = (TABLE_ROOT / "strategy_selection_receipt.sql").read_text(encoding="utf-8")

    assert "OLD.audit_state <> 'clear'" in guard
    assert "NEW.audit_state <> 'cutoff_violation'" in guard
    assert "a finalized strategy selection accepts only a one-way cutoff_violation audit flag" in guard
    assert "audit_state" not in checksum
    assert "audit_state" in table
    assert "ck_strategy_selection_receipt_audit_state" in table
