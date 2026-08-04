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

Embedded object bodies
----------------------
Ten of the bodies installed here are embedded as module constants rather than
loaded from ``db/agri/`` because ``20260803_0018`` drops those objects, and the
declarative tree is regenerated from a dump of *head* -- so the canonical files
no longer exist and ``load_object_sql`` could not replay this revision. Each
embedded body is the text ``load_object_sql`` returned immediately before the
move, its ``CREATE OR REPLACE`` rewriting included, so the applied DDL is
unchanged; the objects that survive at head keep loading from the tree. A
migration whose object a later revision drops must carry its own DDL. See
``db/AGENTS.md``.
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

_STRATEGY_SELECTION_QUALITY_EVIDENCE = r"""
CREATE FUNCTION agri.strategy_selection_quality_evidence(p_selection_receipt_id uuid) RETURNS boolean
    LANGUAGE sql STABLE
    SET "TimeZone" TO 'UTC'
    AS $$
        SELECT EXISTS (
            SELECT 1
              FROM agri.strategy_selection_receipt AS receipt
              INNER JOIN agri.forecast_iteration AS iteration
                  ON iteration.id = receipt.forecast_iteration_id
              INNER JOIN agri.v_forecast_hindcast_outcome AS outcome
                  ON outcome.series_id = iteration.series_id
             WHERE receipt.id = p_selection_receipt_id
               AND outcome.quality_passed
               AND outcome.simulated_cutoff_time <= receipt.data_cutoff
               AND outcome.signal_available_at <= receipt.issue_time
        ) OR EXISTS (
            SELECT 1
              FROM agri.strategy_selection_receipt AS receipt
              INNER JOIN agri.forecast_receipt AS forecast
                  ON forecast.id = receipt.forecast_receipt_id
              INNER JOIN agri.forecast_run AS run
                  ON run.id = forecast.forecast_run_id
              INNER JOIN agri.v_forecast_hindcast_outcome AS outcome
                  ON outcome.series_id = forecast.series_id
                 AND outcome.model_id = run.model_id
             WHERE receipt.id = p_selection_receipt_id
               AND outcome.quality_passed
               AND outcome.simulated_cutoff_time <= receipt.data_cutoff
               AND outcome.signal_available_at <= receipt.issue_time
        )
    $$;
"""

_FORECAST_HINDCAST_RECEIPT_CHECKSUM = r"""
CREATE OR REPLACE FUNCTION agri.forecast_hindcast_receipt_checksum(p_hindcast_run_id uuid) RETURNS character varying
    LANGUAGE plpgsql STABLE
    SET "TimeZone" TO 'UTC'
    SET "IntervalStyle" TO 'iso_8601'
    SET "DateStyle" TO 'ISO, MDY'
    SET extra_float_digits TO '1'
    AS $$
        DECLARE
            digest_version varchar;
            computed varchar;
        BEGIN
            SELECT run.receipt_digest_version
              INTO digest_version
              FROM agri.forecast_hindcast_run AS run
             WHERE run.id = p_hindcast_run_id;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;
            IF digest_version NOT IN ('hindcast_v1', 'hindcast_v2', 'hindcast_v3') THEN
                RAISE EXCEPTION
                    'unsupported hindcast receipt digest version: %', digest_version;
            END IF;

            SELECT encode(public.digest(
                CASE run.receipt_digest_version
                    WHEN 'hindcast_v1' THEN concat_ws('|',
                        run.hindcast_key,
                        to_char(run.simulated_cutoff_time AT TIME ZONE 'UTC',
                            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                        to_char(run.uncertainty_calibration_cutoff_time AT TIME ZONE 'UTC',
                            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                        run.availability_mode,
                        run.input_release_checksum,
                        run.model_checksum,
                        run.parameter_checksum,
                        coalesce((
                            SELECT string_agg(value.value_checksum, E'\n' ORDER BY value.horizon_step)
                            FROM agri.forecast_hindcast_value AS value
                            WHERE value.hindcast_run_id = run.id
                        ), '')
                    )
                    WHEN 'hindcast_v2' THEN jsonb_build_array(
                        'plantgeo-forecast-hindcast-receipt-v2',
                        run.hindcast_key,
                        run.forecast_run_id::text,
                        run.series_id::text,
                        series.series_key,
                        parent.model_id::text,
                        model.model_key,
                        model.model_version,
                        parent.quality_policy_id::text,
                        policy.policy_key,
                        jsonb_build_array(
                            'plantgeo-forecast-quality-policy-v1',
                            policy.is_active::text,
                            policy.min_training_points::text,
                            policy.min_backtest_points::text,
                            policy.min_coverage_fraction::text,
                            policy.max_mae::text,
                            policy.max_rmse::text,
                            policy.max_mape::text,
                            policy.min_skill_score::text,
                            to_jsonb(policy.required_quantiles)
                        ),
                        run.release_set_id::text,
                        to_char(run.simulated_cutoff_time AT TIME ZONE 'UTC',
                            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                        to_char(run.uncertainty_calibration_cutoff_time AT TIME ZONE 'UTC',
                            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                        run.horizon_steps::text,
                        run.step_interval::text,
                        run.minimum_training_points::text,
                        run.training_point_count::text,
                        run.expected_value_count::text,
                        run.availability_mode,
                        run.input_release_checksum,
                        run.model_checksum,
                        run.parameter_checksum,
                        coalesce((
                            SELECT jsonb_agg(
                                jsonb_build_array(
                                    value.horizon_step::text,
                                    to_char(value.valid_time AT TIME ZONE 'UTC',
                                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                                    to_char(value.actual_data_available_at AT TIME ZONE 'UTC',
                                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                                    value.value_checksum
                                ) ORDER BY value.horizon_step
                            )
                            FROM agri.forecast_hindcast_value AS value
                            WHERE value.hindcast_run_id = run.id
                        ), '[]'::jsonb)
                    )::text
                    WHEN 'hindcast_v3' THEN jsonb_build_array(
                        'plantgeo-forecast-hindcast-receipt-v3',
                        run.hindcast_key,
                        run.forecast_run_id::text,
                        run.series_id::text,
                        series.series_key,
                        parent.model_id::text,
                        model.model_key,
                        model.model_version,
                        parent.quality_policy_id::text,
                        policy.policy_key,
                        agri.forecast_quality_policy_contract_v2(policy),
                        run.release_set_id::text,
                        to_char(run.simulated_cutoff_time AT TIME ZONE 'UTC',
                            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                        to_char(run.uncertainty_calibration_cutoff_time AT TIME ZONE 'UTC',
                            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                        run.horizon_steps::text,
                        run.step_interval::text,
                        run.minimum_training_points::text,
                        run.training_point_count::text,
                        run.expected_value_count::text,
                        run.availability_mode,
                        run.input_release_checksum,
                        run.model_checksum,
                        run.parameter_checksum,
                        coalesce((
                            SELECT jsonb_agg(
                                jsonb_build_array(
                                    value.horizon_step::text,
                                    to_char(value.valid_time AT TIME ZONE 'UTC',
                                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                                    to_char(value.actual_data_available_at AT TIME ZONE 'UTC',
                                        'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                                    value.value_checksum
                                ) ORDER BY value.horizon_step
                            )
                            FROM agri.forecast_hindcast_value AS value
                            WHERE value.hindcast_run_id = run.id
                        ), '[]'::jsonb)
                    )::text
                    ELSE NULL
                END,
                'sha256'
            ), 'hex')::varchar
              INTO STRICT computed
              FROM agri.forecast_hindcast_run AS run
              INNER JOIN agri.forecast_run AS parent ON parent.id = run.forecast_run_id
              INNER JOIN agri.forecast_series AS series ON series.id = run.series_id
              INNER JOIN agri.forecast_model AS model ON model.id = parent.model_id
              INNER JOIN agri.forecast_quality_policy AS policy
                  ON policy.id = parent.quality_policy_id
             WHERE run.id = p_hindcast_run_id;
            IF computed IS NULL THEN
                RAISE EXCEPTION
                    'hindcast receipt preimage is undefined for digest version %', digest_version;
            END IF;
            RETURN computed;
        END
        $$;
"""

