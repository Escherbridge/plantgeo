"""Static contract checks for the 0011 signal-evaluation revision (spec section 9)."""

from pathlib import Path

_ARCHIVE_DIR = Path(__file__).parents[1] / "alembic" / "archive"
_REVOKED_FUNCTION_COUNT = 2


def _migration() -> str:
    matches = sorted(_ARCHIVE_DIR.glob("20260725_0011_*.py"))
    assert len(matches) == 1, f"expected exactly one 20260725_0011 migration file, found {matches}"
    return matches[0].read_text(encoding="utf-8")


def test_revision_is_forward_only_and_chained_to_0010() -> None:
    migration = _migration()

    assert 'revision = "20260725_0011"' in migration
    assert 'down_revision = "20260723_0010"' in migration


def test_upgrade_forward_loads_both_functions_from_the_declarative_tree() -> None:
    migration = _migration()

    assert "load_object_sql" in migration
    assert '"functions/forecast_iteration_evaluation.sql"' in migration
    assert '"functions/drought_class_daily_series.sql"' in migration
    # forward-load workflow (db/AGENTS.md): DDL body lives under db/agri/, never inlined here
    assert "CREATE FUNCTION agri.forecast_iteration_evaluation(" not in migration
    assert "CREATE FUNCTION agri.drought_class_daily_series(" not in migration
    assert "CREATE TABLE" not in migration
    assert "CREATE VIEW" not in migration


def test_both_functions_are_revoked_from_public_with_no_serving_grant() -> None:
    migration = _migration()

    assert "REVOKE EXECUTE ON FUNCTION agri.forecast_iteration_evaluation(" in migration
    assert "REVOKE EXECUTE ON FUNCTION agri.drought_class_daily_series(" in migration
    assert migration.count("FROM PUBLIC") >= _REVOKED_FUNCTION_COUNT
    # evaluation-only stays evaluation-only: no grant to any serving/publish role (spec section 9)
    assert "GRANT EXECUTE" not in migration
    for forbidden_role in (
        "plantgeo_forecast_reader",
        "plantgeo_forecast_writer",
        "plantgeo_forecast_publisher",
        "plantgeo_forecast_mv_refresher",
    ):
        assert forbidden_role not in migration


def test_downgrade_is_unimplemented_and_non_destructive() -> None:
    migration = _migration()

    assert "raise NotImplementedError" in migration
    assert "DROP FUNCTION" not in migration
    assert "DROP TABLE" not in migration
    assert "DROP VIEW" not in migration
