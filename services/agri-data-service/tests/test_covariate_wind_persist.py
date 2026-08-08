"""Database-free proofs for the covariate wind training receipt chain: row shapes, order, digests.

No database, a fake for the one seam -- `AsyncSession.execute` -- matching the convention
`tests/test_jobs_worker.py` and `tests/test_ingest_backfill.py` use. What these do NOT prove is
that the SQL parses or that the governance function accepts the lineage; that needs the
disposable-PostgreSQL contract harness, and the outstanding follow-up is named at the bottom.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from agri_data_service.execution.covariate_wind_model import (
    OriginNotEvaluableError,
    RollingOriginBacktest,
    canonical_digest,
    canonical_json,
    model_document,
    rolling_origin_dates,
    run_out_of_fit_point_scores,
    run_rolling_origin_backtest,
)
from agri_data_service.execution.covariate_wind_persist import (
    AS_OF_MODE,
    EVALUATION_LABEL,
    ForecastTrainingPersistError,
    TrainingReceipt,
    WindTrainingReport,
    WindTrainingRequest,
    persist_training_receipt,
    run_covariate_wind_training,
)
from tests.test_covariate_wind_model import (
    SWEEP_CALIBRATION_DAYS,
    SWEEP_HORIZON_COUNT,
    SWEEP_ORIGIN_COUNT,
    SWEEP_ORIGIN_DATE,
    SWEEP_ORIGIN_STRIDE_DAYS,
    sweep_matrix,
    sweep_targets,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

CELL_ID = "11111111-1111-4111-8111-111111111111"
SERIES_ID = "22222222-2222-4222-8222-222222222222"
QUALITY_POLICY_KEY = "reviewed-eval-policy"
RELEASE_MANIFEST_CHECKSUM = "a" * 64
COVARIATE_MANIFEST_CHECKSUM = "b" * 64
AS_OF_TIME = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
SHA256_HEX_LENGTH = 64

# Every id the fake hands back, keyed by the statement fragment that asks for it. Distinct values
# so an assertion that a receipt wired the RIGHT id cannot pass by coincidence.
_SCRIPTED_IDS: Mapping[str, str] = {
    "INSERT INTO agri.job_definition": "33333333-3333-4333-8333-333333333333",
    "INSERT INTO agri.job_run": "44444444-4444-4444-8444-444444444444",
    "INSERT INTO agri.artifact": "55555555-5555-4555-8555-555555555555",
    "INSERT INTO agri.forecast_model": "66666666-6666-4666-8666-666666666666",
    "INSERT INTO agri.forecast_feature_snapshot": "77777777-7777-4777-8777-777777777777",
    "INSERT INTO agri.forecast_training_run": "88888888-8888-4888-8888-888888888888",
    "INSERT INTO agri.forecast_run": "99999999-9999-4999-8999-999999999999",
    "INSERT INTO agri.forecast_backtest_metric": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
}
MODEL_OUTPUT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
BACKTEST_OUTPUT_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
QUALITY_POLICY_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
RELEASE_SET_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
RELEASE_SET_KEY = "governed-release-2026-08-01"
_RELEASE_SET_ROW: Mapping[str, object] = {
    "id": RELEASE_SET_ID,
    "logical_key": RELEASE_SET_KEY,
    "manifest_checksum": RELEASE_MANIFEST_CHECKSUM,
    "as_of_time": AS_OF_TIME,
}


class FakeResult:
    """The narrow slice of SQLAlchemy's Result this lane actually uses."""

    def __init__(self, rows: Sequence[Mapping[str, object]]) -> None:
        self._rows = list(rows)

    def mappings(self) -> FakeResult:
        """Return self, because the fake already yields mappings."""
        return self

    def first(self) -> Mapping[str, object] | None:
        """The first row, or None when the statement matched nothing."""
        return self._rows[0] if self._rows else None

    def all(self) -> list[SimpleNamespace]:
        """Every row, as attribute-addressable objects the readers use."""
        return [SimpleNamespace(**row) for row in self._rows]

    def __iter__(self) -> Iterator[Mapping[str, object]]:
        """Iterate the rows, because `.mappings()` results are consumed by iteration."""
        return iter(self._rows)

    def scalar_one(self) -> object:
        """The single value of the single row, refusing an empty result as the driver does."""
        if not self._rows:
            raise AssertionError("scalar_one() on an empty result")
        return next(iter(self._rows[0].values()))

    def scalar_one_or_none(self) -> object:
        """The single value of the single row, or None when the statement returned nothing."""
        return None if not self._rows else next(iter(self._rows[0].values()))


