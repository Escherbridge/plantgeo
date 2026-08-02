"""Pin the hindcast knowledge horizon and make the evaluation gates able to fail.

Revision ID: 20260801_0014
Revises: 20260725_0013

Five governance defects are closed here; every one of them either made an
evaluation gate vacuous or made a checksummed receipt non-reproducible.

1. ``finalize_forecast_hindcast_run`` read actuals at ``clock_timestamp()``, so
   re-verifying a valid receipt could fail later and an audit could never be
   replayed. ``forecast_hindcast_run.actual_knowledge_as_of`` now stores that
   horizon once, at first finalization, and every later read (regression,
   residual bands, actual lineage, naive baseline, horizon completeness) is
   pinned to it. Pre-existing finalized rows are backfilled from
   ``finalized_at`` -- the server clock of the transaction that performed those
   reads -- for ``retrospective_pinned_release`` runs and from
   ``simulated_cutoff_time`` for ``as_recorded`` runs, which is what the old
   code computed. That is a reconstruction of the original horizon to the
   resolution of the recorded finalize time, not a byte-exact replay of the
   original ``clock_timestamp()``.

2. ``coverage_fraction`` was ``count(*) / expected_value_count`` after an
   equality check had already raised on any mismatch, so it was always exactly
   1.0 and ``min_coverage_fraction`` could never fail. Under the new
   ``hindcast_v3`` digest it is horizon completeness: the number of ideal
   horizon steps (``simulated_cutoff_time + step_interval * k`` for
   ``k in 1..horizon_steps``) that had an actual observation at the pinned
   knowledge horizon, divided by ``horizon_steps``. A ``v3`` run must record
   exactly those steps -- no fabricated points, and no quietly dropped ones --
   so ``expected_value_count`` may now be smaller than ``horizon_steps`` for
   ``v3`` and stays strictly equal for ``v1``/``v2``. ``partial`` stays partial.

3. ``computed_interval_coverage`` was computed and then ignored.
   ``forecast_quality_policy.min_interval_coverage_fraction`` is added and wired
   into ``computed_pass`` (and into the finalization trigger's recomputation, so
   the two agree) for ``hindcast_v3`` runs. Existing and new policy rows are
   backfilled with 0.8: the nominal coverage of a p10-p90 band, i.e. the
   strictest threshold a correctly calibrated nominal interval should meet. It
   is a gate that can fail, not a claim of calibration -- on a ten-step horizon
   it tolerates two misses, and small-sample coverage is noisy.
   ``v_forecast_hindcast_outcome`` now publishes the same evidence: its
   ``quality_policy_contract`` column moves to the
   ``plantgeo-forecast-quality-policy-v2`` array (adding
   ``min_interval_coverage_fraction``), and it adds
   ``actual_knowledge_as_of``, ``coverage_fraction``, and
   ``interval_coverage_fraction`` so a reader does not need the raw run row.
   ``agri.forecast_quality_policy_contract_v2`` is the single function that
   builds that array; both the view and the ``hindcast_v3`` branch of
   ``forecast_hindcast_receipt_checksum`` call it so the literal exists once.

4. ``finalize_strategy_selection_receipt`` gated the evaluation iteration with
   ``cutoff_time >= data_cutoff``, the inverse of the rule labels and features
   use, so a receipt could checksum a cutoff claim it violated by months. The
   corrected rule lives in one place, ``agri.strategy_selection_cutoff_violation``,
   which both the finalizer and this migration's audit pass call. Finalized
   receipts that violate the corrected rule are flagged
   ``audit_state = 'cutoff_violation'`` with a reason and a flag time; nothing
   is deleted and nothing is silently grandfathered. The flag is a one-way,
   post-hoc audit annotation and is deliberately outside the receipt preimage --
   flagging must not invalidate the checksum that records what was claimed.

5. ``finalize_strategy_selection_receipt`` now requires a finalized,
   ``quality_passed`` hindcast for the backing series
   (``agri.strategy_selection_quality_evidence``), joined the way
   ``v_forecast_hindcast_outcome`` ties hindcasts to series -- and additionally
   to model, for a publishable receipt backed by a forecast receipt rather
   than an evaluation-only iteration -- and available by the receipt's
   ``issue_time``.

Digest versions and already-finalized rows
------------------------------------------
``hindcast_v3`` is a new receipt digest. Its preimage adds
``min_interval_coverage_fraction`` to the quality-policy contract (bumped to
``plantgeo-forecast-quality-policy-v2``) and carries the
``plantgeo-forecast-hindcast-receipt-v3`` tag. Existing ``hindcast_v1`` and
``hindcast_v2`` receipts keep their exact preimages, their old coverage formula,
and no interval gate: their stored evidence still re-verifies. New runs must
enter as ``hindcast_v3``; a still-staged ``v2`` run may finalize under ``v2``
semantics rather than becoming unfinalizable. ``actual_knowledge_as_of`` is not
part of any preimage -- it cannot be, because the caller must be able to compute
the expected checksum before the server sets it -- exactly like ``finalized_at``.

GUC pinning follows the 0011 recipe (``TimeZone``, ``DateStyle``,
``IntervalStyle``, ``extra_float_digits = 1``). ``extra_float_digits = 1`` is
PostgreSQL 16's default, so pinning it changes no already-stored checksum; it
only stops a session that set it to <= 0 from producing a different digest.
``forecast_hindcast_receipt_checksum`` keeps ``IntervalStyle = iso_8601``
because its ``step_interval::text`` rendering is already in the v2 preimage.

Fail-closed
-----------
The four finalizers rejected a NULL ``p_expected_checksum`` by accident
(``NULL !~ pattern`` is NULL, not true), which skipped the format gate. NULL is
now rejected explicitly. The hindcast receipt-digest dispatch raises on any
version outside the known set instead of returning a NULL preimage.
"""

