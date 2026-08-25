"""The corrected real-history fixture stays fail-closed and identity-versioned."""

from pathlib import Path


def _fixture() -> str:
    return (Path(__file__).resolve().parents[3] / "infra" / "local-warehouse" / "first-metric-forecast.sql").read_text(
        encoding="utf-8"
    )


def test_corrected_fixture_requires_current_schema_and_new_governed_identities() -> None:
    fixture = _fixture()

    # The fixture used to gate itself on `alembic_version IN (...three ids...)`. That pin went stale
    # the moment 20260725_0011 landed and became permanently unsatisfiable when the 2026-08-25
    # greenfield collapse restamped the schema to 20260825_0000. It now preflights the OBJECTS it
    # calls -- the hindcast plane 20260803_0018 retired -- so it fails with an accurate reason on any
    # database instead of a wrong one on every database.
    # Narrow on purpose: the header comment above the fixture explains the retired pin and therefore
    # says the word. What must be gone is the READ, not the word.
    assert "FROM alembic_version" not in fixture, "a hard-coded revision pin is back in the fixture"
    assert "version_num" not in fixture
    assert "first metric forecast cannot run: this database has no %s." in fixture
    assert "to_regclass(required.object_name) IS NULL" in fixture
    assert "to_regprocedure(required.object_name) IS NULL" in fixture
    assert "('agri.forecast_hindcast_run'::text, 'relation'::text)" in fixture
    assert "The hindcast plane was retired by 20260803_0018" in fixture
    assert "issue_time := statement_timestamp()" in fixture
    assert "nasa-power-ws2m-denver-point-v2" in fixture
    assert "nasa-power-ws2m-denver-7d-baseline-v2" in fixture
    assert "'plantgeo-sql-elapsed-time-linear', 'v2'" in fixture
    assert "receipt_digest_version" in fixture
    assert "'hindcast_v2'" in fixture
    assert "nasa-power-ws2m-denver-point-v1" not in fixture
    assert "SELECT id INTO STRICT series_id" in fixture
    assert "source_series_id" in fixture
    assert "INSERT INTO agri.forecast_series" not in fixture


def test_corrected_fixture_cannot_publish_without_both_stricter_historical_gates() -> None:
    fixture = _fixture()

    pass_gate = "min_hindcast_pass_fraction constant double precision := 0.50"
    coverage_gate = "min_interval_coverage constant double precision := 0.70"
    rejection = "IF NOT gates_passed THEN"
    validation = "PERFORM agri.validate_forecast_run(forecast_run_id)"
    publication = "INSERT INTO agri.forecast_publication("

    assert pass_gate in fixture
    assert coverage_gate in fixture
    assert "hindcast_pass_count::double precision / hindcast_run_count" in fixture
    assert "hindcast_interval_coverage >= min_interval_coverage" in fixture
    assert fixture.index(rejection) < fixture.index(validation) < fixture.index(publication)
    assert "no publication created" in fixture
    assert "strategy_selection', false" in fixture
    assert "INSERT INTO agri.strategy_selection" not in fixture


def test_corrected_fixture_is_hard_wired_to_evaluation_only() -> None:
    fixture = _fixture()

    evaluation_mode = "evaluation_only constant boolean := true"
    publication_mode = "publication_authorized constant boolean := false"
    evaluation_guard = "IF evaluation_only THEN"
    publication_guard = "IF NOT publication_authorized THEN"
    evaluation_return = "validated_without_forecast_receipt_or_publication"
    validation = "PERFORM agri.validate_forecast_run(forecast_run_id)"
    receipt = "INSERT INTO agri.forecast_receipt("
    publication = "INSERT INTO agri.forecast_publication("

    assert evaluation_mode in fixture
    assert publication_mode in fixture
    assert "'publication_authorized', publication_authorized" in fixture
    assert fixture.index(evaluation_guard) < fixture.index(validation) < fixture.index(publication)
    assert fixture.index(publication_guard) < fixture.index(receipt) < fixture.index(publication)
    assert "forecast publication is not explicitly authorized" in fixture
    assert evaluation_return in fixture