_FINALIZE_FORECAST_HINDCAST_RUN = r"""
CREATE OR REPLACE FUNCTION agri.finalize_forecast_hindcast_run(p_hindcast_run_id uuid, p_expected_checksum character varying) RETURNS agri.forecast_hindcast_run
    LANGUAGE plpgsql
    SET "TimeZone" TO 'UTC'
    SET "DateStyle" TO 'ISO, MDY'
    SET "IntervalStyle" TO 'iso_8601'
    SET extra_float_digits TO '1'
    AS $_$
        DECLARE
            target agri.forecast_hindcast_run;
            parent_run agri.forecast_run;
            snapshot agri.forecast_feature_snapshot;
            model agri.forecast_model;
            policy agri.forecast_quality_policy;
            release agri.release_set;
            knowledge_as_of timestamptz;
            computed_checksum varchar;
            value_count integer;
            invalid_count integer;
            available_step_count integer;
            computed_mae double precision;
            computed_rmse double precision;
            computed_naive_rmse double precision;
            computed_skill double precision;
            computed_bias double precision;
            computed_mape double precision;
            computed_coverage double precision;
            computed_interval_coverage double precision;
            computed_pass boolean;
        BEGIN
            IF p_expected_checksum IS NULL OR p_expected_checksum !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'hindcast receipt checksum must be SHA-256';
            END IF;

            SELECT * INTO target
              FROM agri.forecast_hindcast_run
             WHERE id = p_hindcast_run_id
             FOR UPDATE;
            IF NOT FOUND OR target.status NOT IN ('staging', 'finalized') THEN
                RAISE EXCEPTION 'hindcast run is missing or not finalizable';
            END IF;
            IF target.receipt_digest_version NOT IN ('hindcast_v1', 'hindcast_v2', 'hindcast_v3') THEN
                RAISE EXCEPTION
                    'unsupported hindcast receipt digest version: %', target.receipt_digest_version;
            END IF;

            SELECT * INTO STRICT parent_run FROM agri.forecast_run WHERE id = target.forecast_run_id;
            SELECT * INTO STRICT snapshot
              FROM agri.forecast_feature_snapshot WHERE id = parent_run.feature_snapshot_id;
            SELECT * INTO STRICT model FROM agri.forecast_model WHERE id = parent_run.model_id;
            SELECT * INTO STRICT policy
              FROM agri.forecast_quality_policy WHERE id = parent_run.quality_policy_id;
            SELECT * INTO STRICT release FROM agri.release_set WHERE id = target.release_set_id;

            IF parent_run.forecast_method <> 'sql_linear'
               OR model.model_kind <> 'sql_linear'
               OR model.model_purpose <> 'metric_forecast'
               OR model.model_code_checksum <> target.model_checksum
               OR parent_run.model_checksum <> target.model_checksum THEN
                RAISE EXCEPTION 'initial hindcast receipts support only the reviewed SQL metric baseline';
            END IF;
            IF snapshot.status <> 'validated'
               OR snapshot.release_set_id <> target.release_set_id
               OR snapshot.input_release_checksum <> target.input_release_checksum
               OR release.manifest_checksum <> target.input_release_checksum
               OR release.state NOT IN ('validated', 'published') THEN
                RAISE EXCEPTION 'hindcast release and feature lineage is not validated';
            END IF;
            IF target.series_id NOT IN (
                SELECT series_id FROM agri.forecast_backtest_metric
                WHERE forecast_run_id = parent_run.id
            ) THEN
                RAISE EXCEPTION 'hindcast series is not bound to the parent forecast backtest';
            END IF;
            IF target.availability_mode = 'as_recorded'
               AND (release.validated_at > target.simulated_cutoff_time
                    OR release.as_of_time > target.simulated_cutoff_time) THEN
                RAISE EXCEPTION 'as-recorded hindcast inputs were not available at the simulated cutoff';
            END IF;

            -- Pin the actuals/knowledge horizon once, at first finalization, and reuse the
            -- stored value on every later re-verification so audits are reproducible.
            knowledge_as_of := coalesce(
                target.actual_knowledge_as_of,
                CASE
                    WHEN target.availability_mode = 'as_recorded' THEN target.simulated_cutoff_time
                    ELSE clock_timestamp()
                END
            );

            WITH evidence AS (
                SELECT
                    value.*,
                    regression.valid_time AS expected_valid_time,
                    regression.forecast_value AS expected_point_value,
                    regression.training_point_count AS expected_training_count,
                    regression.eligible AS regression_eligible,
                    bands.residual_p10,
                    bands.residual_p50,
                    bands.residual_p90,
                    bands.eligible AS bands_eligible,
                    actual.metric_value AS expected_actual_value,
                    actual.source_release_id AS expected_actual_source_release_id,
                    actual.observation_checksum AS expected_actual_observation_checksum,
                    source_release.data_available_at AS expected_actual_data_available_at,
                    naive.metric_value AS expected_naive_value
                FROM agri.forecast_hindcast_value AS value
                LEFT JOIN LATERAL agri.forecast_linear_regression(
                    target.series_id, target.release_set_id, knowledge_as_of,
                    target.simulated_cutoff_time, target.horizon_steps,
                    target.step_interval, target.minimum_training_points
                ) AS regression ON regression.horizon_step = value.horizon_step
                LEFT JOIN LATERAL agri.forecast_linear_residual_bands(
                    target.series_id, target.release_set_id, knowledge_as_of,
                    target.uncertainty_calibration_cutoff_time, target.horizon_steps,
                    target.step_interval, target.minimum_training_points
                ) AS bands ON true
                LEFT JOIN LATERAL (
                    SELECT base.*
                    FROM agri.forecast_timeseries_base(
                        target.release_set_id, knowledge_as_of
                    ) AS base
                    WHERE base.series_id = target.series_id
                      AND base.observed_at = value.valid_time
                ) AS actual ON true
                LEFT JOIN agri.source_release AS source_release
                    ON source_release.id = actual.source_release_id
                LEFT JOIN LATERAL (
                    SELECT base.metric_value
                    FROM agri.forecast_timeseries_base(
                        target.release_set_id, knowledge_as_of
                    ) AS base
                    WHERE base.series_id = target.series_id
                      AND base.observed_at <= target.simulated_cutoff_time
                    ORDER BY base.observed_at DESC
                    LIMIT 1
                ) AS naive ON true
                WHERE value.hindcast_run_id = target.id
            )
            SELECT
                count(*),
                count(*) FILTER (WHERE
                    horizon_step > target.horizon_steps
                    OR valid_time <> target.simulated_cutoff_time
                        + target.step_interval * horizon_step
                    OR expected_valid_time IS NULL
                    OR NOT regression_eligible
                    OR NOT bands_eligible
                    OR expected_training_count <> target.training_point_count
                    OR point_value IS DISTINCT FROM expected_point_value
                    OR p10_value IS DISTINCT FROM expected_point_value + residual_p10
                    OR p50_value IS DISTINCT FROM expected_point_value + residual_p50
                    OR p90_value IS DISTINCT FROM expected_point_value + residual_p90
                    OR naive_value IS DISTINCT FROM expected_naive_value
                    OR actual_value IS DISTINCT FROM expected_actual_value
                    OR actual_source_release_id IS DISTINCT FROM expected_actual_source_release_id
                    OR actual_observation_checksum IS DISTINCT FROM expected_actual_observation_checksum
                    OR actual_data_available_at IS DISTINCT FROM expected_actual_data_available_at
                )
              INTO value_count, invalid_count
              FROM evidence;
            IF value_count <> target.expected_value_count OR invalid_count > 0 THEN
                RAISE EXCEPTION 'hindcast points failed cutoff, grid, uncertainty, or actual-lineage verification';
            END IF;

            -- Horizon completeness: how much of the declared ideal step grid had an actual
            -- observation at the pinned knowledge time.
            SELECT count(DISTINCT step.horizon_step)
              INTO available_step_count
              FROM generate_series(1, target.horizon_steps) AS step(horizon_step)
              INNER JOIN agri.forecast_timeseries_base(
                    target.release_set_id, knowledge_as_of
              ) AS base
                ON base.series_id = target.series_id
               AND base.observed_at = target.simulated_cutoff_time
                    + target.step_interval * step.horizon_step;

            SELECT
                avg(value.absolute_error),
                sqrt(avg(value.squared_error)),
                sqrt(avg((value.naive_value - value.actual_value)
                    * (value.naive_value - value.actual_value))),
                avg(value.point_value - value.actual_value),
                avg(value.absolute_error / nullif(abs(value.actual_value), 0)),
                avg(CASE WHEN value.interval_covered THEN 1.0 ELSE 0.0 END)
              INTO computed_mae, computed_rmse, computed_naive_rmse,
                   computed_bias, computed_mape, computed_interval_coverage
              FROM agri.forecast_hindcast_value AS value
             WHERE value.hindcast_run_id = target.id;
            IF target.receipt_digest_version = 'hindcast_v3' THEN
                IF value_count <> available_step_count THEN
                    RAISE EXCEPTION
                        'hindcast recorded % of % horizon actuals available at its knowledge horizon',
                        value_count, available_step_count;
                END IF;
                computed_coverage := available_step_count::double precision / target.horizon_steps;
            ELSE
                computed_coverage := value_count::double precision / target.expected_value_count;
            END IF;
            computed_skill := CASE
                WHEN computed_naive_rmse = 0 AND computed_rmse = 0 THEN 1.0
                WHEN computed_naive_rmse = 0 THEN NULL
                ELSE 1.0 - computed_rmse / computed_naive_rmse
            END;
            computed_pass := target.training_point_count >= policy.min_training_points

                AND (
                    (
                        target.receipt_digest_version = 'hindcast_v1'
                        AND target.expected_value_count >= policy.min_backtest_points
                    )
                    OR (
                        target.receipt_digest_version IN ('hindcast_v2', 'hindcast_v3')
                        AND (
                            SELECT bands.backtest_point_count
                            FROM agri.forecast_linear_residual_bands(
                                target.series_id,
                                target.release_set_id,
                                knowledge_as_of,
                                target.uncertainty_calibration_cutoff_time,
                                target.horizon_steps,
                                target.step_interval,
                                target.minimum_training_points
                            ) AS bands
                        ) >= policy.min_backtest_points
                    )
                )

                AND computed_coverage >= policy.min_coverage_fraction
                AND (
                    target.receipt_digest_version <> 'hindcast_v3'
                    OR (computed_interval_coverage IS NOT NULL
                        AND computed_interval_coverage >= policy.min_interval_coverage_fraction)
                )
                AND (policy.max_mae IS NULL OR computed_mae <= policy.max_mae)
                AND (policy.max_rmse IS NULL OR computed_rmse <= policy.max_rmse)
                AND (policy.max_mape IS NULL
                    OR (computed_mape IS NOT NULL AND computed_mape <= policy.max_mape))
                AND (policy.min_skill_score IS NULL
                    OR (computed_skill IS NOT NULL AND computed_skill >= policy.min_skill_score));

            SELECT agri.forecast_hindcast_receipt_checksum(target.id)
              INTO computed_checksum;
            IF computed_checksum IS DISTINCT FROM p_expected_checksum THEN
                RAISE EXCEPTION 'hindcast receipt checksum mismatch';
            END IF;

            IF target.status = 'finalized' THEN
                IF target.actual_knowledge_as_of IS NULL
                   OR target.actual_knowledge_as_of IS DISTINCT FROM knowledge_as_of
                   OR target.receipt_checksum IS DISTINCT FROM computed_checksum
                   OR target.mae IS DISTINCT FROM computed_mae
                   OR target.rmse IS DISTINCT FROM computed_rmse
                   OR target.naive_rmse IS DISTINCT FROM computed_naive_rmse
                   OR target.skill_score IS DISTINCT FROM computed_skill
                   OR target.bias IS DISTINCT FROM computed_bias
                   OR target.mape IS DISTINCT FROM computed_mape
                   OR target.coverage_fraction IS DISTINCT FROM computed_coverage
                   OR target.interval_coverage_fraction IS DISTINCT FROM computed_interval_coverage
                   OR target.quality_passed IS DISTINCT FROM computed_pass THEN
                    RAISE EXCEPTION 'finalized hindcast evidence does not match recomputed values';
                END IF;
                RETURN target;
            END IF;

            UPDATE agri.forecast_hindcast_run
               SET status = 'finalized',
                   quality_passed = computed_pass,
                   mae = computed_mae,
                   rmse = computed_rmse,
                   naive_rmse = computed_naive_rmse,
                   skill_score = computed_skill,
                   bias = computed_bias,
                   mape = computed_mape,
                   coverage_fraction = computed_coverage,
                   interval_coverage_fraction = computed_interval_coverage,
                   actual_knowledge_as_of = knowledge_as_of,
                   receipt_checksum = computed_checksum,
                   recorded_at = clock_timestamp(),
                   finalized_at = clock_timestamp()
             WHERE id = target.id
             RETURNING * INTO target;
            RETURN target;
        END
        $_$;
"""  # noqa: E501