from collections.abc import Sequence

from agri_data_service.db.sql_objects import load_object_sql
from alembic import op

revision = "20260801_0014"
down_revision = "20260725_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_MIN_INTERVAL_COVERAGE_FRACTION = "0.8"
CUTOFF_VIOLATION_REASON = (
    "20260801_0014 audit: the bound forecast iteration cutoff_time is later than the "
    "receipt data_cutoff under the corrected as-of rule"
)

_REPLACED_FUNCTIONS = (
    "functions/forecast_hindcast_receipt_checksum.sql",
    "functions/finalize_forecast_hindcast_run.sql",
    "functions/enforce_forecast_hindcast_insert_contract.sql",
    "functions/enforce_forecast_hindcast_finalization_policy.sql",
    "functions/finalize_forecast_receipt.sql",
    "functions/finalize_strategy_label_release.sql",
    "functions/require_strategy_initial_state.sql",
    "functions/guard_strategy_selection_receipt_change.sql",
    "functions/finalize_strategy_selection_receipt.sql",
)

_NEW_FUNCTIONS = (
    "functions/forecast_quality_policy_contract_v2.sql",
    "functions/strategy_selection_cutoff_violation.sql",
    "functions/strategy_selection_quality_evidence.sql",
)

_REPLACED_VIEWS = ("views/v_forecast_hindcast_outcome.sql",)


def upgrade() -> None:
    _add_hindcast_knowledge_pin()
    _add_interval_coverage_policy()
    _add_selection_audit_flag()

    for function_file in _NEW_FUNCTIONS:
        op.execute(load_object_sql(function_file))
    for function_file in _REPLACED_FUNCTIONS:
        op.execute(load_object_sql(function_file, or_replace=True))
    for view_file in _REPLACED_VIEWS:
        op.execute(load_object_sql(view_file, or_replace=True))

    _flag_cutoff_violations()

    op.execute(
        """
        REVOKE EXECUTE ON FUNCTION agri.strategy_selection_cutoff_violation(uuid) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.strategy_selection_quality_evidence(uuid) FROM PUBLIC;
        """
    )