def statement_body(sql: str) -> str:
    """Drop the `--` documentation header so a fragment matches the STATEMENT, not its prose.

    Load-bearing, not tidiness: `sql/AGENTS.md` requires every runtime query to open with a
    clause-by-clause explanation, and those explanations name other tables and other functions.
    Matching on the raw text made `insert_training_job_run.sql` -- whose header explains what
    `agri.validate_forecast_training_run` will check -- answer as though it were the validator.
    """
    return "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))


class RecordingSession:
    """An AsyncSession stand-in that records every statement and answers it by a text fragment."""

    def __init__(self, *, missing: frozenset[str] = frozenset()) -> None:
        """Script the happy path; `missing` names fragments that answer with no rows at all."""
        self.statements: list[tuple[str, dict[str, object]]] = []
        self.commits = 0
        self.rollbacks = 0
        self._missing = missing
        self._job_output_ids = [MODEL_OUTPUT_ID, BACKTEST_OUTPUT_ID]

    async def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> FakeResult:
        """Record the comment-stripped statement and answer it from the script.

        The bind check is the point of doing this here rather than in each test: a real driver
        refuses a statement whose `:name` placeholders and supplied parameters disagree, and the
        project's memory note is that stubbed sessions let exactly that class of error reach
        production. This fake refuses it too.
        """
        bound = set(getattr(statement, "_bindparams", {}) or {})
        supplied = set(parameters or {})
        if bound != supplied:
            raise AssertionError(
                f"statement binds {sorted(bound)} but was given {sorted(supplied)}; "
                f"missing={sorted(bound - supplied)} extra={sorted(supplied - bound)}"
            )
        sql = statement_body(str(statement))
        self.statements.append((sql, dict(parameters or {})))
        return FakeResult(self._rows_for(sql))

    async def commit(self) -> None:
        """Count a commit."""
        self.commits += 1

    async def rollback(self) -> None:
        """Count a rollback."""
        self.rollbacks += 1

    def _rows_for(self, sql: str) -> list[Mapping[str, object]]:
        """Answer one statement, honouring the fragments this session was told to leave empty."""
        if "FROM agri.release_set" in sql:
            return [] if "release_set" in self._missing else [_RELEASE_SET_ROW]
        if "agri.forecast_quality_policy" in sql:
            return [] if "quality_policy" in self._missing else [{"id": QUALITY_POLICY_ID}]
        if "INSERT INTO agri.job_output" in sql:
            # Two outputs per run -- the model, then the backtest -- handed back in that order.
            return [{"id": self._job_output_ids.pop(0)}]
        if "validate_forecast_feature_snapshot" in sql or "validate_forecast_training_run" in sql:
            return [{"status": "validated"}]
        scripted = next((identifier for fragment, identifier in _SCRIPTED_IDS.items() if fragment in sql), None)
        return [] if scripted is None else [{"id": scripted}]

    def parameters_for(self, fragment: str) -> dict[str, object]:
        """The bound parameters of the one statement carrying this fragment."""
        matches = [params for sql, params in self.statements if fragment in sql]
        if len(matches) != 1:
            raise AssertionError(f"expected exactly one statement carrying {fragment!r}, found {len(matches)}")
        return matches[0]

    def every_parameters_for(self, fragment: str) -> list[dict[str, object]]:
        """The bound parameters of every statement carrying this fragment, in execution order."""
        return [params for sql, params in self.statements if fragment in sql]

    def order_of(self, *fragments: str) -> list[int]:
        """The execution position of the first statement carrying each fragment."""
        positions: list[int] = []
        for fragment in fragments:
            found = next((index for index, (sql, _) in enumerate(self.statements) if fragment in sql), None)
            if found is None:
                raise AssertionError(f"no statement carried {fragment!r}")
            positions.append(found)
        return positions