_ENFORCE_FORECAST_HINDCAST_INSERT_CONTRACT = r"""
CREATE OR REPLACE FUNCTION agri.enforce_forecast_hindcast_insert_contract() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            IF NEW.status <> 'staging' THEN
                RAISE EXCEPTION 'new hindcast runs must begin in staging status';
            END IF;
            IF NEW.receipt_digest_version <> 'hindcast_v3' THEN
                RAISE EXCEPTION 'new hindcast runs require receipt digest version hindcast_v3';
            END IF;
            IF NEW.actual_knowledge_as_of IS NOT NULL THEN
                RAISE EXCEPTION 'the hindcast actual-knowledge horizon is server-set at finalization';
            END IF;
            RETURN NEW;
        END
        $$;
"""

_ENFORCE_FORECAST_HINDCAST_FINALIZATION_POLICY = r"""
CREATE OR REPLACE FUNCTION agri.enforce_forecast_hindcast_finalization_policy() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            policy agri.forecast_quality_policy;
            actual_calibration_samples bigint;
            current_quality_passed boolean;
        BEGIN
            IF NEW.status <> 'finalized' OR OLD.status = 'finalized' THEN
                RETURN NEW;
            END IF;
            IF NEW.receipt_digest_version NOT IN ('hindcast_v2', 'hindcast_v3') THEN
                RAISE EXCEPTION
                    'hindcast finalization requires receipt digest version hindcast_v2 or hindcast_v3';
            END IF;
            IF NEW.actual_knowledge_as_of IS NULL THEN
                RAISE EXCEPTION 'hindcast finalization must pin its actual-knowledge horizon';
            END IF;
            IF NEW.availability_mode = 'as_recorded'
               AND NEW.actual_knowledge_as_of <> NEW.simulated_cutoff_time THEN
                RAISE EXCEPTION
                    'as-recorded hindcast knowledge horizon must equal the simulated cutoff';
            END IF;

            SELECT policy_row.*
              INTO STRICT policy
              FROM agri.forecast_run AS parent
              INNER JOIN agri.forecast_quality_policy AS policy_row
                  ON policy_row.id = parent.quality_policy_id
             WHERE parent.id = NEW.forecast_run_id
             FOR SHARE OF policy_row;

            IF NOT policy.is_active THEN
                RAISE EXCEPTION 'hindcast quality policy is inactive';
            END IF;

            SELECT bands.backtest_point_count
              INTO actual_calibration_samples
              FROM agri.forecast_linear_residual_bands(
                    NEW.series_id,
                    NEW.release_set_id,
                    NEW.actual_knowledge_as_of,
                    NEW.uncertainty_calibration_cutoff_time,
                    NEW.horizon_steps,
                    NEW.step_interval,
                    NEW.minimum_training_points
              ) AS bands;

            IF coalesce(actual_calibration_samples, 0) < policy.min_backtest_points THEN
                RAISE EXCEPTION
                    'hindcast uncertainty calibration has % samples; active policy requires at least %',
                    coalesce(actual_calibration_samples, 0),
                    policy.min_backtest_points;
            END IF;

            current_quality_passed := NEW.training_point_count >= policy.min_training_points
                AND NEW.coverage_fraction >= policy.min_coverage_fraction
                AND (
                    NEW.receipt_digest_version <> 'hindcast_v3'
                    OR (NEW.interval_coverage_fraction IS NOT NULL
                        AND NEW.interval_coverage_fraction >= policy.min_interval_coverage_fraction)
                )
                AND (policy.max_mae IS NULL OR NEW.mae <= policy.max_mae)
                AND (policy.max_rmse IS NULL OR NEW.rmse <= policy.max_rmse)
                AND (policy.max_mape IS NULL
                    OR (NEW.mape IS NOT NULL AND NEW.mape <= policy.max_mape))
                AND (policy.min_skill_score IS NULL
                    OR (NEW.skill_score IS NOT NULL AND NEW.skill_score >= policy.min_skill_score));
            NEW.quality_passed := current_quality_passed;
            RETURN NEW;
        END
        $$;
"""