def _add_hindcast_knowledge_pin() -> None:
    op.execute(
        """
        ALTER TABLE agri.forecast_hindcast_run
            ADD COLUMN actual_knowledge_as_of timestamptz;
        """
    )
    # ALTER TABLE ADD COLUMN fires no row triggers, but the backfill UPDATE would hit
    # guard_forecast_hindcast_run_change, which refuses every write to a finalized run.
    op.execute(
        """
        ALTER TABLE agri.forecast_hindcast_run
            DISABLE TRIGGER forecast_hindcast_run_change_guard;
        UPDATE agri.forecast_hindcast_run
           SET actual_knowledge_as_of = CASE
                   WHEN availability_mode = 'as_recorded' THEN simulated_cutoff_time
                   ELSE finalized_at
               END
         WHERE status = 'finalized'
           AND actual_knowledge_as_of IS NULL;
        ALTER TABLE agri.forecast_hindcast_run
            ENABLE TRIGGER forecast_hindcast_run_change_guard;
        """
    )
    op.execute(
        """
        ALTER TABLE agri.forecast_hindcast_run
            ADD CONSTRAINT ck_forecast_hindcast_run_knowledge_pin
            CHECK (status <> 'finalized' OR actual_knowledge_as_of IS NOT NULL);
        """
    )
    op.execute(
        """
        ALTER TABLE agri.forecast_hindcast_run
            DROP CONSTRAINT ck_forecast_hindcast_run_receipt_digest_version;
        ALTER TABLE agri.forecast_hindcast_run
            ADD CONSTRAINT ck_forecast_hindcast_run_receipt_digest_version
            CHECK (receipt_digest_version IN ('hindcast_v1', 'hindcast_v2', 'hindcast_v3'));
        ALTER TABLE agri.forecast_hindcast_run
            ALTER COLUMN receipt_digest_version SET DEFAULT 'hindcast_v3';
        """
    )
    op.execute(
        """
        ALTER TABLE agri.forecast_hindcast_run
            DROP CONSTRAINT ck_forecast_hindcast_run_horizon;
        ALTER TABLE agri.forecast_hindcast_run
            ADD CONSTRAINT ck_forecast_hindcast_run_horizon
            CHECK (
                horizon_steps > 0
                AND step_interval > interval '0'
                AND expected_value_count > 0
                AND expected_value_count <= horizon_steps
                AND (
                    receipt_digest_version = 'hindcast_v3'
                    OR expected_value_count = horizon_steps
                )
            );
        """
    )


def _add_interval_coverage_policy() -> None:
    op.execute(
        f"""
        ALTER TABLE agri.forecast_quality_policy
            ADD COLUMN min_interval_coverage_fraction double precision
            NOT NULL DEFAULT {DEFAULT_MIN_INTERVAL_COVERAGE_FRACTION};
        ALTER TABLE agri.forecast_quality_policy
            ADD CONSTRAINT ck_forecast_quality_policy_interval_coverage
            CHECK (
                min_interval_coverage_fraction > 0
                AND min_interval_coverage_fraction <= 1
            );
        """
    )


def _add_selection_audit_flag() -> None:
    op.execute(
        """
        ALTER TABLE agri.strategy_selection_receipt
            ADD COLUMN audit_state varchar(32) NOT NULL DEFAULT 'clear',
            ADD COLUMN audit_reason text,
            ADD COLUMN audit_flagged_at timestamptz;
        ALTER TABLE agri.strategy_selection_receipt
            ADD CONSTRAINT ck_strategy_selection_receipt_audit_state
            CHECK (
                (
                    audit_state = 'clear'
                    AND audit_reason IS NULL
                    AND audit_flagged_at IS NULL
                )
                OR (
                    audit_state = 'cutoff_violation'
                    AND status = 'finalized'
                    AND audit_reason IS NOT NULL
                    AND audit_flagged_at IS NOT NULL
                )
            );
        """
    )


def _flag_cutoff_violations() -> None:
    op.execute(
        f"""
        UPDATE agri.strategy_selection_receipt AS receipt
           SET audit_state = 'cutoff_violation',
               audit_reason = {_quote(CUTOFF_VIOLATION_REASON)},
               audit_flagged_at = now()
         WHERE receipt.status = 'finalized'
           AND receipt.audit_state = 'clear'
           AND agri.strategy_selection_cutoff_violation(receipt.id);
        """
    )


def _quote(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def downgrade() -> None:
    raise NotImplementedError(
        "Pinned knowledge horizons, tightened quality gates, and cutoff-violation audit flags "
        "are forward-only evidence; restore a verified backup into a fresh database."
    )