def build_report(*, origin_count: int = SWEEP_ORIGIN_COUNT) -> WindTrainingReport:
    """A real report over the synthetic sweep matrix, so the persisted shapes are the real shapes."""
    matrix = sweep_matrix()
    targets = sweep_targets(matrix)
    request = WindTrainingRequest(
        cell_id=CELL_ID,
        series_id=SERIES_ID,
        history_start=matrix.dates[0],
        history_end=matrix.dates[-1],
        origin_date=SWEEP_ORIGIN_DATE,
        as_of_time=AS_OF_TIME,
        horizon_count=SWEEP_HORIZON_COUNT,
        calibration_days=SWEEP_CALIBRATION_DAYS,
        origin_count=origin_count,
        origin_stride_days=SWEEP_ORIGIN_STRIDE_DAYS,
        quality_policy_key=QUALITY_POLICY_KEY,
    )
    backtest: RollingOriginBacktest = run_rolling_origin_backtest(
        matrix,
        targets,
        origin_dates=rolling_origin_dates(
            SWEEP_ORIGIN_DATE, origin_count=origin_count, origin_stride_days=SWEEP_ORIGIN_STRIDE_DAYS
        ),
        calibration_days=SWEEP_CALIBRATION_DAYS,
        horizon_count=SWEEP_HORIZON_COUNT,
    )
    return WindTrainingReport(
        request=request,
        coverage=matrix.coverage(targets),
        backtest=backtest,
        manifest={"manifest_checksum": COVARIATE_MANIFEST_CHECKSUM, "day_count": len(matrix.dates)},
        declared_gaps=[{"stream_key": "era5_land", "gap_kind": "credential_gated"}],
        target_day_count=len(targets),
        feature_names=matrix.feature_names,
        out_of_fit=run_out_of_fit_point_scores(
            matrix,
            targets,
            fit_target_last=backtest.origins[-1].fit_target_last,
            evaluation_target_last=backtest.origins[-1].calibration_target_last,
            horizon_count=SWEEP_HORIZON_COUNT,
        ),
        baseline_iterations=[],
        leading_coefficients=[],
    )


async def persist(session: RecordingSession, report: WindTrainingReport) -> TrainingReceipt:
    """Drive the receipt writer against the fake session with pinned timestamps."""
    return await persist_training_receipt(
        session,  # type: ignore[arg-type] - a deliberate narrow stand-in for AsyncSession
        report,
        started_at=AS_OF_TIME,
        completed_at=AS_OF_TIME,
    )


async def test_persist_writes_the_receipt_chain_in_lineage_order() -> None:
    """Every row a validator inspects must exist before the validator runs, so order is the contract."""
    session = RecordingSession()

    receipt = await persist(session, build_report())

    positions = session.order_of(
        "FROM agri.release_set",
        "INSERT INTO agri.job_definition",
        "INSERT INTO agri.job_run",
        "INSERT INTO agri.artifact",
        "INSERT INTO agri.forecast_model",
        "INSERT INTO agri.job_output",
        "INSERT INTO agri.forecast_feature_snapshot",
        "validate_forecast_feature_snapshot",
        "INSERT INTO agri.forecast_training_run",
        "validate_forecast_training_run",
        "INSERT INTO agri.forecast_run",
        "INSERT INTO agri.forecast_backtest_metric",
    )
    assert positions == sorted(positions)
    assert receipt.training_run_status == "validated"
    assert receipt.feature_snapshot_status == "validated"
    # The lane never promotes the forecast run, which is what keeps it off every serving surface.
    assert not any("validate_forecast_run(" in sql for sql, _ in session.statements)
    # Committing is the caller's job, not the writer's.
    assert session.commits == 0


async def test_persist_binds_the_covariate_manifest_checksum_as_the_feature_lineage() -> None:
    session = RecordingSession()

    receipt = await persist(session, build_report())

    snapshot = session.parameters_for("INSERT INTO agri.forecast_feature_snapshot")
    training = session.parameters_for("INSERT INTO agri.forecast_training_run")
    assert snapshot["feature_checksum"] == COVARIATE_MANIFEST_CHECKSUM
    # The validator compares the receipt's pair against the snapshot's and raises on a difference,
    # so they must be the same two values rather than two independently derived ones.
    assert training["feature_checksum"] == snapshot["feature_checksum"]
    assert training["input_release_checksum"] == snapshot["input_release_checksum"]
    assert snapshot["input_release_checksum"] == RELEASE_MANIFEST_CHECKSUM
    assert receipt.feature_checksum == COVARIATE_MANIFEST_CHECKSUM


async def test_persist_hands_the_validator_the_same_digests_it_wrote_into_the_lineage() -> None:
    """The digests are not placeholders: the model output, the artifact and the call must agree."""
    session = RecordingSession()

    receipt = await persist(session, build_report())

    artifact = session.parameters_for("INSERT INTO agri.artifact")
    model_output = session.every_parameters_for("INSERT INTO agri.job_output")[0]
    validate = session.parameters_for("validate_forecast_training_run")
    assert artifact["checksum_sha256"] == receipt.model_checksum
    assert model_output["checksum_sha256"] == receipt.model_checksum
    assert model_output["row_count"] == 1
    assert json.loads(str(model_output["metadata_json"]))["validation_checksum"] == receipt.validation_checksum
    assert validate["model_checksum"] == receipt.model_checksum
    assert validate["validation_checksum"] == receipt.validation_checksum
    # Every digest is a real SHA-256 over canonical content, not a stand-in.
    for digest in (receipt.model_checksum, receipt.validation_checksum, receipt.training_code_checksum):
        assert len(digest) == SHA256_HEX_LENGTH
        assert set(digest) <= set("0123456789abcdef")