_FINALIZE_FORECAST_RECEIPT = r"""
CREATE OR REPLACE FUNCTION agri.finalize_forecast_receipt(p_receipt_id uuid, p_expected_checksum character varying) RETURNS agri.forecast_receipt
    LANGUAGE plpgsql
    SET "TimeZone" TO 'UTC'
    SET "DateStyle" TO 'ISO, MDY'
    SET "IntervalStyle" TO 'postgres'
    SET extra_float_digits TO '1'
    SET search_path TO 'public', 'pg_catalog'
    AS $_$
        DECLARE
            receipt agri.forecast_receipt;
            run_status varchar;
            run_issue_time timestamptz;
            run_valid_from timestamptz;
            run_valid_to timestamptz;
            run_horizon_steps integer;
            run_step_interval interval;
            required_quantiles double precision[];
            output_state varchar;
            output_run_id uuid;
            output_checksum varchar;
            output_row_count bigint;
            actual_count integer;
            actual_min_time timestamptz;
            actual_max_time timestamptz;
            actual_checksum varchar;
            missing_band_count integer;
            invalid_grid_count integer;
            state_series_id uuid;
            state_valid_from timestamptz;
            state_valid_to timestamptz;
            state_available_at timestamptz;
        BEGIN
            IF p_expected_checksum IS NULL OR p_expected_checksum !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'receipt checksum must be SHA-256';
            END IF;
            SELECT * INTO receipt
              FROM agri.forecast_receipt
             WHERE id = p_receipt_id
             FOR UPDATE;
            IF NOT FOUND OR receipt.status NOT IN ('staging', 'finalized') THEN
                RAISE EXCEPTION 'forecast receipt is missing or not eligible for finalization';
            END IF;
            SELECT
                run.status, run.issue_time, run.valid_from, run.valid_to,
                run.horizon_steps, run.step_interval, policy.required_quantiles
              INTO
                run_status, run_issue_time, run_valid_from, run_valid_to,
                run_horizon_steps, run_step_interval, required_quantiles
              FROM agri.forecast_run AS run
              INNER JOIN agri.forecast_quality_policy AS policy ON policy.id = run.quality_policy_id
             WHERE run.id = receipt.forecast_run_id;
            IF run_status <> 'validated' THEN
                RAISE EXCEPTION 'forecast receipt requires a validated forecast run';
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM agri.forecast_backtest_metric AS metric
                INNER JOIN agri.job_output AS output ON output.id = metric.job_output_id
                WHERE metric.forecast_run_id = receipt.forecast_run_id
                  AND metric.series_id = receipt.series_id
                  AND metric.passed
                  AND output.state IN ('validated', 'published')
                  AND output.checksum_sha256 = metric.metrics_checksum
                  AND output.row_count = 1
            ) THEN
                RAISE EXCEPTION 'forecast receipt series has no passing backtest evidence';
            END IF;
            IF receipt.issue_time <> run_issue_time
               OR receipt.valid_from <> run_valid_from
               OR receipt.valid_to <> run_valid_to
               OR receipt.expected_value_count <> run_horizon_steps
               OR NOT receipt.quantile_levels @> required_quantiles THEN
                RAISE EXCEPTION 'forecast receipt issue, valid-time, or quantile policy mismatch';
            END IF;
            IF receipt.entity_state_id IS NOT NULL THEN
                SELECT series_id, valid_from, valid_to, data_available_at
                  INTO state_series_id, state_valid_from, state_valid_to, state_available_at
                  FROM agri.forecast_entity_state
                 WHERE id = receipt.entity_state_id;
                IF state_series_id <> receipt.series_id
                   OR state_available_at > receipt.issue_time
                   OR receipt.issue_time < state_valid_from
                   OR (state_valid_to IS NOT NULL AND receipt.issue_time >= state_valid_to) THEN
                    RAISE EXCEPTION 'forecast receipt entity state lineage mismatch';
                END IF;
            END IF;
            SELECT state, job_run_id, checksum_sha256, row_count
              INTO output_state, output_run_id, output_checksum, output_row_count
              FROM agri.job_output
             WHERE id = receipt.job_output_id;
            IF output_state NOT IN ('validated', 'published')
               OR output_run_id <> (SELECT job_run_id FROM agri.forecast_run WHERE id = receipt.forecast_run_id)
               OR output_checksum <> p_expected_checksum
               OR output_row_count IS NULL
               OR output_row_count <> receipt.expected_value_count THEN
                RAISE EXCEPTION 'forecast receipt job output lineage mismatch';
            END IF;

            SELECT
                count(*),
                min(value.valid_time),
                max(value.valid_time),
                count(*) FILTER (
                    WHERE value.horizon_step < 1
                       OR value.horizon_step > run_horizon_steps
                       OR value.valid_time <> receipt.issue_time + (run_step_interval * value.horizon_step)
                ),
                count(*) FILTER (
                    WHERE EXISTS (
                        SELECT 1
                        FROM unnest(receipt.quantile_levels) AS required(quantile)
                        WHERE NOT (value.quantile_values ? required.quantile::text)
                           OR jsonb_typeof(value.quantile_values -> required.quantile::text) <> 'number'
                           OR CASE
                               WHEN jsonb_typeof(value.quantile_values -> required.quantile::text) = 'number'
                               THEN ((value.quantile_values ->> required.quantile::text)::double precision)::text
                                    IN ('NaN', 'Infinity', '-Infinity')
                               ELSE false
                           END
                    )
                       OR EXISTS (
                           SELECT 1
                           FROM unnest(receipt.quantile_levels) AS lower(quantile)
                           CROSS JOIN unnest(receipt.quantile_levels) AS upper(quantile)
                           WHERE lower.quantile < upper.quantile
                             AND CASE
                                 WHEN jsonb_typeof(value.quantile_values -> lower.quantile::text) = 'number'
                                  AND jsonb_typeof(value.quantile_values -> upper.quantile::text) = 'number'
                                 THEN (value.quantile_values ->> lower.quantile::text)::double precision
                                      > (value.quantile_values ->> upper.quantile::text)::double precision
                                 ELSE false
                             END
                       )
                       OR (
                           0.1 = ANY(receipt.quantile_levels)
                           AND (value.quantile_values ->> '0.1')::double precision IS DISTINCT FROM value.p10_value
                       )
                       OR (
                           0.5 = ANY(receipt.quantile_levels)
                           AND (value.quantile_values ->> '0.5')::double precision IS DISTINCT FROM value.p50_value
                       )
                       OR (
                           0.9 = ANY(receipt.quantile_levels)
                           AND (value.quantile_values ->> '0.9')::double precision IS DISTINCT FROM value.p90_value
                       )
                ),
                encode(
                    digest(
                        coalesce(string_agg(
                            concat_ws('|',
                                to_char(value.valid_time AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                                value.horizon_step::text,
                                value.point_value::text,
                                coalesce(value.p10_value::text, ''),
                                coalesce(value.p50_value::text, ''),
                                coalesce(value.p90_value::text, ''),
                                value.quantile_values::text
                            ), E'\n' ORDER BY value.horizon_step), ''),
                        'sha256'
                    ),
                    'hex'
                )
              INTO
                actual_count, actual_min_time, actual_max_time,
                invalid_grid_count, missing_band_count, actual_checksum
              FROM agri.forecast_value AS value
             WHERE value.forecast_receipt_id = receipt.id;

            IF actual_count <> receipt.expected_value_count
               OR actual_min_time < receipt.valid_from
               OR actual_max_time >= receipt.valid_to
               OR invalid_grid_count > 0
               OR missing_band_count > 0 THEN
                RAISE EXCEPTION 'forecast receipt count or valid-time extent mismatch';
            END IF;
            IF actual_checksum <> p_expected_checksum THEN
                RAISE EXCEPTION 'forecast receipt checksum mismatch';
            END IF;

            IF receipt.status = 'staging' THEN
                UPDATE agri.forecast_receipt
                   SET status = 'finalized', receipt_checksum = actual_checksum, finalized_at = now()
                 WHERE id = receipt.id
                 RETURNING * INTO receipt;
            ELSIF receipt.receipt_checksum <> actual_checksum THEN
                RAISE EXCEPTION 'finalized forecast receipt checksum changed';
            END IF;
            RETURN receipt;
        END
        $_$;
"""  # noqa: E501

