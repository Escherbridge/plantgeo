"""Version hindcast receipts and enforce finalization policy guards.

Revision ID: 20260722_0008
Revises: 20260722_0007
"""

from collections.abc import Sequence

from alembic import op

revision = "20260722_0008"
down_revision = "20260722_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE agri.forecast_hindcast_run
        ADD COLUMN receipt_digest_version varchar(32) NOT NULL DEFAULT 'hindcast_v1';

        ALTER TABLE agri.forecast_hindcast_run
        ALTER COLUMN receipt_digest_version SET DEFAULT 'hindcast_v2';

        ALTER TABLE agri.forecast_hindcast_run
        ADD CONSTRAINT ck_forecast_hindcast_run_receipt_digest_version
        CHECK (receipt_digest_version IN ('hindcast_v1', 'hindcast_v2'));
        """
    )

    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION agri.forecast_hindcast_receipt_checksum(
            p_hindcast_run_id uuid
        )
        RETURNS varchar
        LANGUAGE sql
        STABLE
        SET TimeZone = 'UTC'
        SET IntervalStyle = 'iso_8601'
        AS $$
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
                END,
                'sha256'
            ), 'hex')::varchar
            FROM agri.forecast_hindcast_run AS run
            INNER JOIN agri.forecast_run AS parent ON parent.id = run.forecast_run_id
            INNER JOIN agri.forecast_series AS series ON series.id = run.series_id
            INNER JOIN agri.forecast_model AS model ON model.id = parent.model_id
            INNER JOIN agri.forecast_quality_policy AS policy
                ON policy.id = parent.quality_policy_id
            WHERE run.id = p_hindcast_run_id
        $$;

        CREATE FUNCTION agri.enforce_forecast_hindcast_finalization_policy()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            policy agri.forecast_quality_policy;
            actual_calibration_samples bigint;
            current_quality_passed boolean;
            knowledge_as_of timestamptz;
        BEGIN
            IF NEW.status <> 'finalized' OR OLD.status = 'finalized' THEN
                RETURN NEW;
            END IF;
            IF NEW.receipt_digest_version <> 'hindcast_v2' THEN
                RAISE EXCEPTION 'new hindcast finalizations require receipt digest version hindcast_v2';
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

            knowledge_as_of := CASE
                WHEN NEW.availability_mode = 'as_recorded' THEN NEW.simulated_cutoff_time
                ELSE clock_timestamp()
            END;
            SELECT bands.backtest_point_count
              INTO actual_calibration_samples
              FROM agri.forecast_linear_residual_bands(
                    NEW.series_id,
                    NEW.release_set_id,
                    knowledge_as_of,
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

        CREATE FUNCTION agri.enforce_forecast_hindcast_insert_contract()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.status <> 'staging' THEN
                RAISE EXCEPTION 'new hindcast runs must begin in staging status';
            END IF;
            IF NEW.receipt_digest_version <> 'hindcast_v2' THEN
                RAISE EXCEPTION 'new hindcast runs require receipt digest version hindcast_v2';
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE FUNCTION agri.guard_forecast_quality_policy_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM agri.forecast_run AS parent
                INNER JOIN agri.forecast_hindcast_run AS hindcast
                    ON hindcast.forecast_run_id = parent.id
                WHERE parent.quality_policy_id = OLD.id
                  AND hindcast.status = 'finalized'
            ) THEN
                RAISE EXCEPTION
                    'quality policies referenced by finalized hindcast receipts are immutable';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END
        $$;

        DO $replace_hindcast_finalizer$
        DECLARE
            definition text;
            legacy_gate constant text :=
                'AND target.expected_value_count >= policy.min_backtest_points';
            versioned_gate constant text := $gate$
                AND (
                    (
                        target.receipt_digest_version = 'hindcast_v1'
                        AND target.expected_value_count >= policy.min_backtest_points
                    )
                    OR (
                        target.receipt_digest_version = 'hindcast_v2'
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
            $gate$;
        BEGIN
            SELECT pg_get_functiondef(
                'agri.finalize_forecast_hindcast_run(uuid,character varying)'::regprocedure
            ) INTO STRICT definition;
            IF strpos(definition, legacy_gate) = 0
               OR strpos(
                    substr(definition, strpos(definition, legacy_gate) + length(legacy_gate)),
                    legacy_gate
               ) > 0 THEN
                RAISE EXCEPTION 'unexpected predecessor hindcast finalizer definition';
            END IF;
            EXECUTE replace(definition, legacy_gate, versioned_gate);
        END
        $replace_hindcast_finalizer$;

        CREATE TRIGGER forecast_hindcast_insert_contract
            BEFORE INSERT ON agri.forecast_hindcast_run
            FOR EACH ROW
            EXECUTE FUNCTION agri.enforce_forecast_hindcast_insert_contract();

        CREATE TRIGGER forecast_hindcast_finalization_policy_guard
            BEFORE UPDATE OF status ON agri.forecast_hindcast_run
            FOR EACH ROW
            WHEN (NEW.status = 'finalized' AND OLD.status <> 'finalized')
            EXECUTE FUNCTION agri.enforce_forecast_hindcast_finalization_policy();

        CREATE TRIGGER forecast_quality_policy_finalized_receipt_guard
            BEFORE UPDATE OR DELETE ON agri.forecast_quality_policy
            FOR EACH ROW
            EXECUTE FUNCTION agri.guard_forecast_quality_policy_change();

        CREATE OR REPLACE FUNCTION agri.guard_forecast_hindcast_run_change()
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
                NEW.parameter_checksum, NEW.receipt_digest_version, NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.id, OLD.hindcast_key, OLD.forecast_run_id, OLD.series_id,
                OLD.release_set_id, OLD.simulated_cutoff_time,
                OLD.uncertainty_calibration_cutoff_time, OLD.horizon_steps,
                OLD.step_interval, OLD.minimum_training_points, OLD.training_point_count,
                OLD.expected_value_count,
                OLD.availability_mode, OLD.input_release_checksum, OLD.model_checksum,
                OLD.parameter_checksum, OLD.receipt_digest_version, OLD.created_at
            ) OR NEW.status <> 'finalized' THEN
                RAISE EXCEPTION 'hindcast identity is immutable and only finalization may update a staged run';
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE OR REPLACE VIEW agri.v_forecast_hindcast_outcome AS
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
            'forecast_evaluation_v1'::text AS signal_contract_version,
            hindcast.receipt_digest_version,
            parent.quality_policy_id,
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
            ) AS quality_policy_contract
        FROM agri.forecast_hindcast_run AS hindcast
        INNER JOIN agri.forecast_hindcast_value AS value
            ON value.hindcast_run_id = hindcast.id
        INNER JOIN agri.forecast_run AS parent ON parent.id = hindcast.forecast_run_id
        INNER JOIN agri.forecast_model AS model ON model.id = parent.model_id
        INNER JOIN agri.forecast_quality_policy AS policy
            ON policy.id = parent.quality_policy_id
        INNER JOIN agri.forecast_series AS series ON series.id = hindcast.series_id
        WHERE hindcast.status = 'finalized';

        REVOKE EXECUTE ON FUNCTION
            agri.enforce_forecast_hindcast_finalization_policy()
        FROM PUBLIC;

        REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA agri FROM PUBLIC;
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Hindcast receipt governance is forward-only; restore a verified backup into a fresh database."
    )