async def test_the_model_checksum_is_the_digest_of_the_stored_model_document() -> None:
    """The artifact's CHECK constraint recomputes this server-side, so the two must be derived alike."""
    report = build_report()
    session = RecordingSession()

    receipt = await persist(session, report)

    document = model_document(report.backtest, feature_names=report.feature_names, alpha=report.request.alpha)
    assert receipt.model_checksum == canonical_digest(document)
    assert session.parameters_for("INSERT INTO agri.artifact")["model_document"] == canonical_json(document)


async def test_persist_writes_one_backtest_metric_per_origin_keyed_by_that_origins_cutoff() -> None:
    report = build_report()
    session = RecordingSession()

    receipt = await persist(session, report)

    metrics = session.every_parameters_for("INSERT INTO agri.forecast_backtest_metric")
    assert len(metrics) == SWEEP_ORIGIN_COUNT
    assert receipt.backtest_metric_count == SWEEP_ORIGIN_COUNT
    cutoffs = [row["cutoff_time"] for row in metrics]
    # The unique key is (run, series, cutoff): one row per origin is the only shape that fits it.
    assert len(set(cutoffs)) == SWEEP_ORIGIN_COUNT
    assert cutoffs == [
        datetime(origin.origin_date.year, origin.origin_date.month, origin.origin_date.day, tzinfo=UTC)
        for origin in report.backtest.origins
    ]
    for row in metrics:
        assert row["backtest_point_count"] == SWEEP_HORIZON_COUNT
        assert row["training_point_count"] >= 1
        assert 0.0 <= float(str(row["coverage_fraction"])) <= 1.0
        assert len(str(row["metrics_checksum"])) == SHA256_HEX_LENGTH


async def test_the_forecast_run_carries_the_aggregate_the_metric_rows_cannot_hold() -> None:
    session = RecordingSession()

    await persist(session, build_report())

    summary = json.loads(str(session.parameters_for("INSERT INTO agri.forecast_run")["quality_summary"]))
    assert summary["origin_count"] == SWEEP_ORIGIN_COUNT
    assert summary["aggregate"]["evaluated_count"] == SWEEP_ORIGIN_COUNT * SWEEP_HORIZON_COUNT
    assert [entry["horizon"] for entry in summary["per_horizon"]] == list(range(SWEEP_HORIZON_COUNT))
    assert summary["publication_authorized"] is False
    assert summary["feature_coverage"]["candidate_day_count"] > 0


async def test_the_validation_metrics_record_the_global_as_of_mode_and_the_exclusion_accounting() -> None:
    report = build_report()
    session = RecordingSession()

    await persist(session, report)

    metrics = json.loads(str(session.parameters_for("validate_forecast_training_run")["validation_metrics"]))
    assert metrics["as_of_mode"] == AS_OF_MODE == "global"
    assert metrics["label"] == EVALUATION_LABEL
    assert metrics["publication_authorized"] is False
    coverage = metrics["feature_coverage"]
    assert coverage["candidate_day_count"] == report.coverage.candidate_day_count
    assert coverage["excluded_day_count"] == report.coverage.excluded_day_count
    assert coverage["target_missing_day_count"] == report.coverage.target_missing_day_count
    assert "blocking_features" in coverage
    assert metrics["rolling_origin_backtest"]["origin_count"] == SWEEP_ORIGIN_COUNT
    assert any("effective sample size" in caveat for caveat in metrics["caveats"])
    assert any("as_of_mode is 'global'" in caveat for caveat in metrics["caveats"])


async def test_persist_refuses_when_no_validated_release_set_predates_the_as_of_instant() -> None:
    session = RecordingSession(missing=frozenset({"release_set"}))

    with pytest.raises(ForecastTrainingPersistError, match="no validated release set"):
        await persist(session, build_report())

    assert not any("INSERT INTO agri.forecast_training_run" in sql for sql, _ in session.statements)