# The next three bodies are also embedded, without OR REPLACE, in 20260725_0013.
_FINALIZE_STRATEGY_LABEL_RELEASE = r"""
CREATE OR REPLACE FUNCTION agri.finalize_strategy_label_release(p_label_release_id uuid, p_expected_checksum character varying) RETURNS agri.strategy_label_release
    LANGUAGE plpgsql
    SET search_path TO 'public', 'pg_catalog'
    AS $_$
        DECLARE
            label agri.strategy_label_release;
            pinned_release agri.release_set;
            outcome agri.strategy_outcome_definition;
            actual_row_count bigint;
            actual_treated_count bigint;
            actual_control_count bigint;
            actual_strategy_count integer;
            actual_spatial_block_count integer;
            distinct_subject_count bigint;
            invalid_episode_count bigint;
            invalid_cohort_count bigint;
            missing_taxonomy_count bigint;
            computed_checksum varchar;
        BEGIN
            IF p_expected_checksum IS NULL OR p_expected_checksum !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'strategy label release checksum must be SHA-256';
            END IF;

            SELECT * INTO label
              FROM agri.strategy_label_release
             WHERE id = p_label_release_id
             FOR UPDATE;
            IF NOT FOUND OR label.status NOT IN ('staging', 'validated') THEN
                RAISE EXCEPTION 'strategy label release is missing or not eligible for finalization';
            END IF;

            SELECT * INTO pinned_release
              FROM agri.release_set
             WHERE id = label.release_set_id;
            SELECT * INTO outcome
              FROM agri.strategy_outcome_definition
             WHERE id = label.outcome_definition_id;
            IF pinned_release.state NOT IN ('validated', 'published')
               OR pinned_release.validated_at IS NULL
               OR pinned_release.manifest_checksum IS NULL THEN
                RAISE EXCEPTION 'strategy label release requires a validated pinned release set';
            END IF;
            IF outcome.review_state <> 'approved' THEN
                RAISE EXCEPTION 'strategy label release requires an approved outcome definition';
            END IF;
            IF outcome.definition_checksum IS DISTINCT FROM
                agri.strategy_outcome_definition_checksum(outcome) THEN
                RAISE EXCEPTION 'strategy label release outcome definition checksum mismatch';
            END IF;
            IF label.strategy_taxonomy_checksum <> encode(
                digest(label.strategy_taxonomy_snapshot::text, 'sha256'),
                'hex'
            ) THEN
                RAISE EXCEPTION 'strategy label release taxonomy snapshot checksum mismatch';
            END IF;
            IF label.feature_schema_checksum <> encode(
                digest(label.feature_schema::text, 'sha256'),
                'hex'
            )
               OR jsonb_typeof(label.feature_schema) <> 'array'
               OR jsonb_array_length(label.feature_schema) = 0
               OR EXISTS (
                    SELECT 1
                      FROM jsonb_array_elements(label.feature_schema) AS feature(value)
                     WHERE jsonb_typeof(feature.value) <> 'string'
                        OR btrim(feature.value #>> '{}') = ''
               )
               OR (
                    SELECT count(*) <> count(DISTINCT feature.value #>> '{}')
                      FROM jsonb_array_elements(label.feature_schema) AS feature(value)
               ) THEN
                RAISE EXCEPTION 'strategy label release feature schema is invalid or checksum-mismatched';
            END IF;

            SELECT
                count(*),
                count(*) FILTER (WHERE episode.arm_kind = 'treatment'),
                count(*) FILTER (WHERE episode.arm_kind = 'control'),
                count(DISTINCT episode.strategy_id) FILTER (WHERE episode.strategy_id IS NOT NULL),
                count(DISTINCT episode.spatial_block_key),
                count(DISTINCT episode.analysis_subject_id),
                count(*) FILTER (
                    WHERE baseline.analysis_subject_id <> episode.analysis_subject_id
                       OR observed.analysis_subject_id <> episode.analysis_subject_id
                       OR baseline.release_set_id <> label.release_set_id
                       OR observed.release_set_id <> label.release_set_id
                       OR baseline.evidence_kind <> 'observed_fact'
                       OR observed.evidence_kind <> 'observed_fact'
                       OR baseline.metric_name <> outcome.metric_name
                       OR observed.metric_name <> outcome.metric_name
                       OR baseline.numeric_value IS NULL
                       OR observed.numeric_value IS NULL
                       OR baseline.value_unit IS DISTINCT FROM outcome.metric_unit
                       OR observed.value_unit IS DISTINCT FROM outcome.metric_unit
                       OR episode.target_unit <> outcome.metric_unit
                       OR episode.covariate_checksum <> encode(
                            digest(episode.covariate_snapshot::text, 'sha256'),
                            'hex'
                       )
                       OR jsonb_typeof(episode.covariate_snapshot) <> 'array'
                       OR jsonb_array_length(episode.covariate_snapshot) <>
                            jsonb_array_length(label.feature_schema)
                       OR EXISTS (
                            SELECT 1
                              FROM jsonb_array_elements(episode.covariate_snapshot) AS covariate(value)
                             WHERE jsonb_typeof(covariate.value) <> 'number'
                       )
                       OR episode.covariates_available_at > episode.assigned_at
                       OR baseline.data_available_at > label.as_of_time
                       OR observed.data_available_at > label.as_of_time
                       OR episode.data_available_at > label.as_of_time
                       OR episode.outcome_end > episode.data_available_at
                       OR baseline.observed_from IS NULL
                       OR baseline.observed_to IS NULL
                       OR observed.observed_from IS NULL
                       OR observed.observed_to IS NULL
                       OR baseline.observed_from > episode.baseline_start
                       OR baseline.observed_to < episode.baseline_end
                       OR observed.observed_from > episode.outcome_start
                       OR observed.observed_to < episode.outcome_end
                       OR episode.episode_checksum IS DISTINCT FROM
                            agri.strategy_label_episode_checksum(episode.id)
                       OR episode.baseline_end - episode.baseline_start <> outcome.baseline_window
                       OR episode.outcome_end - episode.outcome_start <> outcome.outcome_window
                       OR episode.target_value IS DISTINCT FROM CASE outcome.benefit_direction
                            WHEN 'increase' THEN observed.numeric_value - baseline.numeric_value
                            WHEN 'decrease' THEN baseline.numeric_value - observed.numeric_value
                       END
                )
              INTO
                actual_row_count,
                actual_treated_count,
                actual_control_count,
                actual_strategy_count,
                actual_spatial_block_count,
                distinct_subject_count,
                invalid_episode_count
              FROM agri.strategy_label_episode AS episode
              INNER JOIN agri.intervention_evidence_input AS baseline
                ON baseline.id = episode.baseline_evidence_input_id
              INNER JOIN agri.intervention_evidence_input AS observed
                ON observed.id = episode.outcome_evidence_input_id
             WHERE episode.label_release_id = label.id;

            SELECT count(*) INTO missing_taxonomy_count
              FROM agri.strategy_label_episode AS episode
             WHERE episode.label_release_id = label.id
               AND episode.arm_kind = 'treatment'
               AND NOT EXISTS (
                    SELECT 1
                      FROM jsonb_array_elements(label.strategy_taxonomy_snapshot) AS item
                     WHERE item ->> 'strategy_id' = episode.strategy_id::text
               );

            SELECT count(*) INTO invalid_cohort_count
              FROM (
                    SELECT episode.cohort_key
                      FROM agri.strategy_label_episode AS episode
                     WHERE episode.label_release_id = label.id
                     GROUP BY episode.cohort_key
                    HAVING count(DISTINCT episode.assigned_at) <> 1
              ) AS invalid_cohort;

            IF actual_row_count <> label.row_count
               OR actual_treated_count <> label.treated_count
               OR actual_control_count <> label.control_count
               OR actual_strategy_count <> label.strategy_count
               OR actual_spatial_block_count <> label.spatial_block_count
               OR distinct_subject_count <> label.row_count THEN
                RAISE EXCEPTION 'strategy label release declared counts do not match persisted episodes';
            END IF;
            IF invalid_episode_count > 0 THEN
                RAISE EXCEPTION 'strategy label release contains lineage, feature, availability, window, metric, or unit mismatch';
            END IF;
            IF missing_taxonomy_count > 0 THEN
                RAISE EXCEPTION 'strategy label release treatment is absent from the pinned taxonomy snapshot';
            END IF;
            IF invalid_cohort_count > 0 THEN
                RAISE EXCEPTION 'strategy label release cohort maps to multiple assignment times';
            END IF;

            computed_checksum := agri.strategy_label_release_checksum(label.id);
            IF computed_checksum IS DISTINCT FROM p_expected_checksum THEN
                RAISE EXCEPTION 'strategy label release checksum mismatch';
            END IF;

            IF label.status = 'staging' THEN
                UPDATE agri.strategy_label_release
                   SET status = 'validated',
                       receipt_checksum = computed_checksum,
                       validated_at = now()
                 WHERE id = label.id
                 RETURNING * INTO label;
            ELSIF label.receipt_checksum IS DISTINCT FROM computed_checksum THEN
                RAISE EXCEPTION 'validated strategy label release no longer matches its receipt';
            END IF;
            RETURN label;
        END
    $_$;
"""  # noqa: E501

