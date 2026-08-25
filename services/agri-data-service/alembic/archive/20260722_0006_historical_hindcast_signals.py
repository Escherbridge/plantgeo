"""Persist leakage-checked SQL hindcasts and forecast-versus-actual signals.

Revision ID: 20260722_0006
Revises: 20260722_0005
"""

from collections.abc import Sequence

from alembic import op

revision = "20260722_0006"
down_revision = "20260722_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE FUNCTION agri.forecast_hindcast_value_checksum(
            p_valid_time timestamptz,
            p_horizon_step integer,
            p_point_value double precision,
            p_p10_value double precision,
            p_p50_value double precision,
            p_p90_value double precision,
            p_naive_value double precision,
            p_actual_value double precision,
            p_actual_source_release_id uuid,
            p_actual_observation_checksum varchar
        )
        RETURNS varchar
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        SET TimeZone = 'UTC'
        AS $$
            SELECT encode(public.digest(concat_ws('|',
                to_char(p_valid_time AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                p_horizon_step::text,
                p_point_value::text,
                p_p10_value::text,
                p_p50_value::text,
                p_p90_value::text,
                p_naive_value::text,
                p_actual_value::text,
                p_actual_source_release_id::text,
                p_actual_observation_checksum
            ), 'sha256'), 'hex')::varchar
        $$;

        CREATE TABLE agri.forecast_hindcast_run (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            hindcast_key varchar(255) NOT NULL UNIQUE,
            forecast_run_id uuid NOT NULL REFERENCES agri.forecast_run(id),
            series_id uuid NOT NULL REFERENCES agri.forecast_series(id),
            release_set_id uuid NOT NULL REFERENCES agri.release_set(id),
            simulated_cutoff_time timestamptz NOT NULL,
            uncertainty_calibration_cutoff_time timestamptz NOT NULL,
            horizon_steps integer NOT NULL,
            step_interval interval NOT NULL,
            minimum_training_points integer NOT NULL,
            training_point_count integer NOT NULL,
            expected_value_count integer NOT NULL,
            availability_mode varchar(40) NOT NULL DEFAULT 'retrospective_pinned_release',
            input_release_checksum varchar(64) NOT NULL,
            model_checksum varchar(64) NOT NULL,
            parameter_checksum varchar(64) NOT NULL,
            status varchar(24) NOT NULL DEFAULT 'staging',
            quality_passed boolean NOT NULL DEFAULT false,
            mae double precision,
            rmse double precision,
            naive_rmse double precision,
            skill_score double precision,
            bias double precision,
            mape double precision,
            coverage_fraction double precision,
            interval_coverage_fraction double precision,
            receipt_checksum varchar(64),
            recorded_at timestamptz,
            finalized_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_forecast_hindcast_run_identity UNIQUE(
                forecast_run_id, series_id, simulated_cutoff_time,
                input_release_checksum, model_checksum, parameter_checksum
            ),
            CONSTRAINT ck_forecast_hindcast_run_calibration_cutoff CHECK (
                uncertainty_calibration_cutoff_time < simulated_cutoff_time
            ),
            CONSTRAINT ck_forecast_hindcast_run_horizon CHECK (
                horizon_steps > 0 AND expected_value_count = horizon_steps
                AND step_interval > interval '0'
            ),
            CONSTRAINT ck_forecast_hindcast_run_training_count CHECK (
                minimum_training_points >= 3 AND training_point_count >= minimum_training_points
            ),
            CONSTRAINT ck_forecast_hindcast_run_availability CHECK (
                availability_mode IN ('as_recorded', 'retrospective_pinned_release')
            ),
            CONSTRAINT ck_forecast_hindcast_run_checksums CHECK (
                input_release_checksum ~ '^[0-9a-f]{64}$'
                AND model_checksum ~ '^[0-9a-f]{64}$'
                AND parameter_checksum ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_forecast_hindcast_run_status CHECK (status IN ('staging', 'finalized')),
            CONSTRAINT ck_forecast_hindcast_run_finalized_evidence CHECK (
                status <> 'finalized' OR (
                    receipt_checksum ~ '^[0-9a-f]{64}$'
                    AND recorded_at IS NOT NULL
                    AND finalized_at IS NOT NULL
                    AND mae >= 0
                    AND rmse >= 0
                    AND naive_rmse >= 0
                    AND (skill_score IS NULL OR skill_score <= 1)
                    AND (mape IS NULL OR mape >= 0)
                    AND coverage_fraction BETWEEN 0 AND 1
                    AND interval_coverage_fraction BETWEEN 0 AND 1
                )
            )
        );
        CREATE INDEX ix_forecast_hindcast_run_series_cutoff
            ON agri.forecast_hindcast_run(series_id, simulated_cutoff_time);
        CREATE INDEX ix_forecast_hindcast_run_parent
            ON agri.forecast_hindcast_run(forecast_run_id);

        CREATE TABLE agri.forecast_hindcast_value (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            hindcast_run_id uuid NOT NULL REFERENCES agri.forecast_hindcast_run(id),
            valid_time timestamptz NOT NULL,
            horizon_step integer NOT NULL,
            point_value double precision NOT NULL,
            p10_value double precision NOT NULL,
            p50_value double precision NOT NULL,
            p90_value double precision NOT NULL,
            naive_value double precision NOT NULL,
            actual_value double precision NOT NULL,
            actual_source_release_id uuid NOT NULL REFERENCES agri.source_release(id),
            actual_observation_checksum varchar(64) NOT NULL,
            actual_data_available_at timestamptz NOT NULL,
            residual_value double precision GENERATED ALWAYS AS (actual_value - point_value) STORED,
            absolute_error double precision GENERATED ALWAYS AS (abs(actual_value - point_value)) STORED,
            squared_error double precision GENERATED ALWAYS AS (
                (actual_value - point_value) * (actual_value - point_value)
            ) STORED,
            interval_covered boolean GENERATED ALWAYS AS (
                actual_value >= p10_value AND actual_value <= p90_value
            ) STORED,
            value_checksum varchar(64) GENERATED ALWAYS AS (
                agri.forecast_hindcast_value_checksum(
                    valid_time, horizon_step, point_value, p10_value, p50_value, p90_value,
                    naive_value, actual_value, actual_source_release_id,
                    actual_observation_checksum
                )
            ) STORED,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_forecast_hindcast_value_time UNIQUE(hindcast_run_id, valid_time),
            CONSTRAINT uq_forecast_hindcast_value_horizon UNIQUE(hindcast_run_id, horizon_step),
            CONSTRAINT ck_forecast_hindcast_value_horizon CHECK (horizon_step > 0),
            CONSTRAINT ck_forecast_hindcast_value_bands CHECK (
                p10_value <= p50_value AND p50_value <= p90_value
            ),
            CONSTRAINT ck_forecast_hindcast_value_actual_checksum CHECK (
                actual_observation_checksum ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_forecast_hindcast_value_finite CHECK (
                point_value::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND p10_value::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND p50_value::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND p90_value::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND naive_value::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND actual_value::text NOT IN ('NaN', 'Infinity', '-Infinity')
            )
        );
        CREATE INDEX ix_forecast_hindcast_value_run_time
            ON agri.forecast_hindcast_value(hindcast_run_id, valid_time);
        """
    )

    op.execute(
        r"""
        CREATE FUNCTION agri.forecast_hindcast_receipt_checksum(p_hindcast_run_id uuid)
        RETURNS varchar
        LANGUAGE sql
        STABLE
        SET TimeZone = 'UTC'
        AS $$
            SELECT encode(public.digest(concat_ws('|',
                run.hindcast_key,
                to_char(run.simulated_cutoff_time AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                to_char(run.uncertainty_calibration_cutoff_time AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                run.availability_mode,
                run.input_release_checksum,
                run.model_checksum,
                run.parameter_checksum,
                coalesce(string_agg(value.value_checksum, E'\n' ORDER BY value.horizon_step), '')
            ), 'sha256'), 'hex')::varchar
            FROM agri.forecast_hindcast_run AS run
            LEFT JOIN agri.forecast_hindcast_value AS value ON value.hindcast_run_id = run.id
            WHERE run.id = p_hindcast_run_id
            GROUP BY run.id
        $$;

        CREATE FUNCTION agri.finalize_forecast_hindcast_run(
            p_hindcast_run_id uuid,
            p_expected_checksum varchar
        )
        RETURNS agri.forecast_hindcast_run
        LANGUAGE plpgsql
        AS $$
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
            SELECT * INTO target
              FROM agri.forecast_hindcast_run
             WHERE id = p_hindcast_run_id
             FOR UPDATE;
            IF NOT FOUND OR target.status NOT IN ('staging', 'finalized') THEN
                RAISE EXCEPTION 'hindcast run is missing or not finalizable';
            END IF;
            IF p_expected_checksum !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'hindcast receipt checksum must be SHA-256';
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
            knowledge_as_of := CASE
                WHEN target.availability_mode = 'as_recorded' THEN target.simulated_cutoff_time
                ELSE clock_timestamp()
            END;

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
                        target.release_set_id, clock_timestamp()
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

            SELECT
                avg(value.absolute_error),
                sqrt(avg(value.squared_error)),
                sqrt(avg((value.naive_value - value.actual_value)
                    * (value.naive_value - value.actual_value))),
                avg(value.point_value - value.actual_value),
                avg(value.absolute_error / nullif(abs(value.actual_value), 0)),
                count(*)::double precision / target.expected_value_count,
                avg(CASE WHEN value.interval_covered THEN 1.0 ELSE 0.0 END)
              INTO computed_mae, computed_rmse, computed_naive_rmse,
                   computed_bias, computed_mape, computed_coverage,
                   computed_interval_coverage
              FROM agri.forecast_hindcast_value AS value
             WHERE value.hindcast_run_id = target.id;
            computed_skill := CASE
                WHEN computed_naive_rmse = 0 AND computed_rmse = 0 THEN 1.0
                WHEN computed_naive_rmse = 0 THEN NULL
                ELSE 1.0 - computed_rmse / computed_naive_rmse
            END;
            computed_pass := target.training_point_count >= policy.min_training_points
                AND target.expected_value_count >= policy.min_backtest_points
                AND computed_coverage >= policy.min_coverage_fraction
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
                IF target.receipt_checksum IS DISTINCT FROM computed_checksum
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
                   receipt_checksum = computed_checksum,
                   recorded_at = clock_timestamp(),
                   finalized_at = clock_timestamp()
             WHERE id = target.id
             RETURNING * INTO target;
            RETURN target;
        END
        $$;
        """
    )

    op.execute(
        r"""
        CREATE FUNCTION agri.guard_forecast_hindcast_value_write()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE parent_status varchar;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'hindcast values are append-only';
            END IF;
            SELECT status INTO parent_status
              FROM agri.forecast_hindcast_run
             WHERE id = NEW.hindcast_run_id
             FOR SHARE;
            IF parent_status <> 'staging' THEN
                RAISE EXCEPTION 'hindcast values are writable only while their run is staging';
            END IF;
            RETURN NEW;
        END
        $$;
        CREATE TRIGGER forecast_hindcast_value_write_guard
            BEFORE INSERT OR UPDATE OR DELETE ON agri.forecast_hindcast_value
            FOR EACH ROW EXECUTE FUNCTION agri.guard_forecast_hindcast_value_write();

        CREATE FUNCTION agri.guard_forecast_hindcast_run_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.status = 'finalized' THEN
                RAISE EXCEPTION 'finalized hindcast runs are immutable';
            END IF;
            IF ROW(
                NEW.id, NEW.hindcast_key, NEW.forecast_run_id, NEW.series_id,
                NEW.release_set_id, NEW.simulated_cutoff_time,
                NEW.uncertainty_calibration_cutoff_time, NEW.horizon_steps,
                NEW.step_interval, NEW.minimum_training_points, NEW.training_point_count,
                NEW.expected_value_count,
                NEW.availability_mode, NEW.input_release_checksum, NEW.model_checksum,
                NEW.parameter_checksum, NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.id, OLD.hindcast_key, OLD.forecast_run_id, OLD.series_id,
                OLD.release_set_id, OLD.simulated_cutoff_time,
                OLD.uncertainty_calibration_cutoff_time, OLD.horizon_steps,
                OLD.step_interval, OLD.minimum_training_points, OLD.training_point_count,
                OLD.expected_value_count,
                OLD.availability_mode, OLD.input_release_checksum, OLD.model_checksum,
                OLD.parameter_checksum, OLD.created_at
            ) OR NEW.status <> 'finalized' THEN
                RAISE EXCEPTION 'hindcast identity is immutable and only finalization may update a staged run';
            END IF;
            RETURN NEW;
        END
        $$;
        CREATE TRIGGER forecast_hindcast_run_change_guard
            BEFORE UPDATE OR DELETE ON agri.forecast_hindcast_run
            FOR EACH ROW EXECUTE FUNCTION agri.guard_forecast_hindcast_run_change();

        CREATE FUNCTION agri.verify_forecast_hindcast_finalization()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.status = 'finalized' AND OLD.status <> 'finalized' THEN
                PERFORM agri.finalize_forecast_hindcast_run(NEW.id, NEW.receipt_checksum);
            END IF;
            RETURN NEW;
        END
        $$;
        CREATE TRIGGER forecast_hindcast_finalized_verify
            AFTER UPDATE OF status ON agri.forecast_hindcast_run
            FOR EACH ROW EXECUTE FUNCTION agri.verify_forecast_hindcast_finalization();

        CREATE VIEW agri.v_forecast_hindcast_outcome AS
        SELECT
            hindcast.id AS hindcast_run_id,
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
            value.point_value - value.actual_value AS forecast_error,
            value.absolute_error,
            value.squared_error,
            value.interval_covered,
            value.value_checksum,
            'forecast_evaluation_v1'::text AS signal_contract_version
        FROM agri.forecast_hindcast_run AS hindcast
        INNER JOIN agri.forecast_hindcast_value AS value ON value.hindcast_run_id = hindcast.id
        INNER JOIN agri.forecast_run AS parent ON parent.id = hindcast.forecast_run_id
        INNER JOIN agri.forecast_model AS model ON model.id = parent.model_id
        INNER JOIN agri.forecast_series AS series ON series.id = hindcast.series_id
        WHERE hindcast.status = 'finalized';

        CREATE FUNCTION agri.forecast_hindcast_signal_timeseries(
            p_series_id uuid,
            p_model_id uuid,
            p_signal_kind varchar,
            p_horizon_step integer,
            p_as_of_time timestamptz
        )
        RETURNS TABLE(
            observed_at timestamptz,
            metric_value double precision,
            signal_kind text,
            hindcast_run_id uuid,
            simulated_cutoff_time timestamptz,
            horizon_step integer,
            signal_available_at timestamptz,
            receipt_checksum text,
            value_checksum text,
            quality_passed boolean
        )
        LANGUAGE sql
        STABLE
        AS $$
            SELECT
                outcome.valid_time,
                CASE p_signal_kind
                    WHEN 'forecast_point' THEN outcome.point_value
                    WHEN 'actual' THEN outcome.actual_value
                    WHEN 'residual_actual_minus_forecast' THEN outcome.residual_value
                    WHEN 'absolute_error' THEN outcome.absolute_error
                    WHEN 'squared_error' THEN outcome.squared_error
                    WHEN 'interval_covered' THEN CASE WHEN outcome.interval_covered THEN 1.0 ELSE 0.0 END
                END,
                p_signal_kind::text,
                outcome.hindcast_run_id,
                outcome.simulated_cutoff_time,
                outcome.horizon_step,
                outcome.signal_available_at,
                outcome.receipt_checksum::text,
                outcome.value_checksum::text,
                outcome.quality_passed
            FROM agri.v_forecast_hindcast_outcome AS outcome
            WHERE outcome.series_id = p_series_id
              AND outcome.model_id = p_model_id
              AND outcome.horizon_step = p_horizon_step
              AND outcome.signal_available_at <= p_as_of_time
              AND p_signal_kind IN (
                  'forecast_point', 'actual', 'residual_actual_minus_forecast',
                  'absolute_error', 'squared_error', 'interval_covered'
              )
            ORDER BY outcome.valid_time, outcome.hindcast_run_id
        $$;

        REVOKE ALL PRIVILEGES ON TABLE
            agri.forecast_hindcast_run,
            agri.forecast_hindcast_value,
            agri.v_forecast_hindcast_outcome
        FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON SEQUENCE agri.forecast_hindcast_value_id_seq FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.forecast_hindcast_value_checksum(
            timestamptz, integer, double precision, double precision, double precision,
            double precision, double precision, double precision, uuid, varchar
        ) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.forecast_hindcast_receipt_checksum(uuid) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.finalize_forecast_hindcast_run(uuid, varchar) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.forecast_hindcast_signal_timeseries(
            uuid, uuid, varchar, integer, timestamptz
        ) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.guard_forecast_hindcast_value_write() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.guard_forecast_hindcast_run_change() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.verify_forecast_hindcast_finalization() FROM PUBLIC;
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Forecast hindcast evidence is forward-only; restore a verified backup into a fresh database."
    )
