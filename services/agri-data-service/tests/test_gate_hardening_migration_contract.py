"""Static contracts for revision 20260801_0014 (knowledge pin + gate hardening).

Reduced to what survives revision ``20260803_0018``: that revision drops the
hindcast plane, all four ``finalize_*`` functions,
``strategy_selection_quality_evidence`` and ``guard_strategy_selection_receipt_change``,
so the assertions that read those canonical function bodies are gone with them.
The migration text is history and still checked in full; the surviving canonical
objects 0014 introduced -- ``strategy_selection_cutoff_violation``,
``forecast_quality_policy_contract_v2``, ``strategy_selection_receipt_checksum``
and the receipt table's one-way audit flag -- are still checked here.
"""

from pathlib import Path

SERVICE_ROOT = Path(__file__).parents[1]
MIGRATION = SERVICE_ROOT / "alembic" / "archive" / "20260801_0014_hindcast_knowledge_pin_and_gate_hardening.py"
FUNCTION_ROOT = SERVICE_ROOT / "db" / "agri" / "functions"
TABLE_ROOT = SERVICE_ROOT / "db" / "agri" / "tables"


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


def test_selection_cutoff_rule_is_canonical_and_no_longer_inverted() -> None:
    predicate = _function("strategy_selection_cutoff_violation.sql")

    assert "iteration.cutoff_time > receipt.data_cutoff" in predicate


def test_the_quality_policy_contract_literal_is_factored_into_one_function() -> None:
    policy_contract = _function("forecast_quality_policy_contract_v2.sql")

    assert "'plantgeo-forecast-quality-policy-v2'" in policy_contract


def test_audit_flag_is_outside_the_receipt_preimage() -> None:
    checksum = _function("strategy_selection_receipt_checksum.sql")
    table = (TABLE_ROOT / "strategy_selection_receipt.sql").read_text(encoding="utf-8")

    assert "audit_state" not in checksum
    assert "audit_state" in table
    assert "ck_strategy_selection_receipt_audit_state" in table