_GUARD_STRATEGY_SELECTION_RECEIPT_CHANGE = r"""
CREATE OR REPLACE FUNCTION agri.guard_strategy_selection_receipt_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $_$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'only verified staging-to-finalized strategy selection transition is allowed';
            END IF;
            IF OLD.status = 'finalized' THEN
                -- The only write a finalized receipt accepts is a one-way audit flag; the
                -- receipt, its checksum, and its lineage stay byte-identical.
                IF NEW.status <> 'finalized'
                   OR OLD.audit_state <> 'clear'
                   OR NEW.audit_state <> 'cutoff_violation'
                   OR NEW.audit_reason IS NULL
                   OR NEW.audit_flagged_at IS NULL
                   OR ROW(
                        NEW.id,
                        NEW.selection_key,
                        NEW.analysis_subject_id,
                        NEW.forecast_receipt_id,
                        NEW.forecast_iteration_id,
                        NEW.feature_snapshot_id,
                        NEW.training_run_id,
                        NEW.selection_policy_id,
                        NEW.issue_time,
                        NEW.applicability_start,
                        NEW.applicability_end,
                        NEW.data_cutoff,
                        NEW.execution_mode,
                        NEW.claim_tier,
                        NEW.decision_state,
                        NEW.abstention_reason,
                        NEW.candidate_count,
                        NEW.receipt_checksum,
                        NEW.finalized_at,
                        NEW.created_at
                   ) IS DISTINCT FROM ROW(
                        OLD.id,
                        OLD.selection_key,
                        OLD.analysis_subject_id,
                        OLD.forecast_receipt_id,
                        OLD.forecast_iteration_id,
                        OLD.feature_snapshot_id,
                        OLD.training_run_id,
                        OLD.selection_policy_id,
                        OLD.issue_time,
                        OLD.applicability_start,
                        OLD.applicability_end,
                        OLD.data_cutoff,
                        OLD.execution_mode,
                        OLD.claim_tier,
                        OLD.decision_state,
                        OLD.abstention_reason,
                        OLD.candidate_count,
                        OLD.receipt_checksum,
                        OLD.finalized_at,
                        OLD.created_at
                   ) THEN
                    RAISE EXCEPTION
                        'a finalized strategy selection accepts only a one-way cutoff_violation audit flag';
                END IF;
                RETURN NEW;
            END IF;
            IF OLD.status <> 'staging'
               OR NEW.status <> 'finalized'
               OR ROW(
                    NEW.id,
                    NEW.selection_key,
                    NEW.analysis_subject_id,
                    NEW.forecast_receipt_id,
                    NEW.forecast_iteration_id,
                    NEW.feature_snapshot_id,
                    NEW.training_run_id,
                    NEW.selection_policy_id,
                    NEW.issue_time,
                    NEW.applicability_start,
                    NEW.applicability_end,
                    NEW.data_cutoff,
                    NEW.execution_mode,
                    NEW.claim_tier,
                    NEW.decision_state,
                    NEW.abstention_reason,
                    NEW.candidate_count,
                    NEW.audit_state,
                    NEW.audit_reason,
                    NEW.audit_flagged_at,
                    NEW.created_at
               ) IS DISTINCT FROM ROW(
                    OLD.id,
                    OLD.selection_key,
                    OLD.analysis_subject_id,
                    OLD.forecast_receipt_id,
                    OLD.forecast_iteration_id,
                    OLD.feature_snapshot_id,
                    OLD.training_run_id,
                    OLD.selection_policy_id,
                    OLD.issue_time,
                    OLD.applicability_start,
                    OLD.applicability_end,
                    OLD.data_cutoff,
                    OLD.execution_mode,
                    OLD.claim_tier,
                    OLD.decision_state,
                    OLD.abstention_reason,
                    OLD.candidate_count,
                    OLD.audit_state,
                    OLD.audit_reason,
                    OLD.audit_flagged_at,
                    OLD.created_at
               )
               OR NEW.receipt_checksum !~ '^[0-9a-f]{64}$'
               OR NEW.finalized_at IS NULL THEN
                RAISE EXCEPTION 'only verified staging-to-finalized strategy selection transition is allowed';
            END IF;
            RETURN NEW;
        END
    $_$;
"""