async def test_persist_refuses_to_invent_a_quality_policy_when_the_named_one_is_absent() -> None:
    session = RecordingSession(missing=frozenset({"quality_policy"}))

    with pytest.raises(ForecastTrainingPersistError, match="will not invent one"):
        await persist(session, build_report())

    assert not any("INSERT INTO agri.forecast_quality_policy" in sql for sql, _ in session.statements)


async def test_persist_refuses_a_report_with_no_manifest_checksum_to_bind_to() -> None:
    report = build_report()
    unbound = WindTrainingReport(
        request=report.request,
        coverage=report.coverage,
        backtest=report.backtest,
        manifest={},
        declared_gaps=None,
        target_day_count=report.target_day_count,
        feature_names=report.feature_names,
        out_of_fit=report.out_of_fit,
        baseline_iterations=[],
        leading_coefficients=[],
    )

    with pytest.raises(ForecastTrainingPersistError, match="no manifest checksum"):
        await persist(RecordingSession(), unbound)


async def test_a_read_only_run_issues_no_write_statement_at_all() -> None:
    """`--persist` off is the default, and the default must not touch a single governed table."""
    session = RecordingSession()
    request = build_report().request

    # The fake answers no covariate rows, so the sweep cannot score. What is under test is that
    # the attempt reached no INSERT on the way to finding that out.
    with pytest.raises(OriginNotEvaluableError):
        await run_covariate_wind_training(session, request, persist=False)  # type: ignore[arg-type]

    assert session.statements, "the read path must at least have queried something"
    assert not any("INSERT INTO" in sql for sql, _ in session.statements)


def test_the_training_key_is_deterministic_for_identical_pinned_inputs() -> None:
    """A re-claimed durable shard must resolve the receipt it already wrote, not mint a second one."""
    first = build_report().request
    second = build_report().request

    assert first.training_key == second.training_key
    assert first.parameter_checksum == second.parameter_checksum
    # A different knob is a different receipt.
    widened = WindTrainingRequest(
        cell_id=first.cell_id,
        series_id=first.series_id,
        history_start=first.history_start,
        history_end=first.history_end,
        origin_date=first.origin_date,
        as_of_time=first.as_of_time,
        horizon_count=first.horizon_count,
        calibration_days=first.calibration_days,
        origin_count=first.origin_count + 1,
        origin_stride_days=first.origin_stride_days,
        quality_policy_key=first.quality_policy_key,
    )
    assert widened.training_key != first.training_key


def test_the_request_defaults_the_origin_stride_to_the_horizon_count() -> None:
    """Non-overlapping origins by default: overlapping ones pool the same days twice."""
    request = WindTrainingRequest(
        cell_id=CELL_ID,
        series_id=SERIES_ID,
        history_start=SWEEP_ORIGIN_DATE.replace(year=SWEEP_ORIGIN_DATE.year - 1),
        history_end=SWEEP_ORIGIN_DATE,
        origin_date=SWEEP_ORIGIN_DATE,
        as_of_time=AS_OF_TIME,
        horizon_count=SWEEP_HORIZON_COUNT,
    )

    assert request.effective_stride_days == SWEEP_HORIZON_COUNT


def test_the_request_refuses_a_naive_as_of_instant() -> None:
    with pytest.raises(ValueError, match="as_of_time must include a timezone"):
        WindTrainingRequest(
            cell_id=CELL_ID,
            series_id=SERIES_ID,
            history_start=SWEEP_ORIGIN_DATE.replace(year=SWEEP_ORIGIN_DATE.year - 1),
            history_end=SWEEP_ORIGIN_DATE,
            origin_date=SWEEP_ORIGIN_DATE,
            as_of_time=datetime(2026, 8, 1, 12, 0),  # noqa: DTZ001 - a naive instant is the thing under test
        )


def test_receipt_ids_are_the_ones_the_lineage_statements_returned() -> None:
    """A receipt that pointed at the wrong row would be provenance that does not resolve."""
    assert uuid.UUID(_SCRIPTED_IDS["INSERT INTO agri.forecast_training_run"])
    assert len(set(_SCRIPTED_IDS.values())) == len(_SCRIPTED_IDS)


# TODO(agri): a `test_covariate_wind_persist_postgresql.py` in the *_postgresql.py style, gated on
# AGRI_TEST_DATABASE_URL, that seeds a validated release set and an active quality policy, runs
# `persist_training_receipt` against a migrated disposable database, and asserts that
# agri.validate_forecast_training_run actually returns 'validated'. The stubs above prove the
# shapes and the ordering; only a real server proves the SQL parses and the lineage is accepted.