_FINALIZE_STRATEGY_SELECTION_RECEIPT = r"""
CREATE OR REPLACE FUNCTION agri.finalize_strategy_selection_receipt(p_selection_receipt_id uuid, p_expected_checksum character varying) RETURNS agri.strategy_selection_receipt
    LANGUAGE plpgsql
    SET "TimeZone" TO 'UTC'
    SET "DateStyle" TO 'ISO, MDY'
    SET "IntervalStyle" TO 'postgres'
    SET extra_float_digits TO '1'
    SET search_path TO 'public', 'pg_catalog'
    AS $_$
        DECLARE
            receipt agri.strategy_selection_receipt;
            training agri.forecast_training_run;
            model agri.forecast_model;
            feature agri.forecast_feature_snapshot;
            label agri.strategy_label_release;
            policy agri.strategy_selection_policy;
            actual_candidate_count integer;
            ranked_candidate_count integer;
            minimum_rank integer;
            maximum_rank integer;
            invalid_candidate_count integer;
            computed_checksum varchar;
        BEGIN
            IF p_expected_checksum IS NULL OR p_expected_checksum !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'strategy selection receipt checksum must be SHA-256';
            END IF;

            SELECT * INTO receipt
              FROM agri.strategy_selection_receipt
             WHERE id = p_selection_receipt_id
             FOR UPDATE;
            IF NOT FOUND OR receipt.status NOT IN ('staging', 'finalized') THEN
                RAISE EXCEPTION 'strategy selection receipt is missing or not eligible for finalization';
            END IF;
            IF receipt.audit_state <> 'clear' THEN
                RAISE EXCEPTION
                    'strategy selection receipt is flagged % and cannot be finalized', receipt.audit_state;
            END IF;

            SELECT * INTO training
              FROM agri.forecast_training_run
             WHERE id = receipt.training_run_id;
            SELECT * INTO model
              FROM agri.forecast_model
             WHERE id = training.model_id;
            SELECT * INTO feature
              FROM agri.forecast_feature_snapshot
             WHERE id = receipt.feature_snapshot_id;
            SELECT * INTO label
              FROM agri.strategy_label_release
             WHERE id = training.strategy_label_release_id;
            SELECT * INTO policy
              FROM agri.strategy_selection_policy
             WHERE id = receipt.selection_policy_id;

            IF training.status <> 'validated'
               OR model.model_purpose <> 'strategy_selection'
               OR training.feature_snapshot_id <> receipt.feature_snapshot_id
               OR training.strategy_label_release_id IS NULL
               OR training.strategy_label_checksum IS DISTINCT FROM label.receipt_checksum
               OR feature.status <> 'validated'
               OR feature.job_output_id IS NULL
               OR label.status <> 'validated'
               OR policy.review_state <> 'approved' THEN
                RAISE EXCEPTION 'strategy selection lineage is not validated and policy-approved';
            END IF;
            IF policy.policy_checksum IS DISTINCT FROM
                agri.strategy_selection_policy_checksum(policy) THEN
                RAISE EXCEPTION 'strategy selection policy checksum mismatch';
            END IF;
            IF label.as_of_time > receipt.data_cutoff
               OR feature.training_window_end > receipt.data_cutoff THEN
                RAISE EXCEPTION 'strategy selection inputs became available after the declared cutoff';
            END IF;

            IF receipt.execution_mode = 'evaluation_only' THEN
                IF NOT EXISTS (
                    SELECT 1
                      FROM agri.forecast_iteration AS iteration
                     WHERE iteration.id = receipt.forecast_iteration_id
                       AND iteration.status = 'finalized'
                       AND iteration.as_of_time = receipt.issue_time
                ) THEN
                    RAISE EXCEPTION 'evaluation-only strategy selection requires its finalized forecast iteration';
                END IF;
                IF agri.strategy_selection_cutoff_violation(receipt.id) THEN
                    RAISE EXCEPTION
                        'strategy selection forecast iteration cutoff is later than the declared data cutoff';
                END IF;
            ELSIF NOT EXISTS (
                SELECT 1
                  FROM agri.forecast_receipt AS forecast
                  INNER JOIN agri.forecast_publication_item AS item
                    ON item.forecast_receipt_id = forecast.id
                  INNER JOIN agri.forecast_publication AS publication
                    ON publication.id = item.publication_id
                 WHERE forecast.id = receipt.forecast_receipt_id
                   AND forecast.status = 'finalized'
                   AND forecast.issue_time = receipt.issue_time
                   AND publication.state = 'published'
            ) THEN
                RAISE EXCEPTION 'publishable strategy selection requires a published finalized forecast receipt';
            END IF;

            IF NOT agri.strategy_selection_quality_evidence(receipt.id) THEN
                IF receipt.execution_mode = 'evaluation_only' THEN
                    RAISE EXCEPTION
                        'strategy selection requires a finalized quality-passed hindcast for its backing series';
                ELSE
                    RAISE EXCEPTION
                        'strategy selection requires a finalized quality-passed hindcast for its backing series and model';
                END IF;
            END IF;

            SELECT
                count(*),
                count(*) FILTER (WHERE candidate.rank IS NOT NULL),
                min(candidate.rank),
                max(candidate.rank),
                count(*) FILTER (
                    WHERE candidate.strategy_snapshot ->> 'strategy_id' IS DISTINCT FROM candidate.strategy_id::text
                       OR candidate.strategy_snapshot_checksum <> encode(
                            digest(candidate.strategy_snapshot::text, 'sha256'),
                            'hex'
                       )
                       OR candidate.candidate_checksum IS DISTINCT FROM
                            agri.strategy_selection_candidate_checksum(candidate.id)
                       OR (candidate.rank IS NOT NULL AND candidate.eligibility_state <> 'eligible')
                )
              INTO
                actual_candidate_count,
                ranked_candidate_count,
                minimum_rank,
                maximum_rank,
                invalid_candidate_count
              FROM agri.strategy_selection_candidate AS candidate
             WHERE candidate.selection_receipt_id = receipt.id;

            IF actual_candidate_count <> receipt.candidate_count THEN
                RAISE EXCEPTION 'strategy selection candidate count mismatch';
            END IF;
            IF invalid_candidate_count > 0 THEN
                RAISE EXCEPTION 'strategy selection candidate snapshot, checksum, eligibility, or rank is invalid';
            END IF;
            IF receipt.decision_state = 'ranked'
               AND (
                    ranked_candidate_count < 1
                    OR minimum_rank <> 1
                    OR maximum_rank <> ranked_candidate_count
               ) THEN
                RAISE EXCEPTION 'ranked strategy selection requires contiguous eligible ranks';
            END IF;
            IF receipt.decision_state = 'abstained' AND ranked_candidate_count <> 0 THEN
                RAISE EXCEPTION 'abstained strategy selection cannot retain ranked candidates';
            END IF;
            IF receipt.claim_tier = 'feasibility_candidate'
               AND EXISTS (
                    SELECT 1
                      FROM agri.strategy_selection_candidate
                     WHERE selection_receipt_id = receipt.id
                       AND evidence_tier <> 'feasibility_candidate'
               ) THEN
                RAISE EXCEPTION 'feasibility receipt cannot contain effect-tier candidates';
            END IF;

            IF receipt.claim_tier = 'effect_candidate' THEN
                -- A later revision must add cluster-bootstrap, placebo,
                -- negative-control, and best-vs-second lower-bound gates.
                RAISE EXCEPTION
                    'effect_candidate finalization is disabled in strategy_selection_v1';
            END IF;

            computed_checksum := agri.strategy_selection_receipt_checksum(receipt.id);
            IF computed_checksum IS DISTINCT FROM p_expected_checksum THEN
                RAISE EXCEPTION 'strategy selection receipt checksum mismatch';
            END IF;

            IF receipt.status = 'staging' THEN
                UPDATE agri.strategy_selection_receipt
                   SET status = 'finalized',
                       receipt_checksum = computed_checksum,
                       finalized_at = now()
                 WHERE id = receipt.id
                 RETURNING * INTO receipt;
            ELSIF receipt.receipt_checksum IS DISTINCT FROM computed_checksum THEN
                RAISE EXCEPTION 'finalized strategy selection no longer matches its receipt';
            END IF;
            RETURN receipt;
        END
    $_$;
"""  # noqa: E501

_V_FORECAST_HINDCAST_OUTCOME = r"""
CREATE OR REPLACE VIEW agri.v_forecast_hindcast_outcome AS
 SELECT hindcast.id AS hindcast_run_id,
    hindcast.hindcast_key,
    hindcast.forecast_run_id,
    parent.model_id,
    model.model_key,
    model.model_version,
    hindcast.series_id,
    series.series_key,
    series.entity_type,
    series.entity_key,
    series.metric_name,
    series.metric_unit,
    series.spatial_cell_id,
    series.spatial_support_kind,
    series.source_spatial_resolution_m,
    hindcast.release_set_id,
    hindcast.input_release_checksum,
    hindcast.simulated_cutoff_time,
    hindcast.uncertainty_calibration_cutoff_time,
    hindcast.availability_mode,
    hindcast.recorded_at AS signal_available_at,
    hindcast.receipt_checksum,
    hindcast.quality_passed,
    value.valid_time,
    value.horizon_step,
    value.point_value,
    value.p10_value,
    value.p50_value,
    value.p90_value,
    value.naive_value,
    value.actual_value,
    value.actual_source_release_id,
    value.actual_observation_checksum,
    value.actual_data_available_at,
    value.residual_value,
    (value.point_value - value.actual_value) AS forecast_error,
    value.absolute_error,
    value.squared_error,
    value.interval_covered,
    value.value_checksum,
    'forecast_evaluation_v1'::text AS signal_contract_version,
    hindcast.receipt_digest_version,
    parent.quality_policy_id,
    policy.policy_key,
    agri.forecast_quality_policy_contract_v2(policy.*) AS quality_policy_contract,
    hindcast.actual_knowledge_as_of,
    hindcast.coverage_fraction,
    hindcast.interval_coverage_fraction
   FROM (((((agri.forecast_hindcast_run hindcast
     JOIN agri.forecast_hindcast_value value ON ((value.hindcast_run_id = hindcast.id)))
     JOIN agri.forecast_run parent ON ((parent.id = hindcast.forecast_run_id)))
     JOIN agri.forecast_model model ON ((model.id = parent.model_id)))
     JOIN agri.forecast_quality_policy policy ON ((policy.id = parent.quality_policy_id)))
     JOIN agri.forecast_series series ON ((series.id = hindcast.series_id)))
  WHERE ((hindcast.status)::text = 'finalized'::text);
"""


def upgrade() -> None:
    _add_hindcast_knowledge_pin()
    _add_interval_coverage_policy()
    _add_selection_audit_flag()

    for function_sql in (
        load_object_sql("functions/forecast_quality_policy_contract_v2.sql"),
        load_object_sql("functions/strategy_selection_cutoff_violation.sql"),
        _STRATEGY_SELECTION_QUALITY_EVIDENCE,
    ):
        op.execute(function_sql)
    for function_sql in (
        _FORECAST_HINDCAST_RECEIPT_CHECKSUM,
        _FINALIZE_FORECAST_HINDCAST_RUN,
        _ENFORCE_FORECAST_HINDCAST_INSERT_CONTRACT,
        _ENFORCE_FORECAST_HINDCAST_FINALIZATION_POLICY,
        _FINALIZE_FORECAST_RECEIPT,
        _FINALIZE_STRATEGY_LABEL_RELEASE,
        load_object_sql("functions/require_strategy_initial_state.sql", or_replace=True),
        _GUARD_STRATEGY_SELECTION_RECEIPT_CHANGE,
        _FINALIZE_STRATEGY_SELECTION_RECEIPT,
    ):
        op.execute(function_sql)
    op.execute(_V_FORECAST_HINDCAST_OUTCOME)

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
