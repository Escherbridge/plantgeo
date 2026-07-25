"""Add the release-pinned SQL forecasting and ML publication plane.

Revision ID: 20260722_0005
Revises: 20260720_0004
Create Date: 2026-07-22
"""

from alembic import op

revision = "20260722_0005"
down_revision = "20260720_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE FUNCTION agri.forecast_quantiles_valid(p_quantiles double precision[])
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE
        AS $$
            SELECT p_quantiles IS NOT NULL
               AND cardinality(p_quantiles) >= 1
               AND array_position(p_quantiles, NULL) IS NULL
               AND NOT EXISTS (
                    SELECT 1 FROM unnest(p_quantiles) AS quantile
                    WHERE quantile < 0 OR quantile > 1
               )
               AND (
                    SELECT count(*) = count(DISTINCT quantile)
                    FROM unnest(p_quantiles) AS quantile
               )
        $$;

        CREATE TABLE agri.forecast_series (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            series_key varchar(255) NOT NULL UNIQUE,
            source_variant_key varchar(255) NOT NULL,
            input_adapter varchar(32) NOT NULL,
            data_source_id uuid NOT NULL REFERENCES agri.data_source(id),
            signal_name varchar(150),
            source_parameter varchar(150),
            support_key varchar(150),
            source_transform_version varchar(100) NOT NULL,
            entity_type varchar(100) NOT NULL,
            entity_key varchar(255) NOT NULL,
            metric_name varchar(150) NOT NULL,
            metric_unit varchar(64) NOT NULL,
            spatial_cell_id uuid REFERENCES agri.spatial_cell(id),
            representation_kind varchar(24) NOT NULL,
            spatial_support_kind varchar(32) NOT NULL,
            source_spatial_resolution_m integer,
            output_spatial_resolution_m integer,
            source_temporal_support interval NOT NULL,
            output_temporal_support interval NOT NULL,
            aggregation_method varchar(100),
            allow_ml_daily_aggregate boolean NOT NULL DEFAULT false,
            metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_forecast_series_representation_kind
                CHECK (representation_kind IN ('raw_native', 'resampled', 'aggregate')),
            CONSTRAINT ck_forecast_series_input_adapter
                CHECK (input_adapter IN ('signal_observation', 'forecast_observation')),
            CONSTRAINT ck_forecast_series_signal_adapter_identity CHECK (
                input_adapter <> 'signal_observation'
                OR (
                    signal_name IS NOT NULL AND source_parameter IS NOT NULL
                    AND support_key IS NOT NULL AND spatial_cell_id IS NOT NULL
                )
            ),
            CONSTRAINT ck_forecast_series_spatial_support_kind
                CHECK (spatial_support_kind IN (
                    'native_grid_cell', 'native_polygon', 'point_sample', 'area_aggregate', 'unknown'
                )),
            CONSTRAINT ck_forecast_series_positive_resolutions CHECK (
                (source_spatial_resolution_m IS NULL OR source_spatial_resolution_m > 0)
                AND (output_spatial_resolution_m IS NULL OR output_spatial_resolution_m > 0)
            ),
            CONSTRAINT ck_forecast_series_positive_temporal_support
                CHECK (source_temporal_support > interval '0' AND output_temporal_support > interval '0'),
            CONSTRAINT ck_forecast_series_aggregate_method CHECK (
                (representation_kind = 'raw_native' AND aggregation_method IS NULL)
                OR (representation_kind IN ('resampled', 'aggregate') AND aggregation_method IS NOT NULL)
            )
        );

        CREATE INDEX ix_forecast_series_entity_metric
            ON agri.forecast_series(entity_type, entity_key, metric_name);
        CREATE INDEX ix_forecast_series_spatial_cell
            ON agri.forecast_series(spatial_cell_id) WHERE spatial_cell_id IS NOT NULL;
        CREATE UNIQUE INDEX uq_forecast_series_exact_source_identity
            ON agri.forecast_series(
                data_source_id, source_transform_version, input_adapter,
                signal_name, source_parameter, support_key, spatial_cell_id,
                entity_type, entity_key, metric_name, metric_unit,
                representation_kind, spatial_support_kind,
                source_temporal_support, output_temporal_support
            ) NULLS NOT DISTINCT;

        CREATE TABLE agri.forecast_entity_state (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            series_id uuid NOT NULL REFERENCES agri.forecast_series(id),
            source_release_id uuid NOT NULL REFERENCES agri.source_release(id),
            state_key varchar(255) NOT NULL,
            valid_from timestamptz NOT NULL,
            valid_to timestamptz,
            data_available_at timestamptz NOT NULL,
            state_checksum varchar(64) NOT NULL,
            state_geometry geometry(Geometry, 4326),
            state_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_forecast_entity_state_identity UNIQUE(series_id, state_key, state_checksum),
            CONSTRAINT ck_forecast_entity_state_ordered_window
                CHECK (valid_to IS NULL OR valid_to > valid_from),
            CONSTRAINT ck_forecast_entity_state_checksum_sha256
                CHECK (state_checksum ~ '^[0-9a-f]{64}$')
        );

        CREATE INDEX ix_forecast_entity_state_series_window
            ON agri.forecast_entity_state(series_id, valid_from, valid_to);
        CREATE INDEX ix_forecast_entity_state_geometry
            ON agri.forecast_entity_state USING gist(state_geometry)
            WHERE state_geometry IS NOT NULL;

        CREATE TABLE agri.forecast_observation (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            series_id uuid NOT NULL REFERENCES agri.forecast_series(id),
            entity_state_id uuid REFERENCES agri.forecast_entity_state(id),
            source_release_id uuid NOT NULL REFERENCES agri.source_release(id),
            observed_at timestamptz NOT NULL,
            valid_from timestamptz,
            valid_to timestamptz,
            data_available_at timestamptz NOT NULL,
            metric_value double precision NOT NULL,
            quality_flag varchar(64) NOT NULL DEFAULT 'accepted',
            source_event_key varchar(255) NOT NULL,
            observation_checksum varchar(64) NOT NULL,
            metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_forecast_observation_source_event
                UNIQUE(source_release_id, series_id, source_event_key),
            CONSTRAINT ck_forecast_observation_ordered_window
                CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from),
            CONSTRAINT ck_forecast_observation_checksum_sha256
                CHECK (observation_checksum ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_forecast_observation_finite_value
                CHECK (metric_value::text NOT IN ('NaN', 'Infinity', '-Infinity'))
        );

        CREATE INDEX ix_forecast_observation_series_time
            ON agri.forecast_observation(series_id, observed_at);
        CREATE INDEX ix_forecast_observation_release_available
            ON agri.forecast_observation(source_release_id, data_available_at);

        CREATE TABLE agri.forecast_feature_snapshot (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            snapshot_key varchar(255) NOT NULL UNIQUE,
            job_run_id uuid NOT NULL REFERENCES agri.job_run(id),
            release_set_id uuid NOT NULL REFERENCES agri.release_set(id),
            input_release_checksum varchar(64) NOT NULL,
            feature_recipe_version varchar(100) NOT NULL,
            feature_code_checksum varchar(64) NOT NULL,
            feature_checksum varchar(64) NOT NULL,
            training_window_start timestamptz NOT NULL,
            training_window_end timestamptz NOT NULL,
            row_count bigint NOT NULL,
            status varchar(24) NOT NULL DEFAULT 'draft',
            validation_metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
            validated_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_forecast_feature_snapshot_checksums CHECK (
                input_release_checksum ~ '^[0-9a-f]{64}$'
                AND feature_code_checksum ~ '^[0-9a-f]{64}$'
                AND feature_checksum ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_forecast_feature_snapshot_ordered_window
                CHECK (training_window_end >= training_window_start),
            CONSTRAINT ck_forecast_feature_snapshot_positive_rows CHECK (row_count > 0),
            CONSTRAINT ck_forecast_feature_snapshot_status
                CHECK (status IN ('draft', 'validated', 'rejected')),
            CONSTRAINT ck_forecast_feature_snapshot_validated_at
                CHECK (status <> 'validated' OR validated_at IS NOT NULL)
        );

        CREATE TABLE agri.forecast_model (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            model_key varchar(255) NOT NULL,
            model_version varchar(100) NOT NULL,
            model_kind varchar(24) NOT NULL,
            model_purpose varchar(32) NOT NULL DEFAULT 'metric_forecast',
            algorithm varchar(150) NOT NULL,
            model_code_checksum varchar(64) NOT NULL,
            artifact_id uuid REFERENCES agri.artifact(id),
            metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_forecast_model_version UNIQUE(model_key, model_version),
            CONSTRAINT ck_forecast_model_kind CHECK (model_kind IN ('sql_linear', 'ml')),
            CONSTRAINT ck_forecast_model_ml_artifact
                CHECK (model_kind <> 'ml' OR artifact_id IS NOT NULL),
            CONSTRAINT ck_forecast_model_purpose
                CHECK (model_purpose IN ('metric_forecast', 'strategy_selection')),
            CONSTRAINT ck_forecast_model_code_checksum_sha256
                CHECK (model_code_checksum ~ '^[0-9a-f]{64}$')
        );

        CREATE TABLE agri.forecast_training_run (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            training_key varchar(255) NOT NULL UNIQUE,
            model_id uuid NOT NULL REFERENCES agri.forecast_model(id),
            job_run_id uuid NOT NULL REFERENCES agri.job_run(id),
            job_output_id uuid NOT NULL REFERENCES agri.job_output(id),
            feature_snapshot_id uuid NOT NULL REFERENCES agri.forecast_feature_snapshot(id),
            execution_mode varchar(24) NOT NULL DEFAULT 'local',
            status varchar(24) NOT NULL DEFAULT 'gated',
            input_release_checksum varchar(64) NOT NULL,
            feature_checksum varchar(64) NOT NULL,
            training_code_checksum varchar(64) NOT NULL,
            model_checksum varchar(64),
            validation_checksum varchar(64),
            validation_metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
            started_at timestamptz,
            completed_at timestamptz,
            validated_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_forecast_training_execution_mode CHECK (execution_mode = 'local'),
            CONSTRAINT ck_forecast_training_status
                CHECK (status IN ('gated', 'running', 'validated', 'rejected')),
            CONSTRAINT ck_forecast_training_input_checksums CHECK (
                input_release_checksum ~ '^[0-9a-f]{64}$'
                AND feature_checksum ~ '^[0-9a-f]{64}$'
                AND training_code_checksum ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_forecast_training_validated_evidence CHECK (
                status <> 'validated'
                OR (
                    completed_at IS NOT NULL AND validated_at IS NOT NULL
                    AND model_checksum ~ '^[0-9a-f]{64}$'
                    AND validation_checksum ~ '^[0-9a-f]{64}$'
                )
            )
        );

        CREATE TABLE agri.forecast_quality_policy (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            policy_key varchar(255) NOT NULL UNIQUE,
            min_training_points integer NOT NULL,
            min_backtest_points integer NOT NULL,
            min_coverage_fraction double precision NOT NULL,
            max_mae double precision,
            max_rmse double precision,
            max_mape double precision,
            min_skill_score double precision,
            required_quantiles double precision[] NOT NULL DEFAULT ARRAY[0.1, 0.5, 0.9]::float8[],
            is_active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_forecast_quality_policy_positive_counts
                CHECK (min_training_points >= 3 AND min_backtest_points > 0),
            CONSTRAINT ck_forecast_quality_policy_coverage
                CHECK (min_coverage_fraction > 0 AND min_coverage_fraction <= 1),
            CONSTRAINT ck_forecast_quality_policy_nonnegative_limits CHECK (
                (max_mae IS NULL OR max_mae >= 0)
                AND (max_rmse IS NULL OR max_rmse >= 0)
                AND (max_mape IS NULL OR max_mape >= 0)
                AND (min_skill_score IS NULL OR min_skill_score <= 1)
            ),
            CONSTRAINT ck_forecast_quality_policy_quantiles CHECK (
                agri.forecast_quantiles_valid(required_quantiles)
                AND 0.5 = ANY(required_quantiles)
            )
        );

        CREATE TABLE agri.forecast_run (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            run_key varchar(255) NOT NULL UNIQUE,
            job_run_id uuid NOT NULL REFERENCES agri.job_run(id),
            feature_snapshot_id uuid NOT NULL REFERENCES agri.forecast_feature_snapshot(id),
            model_id uuid NOT NULL REFERENCES agri.forecast_model(id),
            training_run_id uuid REFERENCES agri.forecast_training_run(id),
            quality_policy_id uuid NOT NULL REFERENCES agri.forecast_quality_policy(id),
            forecast_method varchar(24) NOT NULL,
            issue_time timestamptz NOT NULL,
            valid_from timestamptz NOT NULL,
            valid_to timestamptz NOT NULL,
            horizon_steps integer NOT NULL,
            step_interval interval NOT NULL,
            input_release_checksum varchar(64) NOT NULL,
            feature_checksum varchar(64) NOT NULL,
            model_checksum varchar(64) NOT NULL,
            parameter_checksum varchar(64) NOT NULL,
            status varchar(24) NOT NULL DEFAULT 'staged',
            backtest_passed boolean NOT NULL DEFAULT false,
            quality_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
            validated_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_forecast_run_method CHECK (forecast_method IN ('sql_linear', 'ml')),
            CONSTRAINT ck_forecast_run_model_binding CHECK (
                (forecast_method = 'sql_linear' AND training_run_id IS NULL)
                OR (forecast_method = 'ml' AND training_run_id IS NOT NULL)
            ),
            CONSTRAINT ck_forecast_run_ordered_window CHECK (valid_to > valid_from),
            CONSTRAINT ck_forecast_run_positive_horizon
                CHECK (horizon_steps > 0 AND step_interval > interval '0'),
            CONSTRAINT ck_forecast_run_checksums CHECK (
                input_release_checksum ~ '^[0-9a-f]{64}$'
                AND feature_checksum ~ '^[0-9a-f]{64}$'
                AND model_checksum ~ '^[0-9a-f]{64}$'
                AND parameter_checksum ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_forecast_run_status
                CHECK (status IN ('staged', 'validated', 'rejected')),
            CONSTRAINT ck_forecast_run_validated_evidence
                CHECK (status <> 'validated' OR (backtest_passed AND validated_at IS NOT NULL))
        );

        CREATE INDEX ix_forecast_run_issue_time ON agri.forecast_run(issue_time DESC);

        CREATE TABLE agri.forecast_backtest_metric (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            forecast_run_id uuid NOT NULL REFERENCES agri.forecast_run(id),
            job_output_id uuid NOT NULL REFERENCES agri.job_output(id),
            series_id uuid NOT NULL REFERENCES agri.forecast_series(id),
            cutoff_time timestamptz NOT NULL,
            training_point_count integer NOT NULL,
            backtest_point_count integer NOT NULL,
            mae double precision NOT NULL,
            rmse double precision NOT NULL,
            naive_rmse double precision,
            skill_score double precision,
            bias double precision NOT NULL,
            mape double precision,
            coverage_fraction double precision NOT NULL,
            metrics_checksum varchar(64) NOT NULL,
            passed boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_forecast_backtest_run_series_cutoff
                UNIQUE(forecast_run_id, series_id, cutoff_time),
            CONSTRAINT ck_forecast_backtest_counts
                CHECK (training_point_count >= 3 AND backtest_point_count > 0),
            CONSTRAINT ck_forecast_backtest_nonnegative_metrics
                CHECK (
                    mae >= 0 AND rmse >= 0
                    AND (naive_rmse IS NULL OR naive_rmse >= 0)
                    AND (skill_score IS NULL OR skill_score <= 1)
                    AND (mape IS NULL OR mape >= 0)
                    AND mae::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND rmse::text NOT IN ('NaN', 'Infinity', '-Infinity')
                    AND bias::text NOT IN ('NaN', 'Infinity', '-Infinity')
                ),
            CONSTRAINT ck_forecast_backtest_coverage
                CHECK (coverage_fraction >= 0 AND coverage_fraction <= 1),
            CONSTRAINT ck_forecast_backtest_checksum_sha256
                CHECK (metrics_checksum ~ '^[0-9a-f]{64}$')
        );

        CREATE TABLE agri.forecast_receipt (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            forecast_run_id uuid NOT NULL REFERENCES agri.forecast_run(id),
            job_output_id uuid NOT NULL REFERENCES agri.job_output(id),
            series_id uuid NOT NULL REFERENCES agri.forecast_series(id),
            entity_state_id uuid REFERENCES agri.forecast_entity_state(id),
            issue_time timestamptz NOT NULL,
            valid_from timestamptz NOT NULL,
            valid_to timestamptz NOT NULL,
            expected_value_count integer NOT NULL,
            quantile_levels double precision[] NOT NULL,
            receipt_checksum varchar(64),
            status varchar(24) NOT NULL DEFAULT 'staging',
            quality_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
            finalized_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_forecast_receipt_run_series_window
                UNIQUE(forecast_run_id, series_id, issue_time, valid_from, valid_to),
            CONSTRAINT ck_forecast_receipt_ordered_window CHECK (valid_to > valid_from),
            CONSTRAINT ck_forecast_receipt_expected_values CHECK (expected_value_count > 0),
            CONSTRAINT ck_forecast_receipt_quantiles CHECK (
                agri.forecast_quantiles_valid(quantile_levels)
                AND 0.5 = ANY(quantile_levels)
            ),
            CONSTRAINT ck_forecast_receipt_status CHECK (status IN ('staging', 'finalized')),
            CONSTRAINT ck_forecast_receipt_finalized_evidence CHECK (
                status <> 'finalized'
                OR (receipt_checksum ~ '^[0-9a-f]{64}$' AND finalized_at IS NOT NULL)
            )
        );

        CREATE TABLE agri.forecast_value (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            forecast_receipt_id uuid NOT NULL REFERENCES agri.forecast_receipt(id),
            valid_time timestamptz NOT NULL,
            horizon_step integer NOT NULL,
            point_value double precision NOT NULL,
            p10_value double precision,
            p50_value double precision,
            p90_value double precision,
            quantile_values jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_forecast_value_receipt_time UNIQUE(forecast_receipt_id, valid_time),
            CONSTRAINT uq_forecast_value_receipt_horizon UNIQUE(forecast_receipt_id, horizon_step),
            CONSTRAINT ck_forecast_value_positive_horizon CHECK (horizon_step > 0),
            CONSTRAINT ck_forecast_value_ordered_bands CHECK (
                (p10_value IS NULL OR p50_value IS NULL OR p10_value <= p50_value)
                AND (p50_value IS NULL OR p90_value IS NULL OR p50_value <= p90_value)
            ),
            CONSTRAINT ck_forecast_value_finite_point CHECK (
                point_value::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND (p10_value IS NULL OR p10_value::text NOT IN ('NaN', 'Infinity', '-Infinity'))
                AND (p50_value IS NULL OR p50_value::text NOT IN ('NaN', 'Infinity', '-Infinity'))
                AND (p90_value IS NULL OR p90_value::text NOT IN ('NaN', 'Infinity', '-Infinity'))
            )
        );

        CREATE INDEX ix_forecast_value_receipt_time
            ON agri.forecast_value(forecast_receipt_id, valid_time);

        CREATE TABLE agri.forecast_publication (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            publication_key varchar(255) NOT NULL UNIQUE,
            job_run_id uuid NOT NULL REFERENCES agri.job_run(id),
            job_output_id uuid NOT NULL UNIQUE REFERENCES agri.job_output(id),
            release_set_id uuid NOT NULL REFERENCES agri.release_set(id),
            scope_key varchar(255) NOT NULL,
            state varchar(24) NOT NULL DEFAULT 'draft',
            manifest_checksum varchar(64),
            published_at timestamptz,
            retired_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_forecast_publication_state
                CHECK (state IN ('draft', 'published', 'retired')),
            CONSTRAINT ck_forecast_publication_published_evidence CHECK (
                state = 'draft'
                OR (manifest_checksum ~ '^[0-9a-f]{64}$' AND published_at IS NOT NULL)
            )
        );

        CREATE UNIQUE INDEX uq_forecast_publication_live_scope
            ON agri.forecast_publication(scope_key) WHERE state = 'published';

        CREATE TABLE agri.forecast_publication_item (
            publication_id uuid NOT NULL REFERENCES agri.forecast_publication(id),
            forecast_receipt_id uuid NOT NULL REFERENCES agri.forecast_receipt(id),
            added_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY(publication_id, forecast_receipt_id)
        );
        """
    )

    op.execute(
        r"""
        CREATE FUNCTION agri.forecast_timeseries_base(
            p_release_set_id uuid,
            p_as_of_time timestamptz
        )
        RETURNS TABLE(
            observation_id bigint,
            series_id uuid,
            series_key text,
            source_variant_key text,
            entity_type text,
            entity_key text,
            entity_state_id uuid,
            entity_state_checksum text,
            metric_name text,
            metric_unit text,
            observed_at timestamptz,
            metric_value double precision,
            representation_kind text,
            spatial_support_kind text,
            source_spatial_resolution_m integer,
            output_spatial_resolution_m integer,
            source_temporal_support interval,
            output_temporal_support interval,
            aggregation_method text,
            spatial_cell_id uuid,
            source_release_id uuid,
            input_release_checksum text,
            observation_checksum text
        )
        LANGUAGE sql
        STABLE
        AS $$
            SELECT
                NULL::bigint,
                series.id,
                series.series_key::text,
                series.source_variant_key::text,
                series.entity_type::text,
                series.entity_key::text,
                NULL::uuid,
                NULL::text,
                series.metric_name::text,
                series.metric_unit::text,
                source_contract.observed_at,
                source_contract.normalized_value,
                series.representation_kind::text,
                source_contract.spatial_support_kind,
                source_contract.native_resolution_m,
                source_contract.analysis_resolution_m,
                series.source_temporal_support,
                series.output_temporal_support,
                series.aggregation_method::text,
                source_contract.cell_id,
                source_contract.source_release_id,
                release_set.manifest_checksum::text,
                encode(digest(concat_ws('|',
                    source_release.payload_checksum,
                    source_contract.cell_id::text,
                    source_contract.signal_name,
                    source_contract.source_parameter,
                    source_contract.support_key,
                    source_contract.observed_at::text,
                    source_contract.normalized_value::text
                ), 'sha256'), 'hex')
            FROM agri.forecast_series AS series
            INNER JOIN agri.v_signal_timeseries_contract(p_as_of_time, p_release_set_id) AS source_contract
                ON source_contract.cell_id = series.spatial_cell_id
               AND source_contract.signal_name = series.signal_name
               AND source_contract.source_parameter = series.source_parameter
               AND source_contract.support_key = series.support_key
               AND source_contract.transform_version = series.source_transform_version
               AND source_contract.normalized_unit = series.metric_unit
            INNER JOIN agri.source_release AS source_release
                ON source_release.id = source_contract.source_release_id
               AND source_release.data_source_id = series.data_source_id
            INNER JOIN agri.release_set AS release_set
                ON release_set.id = p_release_set_id
            WHERE series.input_adapter = 'signal_observation'
              AND source_contract.normalized_value IS NOT NULL

            UNION ALL

            SELECT
                observation.id,
                series.id,
                series.series_key::text,
                series.source_variant_key::text,
                series.entity_type::text,
                series.entity_key::text,
                state.id,
                state.state_checksum::text,
                series.metric_name::text,
                series.metric_unit::text,
                observation.observed_at,
                observation.metric_value,
                series.representation_kind::text,
                series.spatial_support_kind::text,
                series.source_spatial_resolution_m,
                series.output_spatial_resolution_m,
                series.source_temporal_support,
                series.output_temporal_support,
                series.aggregation_method::text,
                series.spatial_cell_id,
                observation.source_release_id,
                release_set.manifest_checksum::text,
                observation.observation_checksum::text
            FROM agri.forecast_observation AS observation
            INNER JOIN agri.forecast_series AS series ON series.id = observation.series_id
            LEFT JOIN agri.forecast_entity_state AS state ON state.id = observation.entity_state_id
            INNER JOIN agri.source_release AS source_release
                ON source_release.id = observation.source_release_id
               AND source_release.data_source_id = series.data_source_id
               AND source_release.transform_version = series.source_transform_version
            INNER JOIN agri.release_set_item AS member
                ON member.source_release_id = source_release.id
            INNER JOIN agri.release_set AS release_set
                ON release_set.id = member.release_set_id
            WHERE release_set.id = p_release_set_id
              AND series.input_adapter = 'forecast_observation'
              AND release_set.state IN ('validated', 'published')
              AND release_set.validated_at <= p_as_of_time
              AND release_set.as_of_time <= p_as_of_time
              AND source_release.validation_state = 'valid'
              AND source_release.data_available_at <= p_as_of_time
              AND observation.data_available_at <= p_as_of_time
              AND (
                    state.id IS NULL
                    OR (
                        state.source_release_id = observation.source_release_id
                        AND state.series_id = series.id
                        AND state.data_available_at <= p_as_of_time
                        AND observation.observed_at >= state.valid_from
                        AND (state.valid_to IS NULL OR observation.observed_at < state.valid_to)
                    )
              )
        $$;

        CREATE FUNCTION agri.forecast_percentile(
            p_series_id uuid,
            p_release_set_id uuid,
            p_as_of_time timestamptz,
            p_window_start timestamptz,
            p_window_end timestamptz,
            p_percentile double precision
        )
        RETURNS double precision
        LANGUAGE plpgsql
        STABLE
        AS $$
        DECLARE
            result_value double precision;
        BEGIN
            IF p_percentile < 0 OR p_percentile > 1 THEN
                RAISE EXCEPTION 'percentile must be between zero and one';
            END IF;
            IF p_window_end <= p_window_start THEN
                RAISE EXCEPTION 'percentile window must be non-empty';
            END IF;

            SELECT percentile_cont(p_percentile) WITHIN GROUP (ORDER BY base.metric_value)
              INTO result_value
              FROM agri.forecast_timeseries_base(p_release_set_id, p_as_of_time) AS base
             WHERE base.series_id = p_series_id
               AND base.observed_at >= p_window_start
               AND base.observed_at < p_window_end;
            RETURN result_value;
        END
        $$;

        CREATE FUNCTION agri.forecast_normalized_series(
            p_series_id uuid,
            p_release_set_id uuid,
            p_as_of_time timestamptz,
            p_window_start timestamptz,
            p_window_end timestamptz,
            p_bucket_interval interval
        )
        RETURNS TABLE(
            bucket_start timestamptz,
            bucket_end timestamptz,
            sample_count bigint,
            metric_value double precision,
            minimum_value double precision,
            maximum_value double precision,
            is_missing boolean,
            serving_representation text
        )
        LANGUAGE plpgsql
        STABLE
        AS $$
        BEGIN
            IF p_window_end <= p_window_start THEN
                RAISE EXCEPTION 'normalization window must be non-empty';
            END IF;
            IF p_bucket_interval <= interval '0'
               OR extract(year FROM p_bucket_interval) <> 0
               OR extract(month FROM p_bucket_interval) <> 0 THEN
                RAISE EXCEPTION 'normalization bucket must be a positive fixed interval without months';
            END IF;

            RETURN QUERY
            SELECT
                bucket.bucket_start,
                bucket.bucket_start + p_bucket_interval,
                count(base.metric_value),
                avg(base.metric_value),
                min(base.metric_value),
                max(base.metric_value),
                count(base.metric_value) = 0,
                'normalized_bucket'::text
            FROM generate_series(
                p_window_start,
                p_window_end - p_bucket_interval,
                p_bucket_interval
            ) AS bucket(bucket_start)
            LEFT JOIN agri.forecast_timeseries_base(p_release_set_id, p_as_of_time) AS base
              ON base.series_id = p_series_id
             AND base.observed_at >= bucket.bucket_start
             AND base.observed_at < bucket.bucket_start + p_bucket_interval
            GROUP BY bucket.bucket_start
            ORDER BY bucket.bucket_start;
        END
        $$;

        CREATE FUNCTION agri.forecast_rolling_stats(
            p_series_id uuid,
            p_release_set_id uuid,
            p_as_of_time timestamptz,
            p_window_rows integer DEFAULT 30
        )
        RETURNS TABLE(
            observed_at timestamptz,
            metric_value double precision,
            sample_count bigint,
            rolling_mean double precision,
            rolling_stddev double precision,
            rolling_p10 double precision,
            rolling_p50 double precision,
            rolling_p90 double precision
        )
        LANGUAGE plpgsql
        STABLE
        AS $$
        BEGIN
            IF p_window_rows < 2 OR p_window_rows > 10000 THEN
                RAISE EXCEPTION 'rolling window rows must be between 2 and 10000';
            END IF;

            RETURN QUERY
            WITH ordered AS (
                SELECT
                    base.observed_at,
                    base.metric_value,
                    row_number() OVER (ORDER BY base.observed_at, base.observation_id) AS row_number
                FROM agri.forecast_timeseries_base(p_release_set_id, p_as_of_time) AS base
                WHERE base.series_id = p_series_id
            )
            SELECT
                current_row.observed_at,
                current_row.metric_value,
                window_stats.sample_count,
                window_stats.rolling_mean,
                window_stats.rolling_stddev,
                window_stats.rolling_p10,
                window_stats.rolling_p50,
                window_stats.rolling_p90
            FROM ordered AS current_row
            CROSS JOIN LATERAL (
                SELECT
                    count(*) AS sample_count,
                    avg(window_row.metric_value) AS rolling_mean,
                    stddev_samp(window_row.metric_value) AS rolling_stddev,
                    percentile_cont(0.1) WITHIN GROUP (ORDER BY window_row.metric_value) AS rolling_p10,
                    percentile_cont(0.5) WITHIN GROUP (ORDER BY window_row.metric_value) AS rolling_p50,
                    percentile_cont(0.9) WITHIN GROUP (ORDER BY window_row.metric_value) AS rolling_p90
                FROM ordered AS window_row
                WHERE window_row.row_number BETWEEN
                    greatest(1, current_row.row_number - p_window_rows + 1)
                    AND current_row.row_number
            ) AS window_stats
            ORDER BY current_row.observed_at;
        END
        $$;

        CREATE FUNCTION agri.forecast_linear_regression(
            p_series_id uuid,
            p_release_set_id uuid,
            p_as_of_time timestamptz,
            p_cutoff_time timestamptz,
            p_horizon_steps integer,
            p_step_interval interval,
            p_min_training_points integer DEFAULT 30
        )
        RETURNS TABLE(
            horizon_step integer,
            valid_time timestamptz,
            forecast_value double precision,
            training_point_count bigint,
            slope_per_second double precision,
            intercept double precision,
            eligible boolean,
            gate_reason text
        )
        LANGUAGE plpgsql
        STABLE
        AS $$
        BEGIN
            IF p_horizon_steps < 1 OR p_horizon_steps > 10000 THEN
                RAISE EXCEPTION 'forecast horizon must be between 1 and 10000 steps';
            END IF;
            IF p_step_interval <= interval '0' THEN
                RAISE EXCEPTION 'forecast step interval must be positive';
            END IF;
            IF p_min_training_points < 3 THEN
                RAISE EXCEPTION 'linear regression requires at least three training points';
            END IF;

            RETURN QUERY
            WITH training AS (
                SELECT
                    extract(epoch FROM base.observed_at)::double precision AS x,
                    base.metric_value AS y
                FROM agri.forecast_timeseries_base(p_release_set_id, p_as_of_time) AS base
                WHERE base.series_id = p_series_id
                  AND base.observed_at <= p_cutoff_time
            ),
            coefficients AS (
                SELECT
                    count(*) AS point_count,
                    count(DISTINCT x) AS distinct_time_count,
                    regr_slope(y, x) AS slope,
                    regr_intercept(y, x) AS intercept
                FROM training
            )
            SELECT
                step.step_number,
                p_cutoff_time + (p_step_interval * step.step_number),
                CASE
                    WHEN coefficients.point_count >= p_min_training_points
                     AND coefficients.distinct_time_count >= 2
                     AND coefficients.slope IS NOT NULL
                     AND coefficients.intercept IS NOT NULL
                    THEN coefficients.intercept
                       + coefficients.slope * extract(
                           epoch FROM p_cutoff_time + (p_step_interval * step.step_number)
                         )
                    ELSE NULL
                END,
                coefficients.point_count,
                coefficients.slope,
                coefficients.intercept,
                coefficients.point_count >= p_min_training_points
                    AND coefficients.distinct_time_count >= 2
                    AND coefficients.slope IS NOT NULL
                    AND coefficients.intercept IS NOT NULL,
                CASE
                    WHEN coefficients.point_count < p_min_training_points THEN 'insufficient_training_points'
                    WHEN coefficients.distinct_time_count < 2 THEN 'insufficient_distinct_timestamps'
                    WHEN coefficients.slope IS NULL OR coefficients.intercept IS NULL THEN 'singular_regression'
                    ELSE 'passed'
                END
            FROM coefficients
            CROSS JOIN generate_series(1, p_horizon_steps) AS step(step_number);
        END
        $$;

        CREATE FUNCTION agri.forecast_linear_backtest(
            p_series_id uuid,
            p_release_set_id uuid,
            p_as_of_time timestamptz,
            p_cutoff_time timestamptz,
            p_horizon_steps integer,
            p_step_interval interval,
            p_min_training_points integer DEFAULT 30
        )
        RETURNS TABLE(
            training_point_count bigint,
            backtest_point_count bigint,
            expected_point_count integer,
            mae double precision,
            rmse double precision,
            naive_rmse double precision,
            skill_score double precision,
            bias double precision,
            mape double precision,
            coverage_fraction double precision,
            eligible boolean,
            gate_reason text
        )
        LANGUAGE sql
        STABLE
        AS $$
            WITH predictions AS (
                SELECT *
                FROM agri.forecast_linear_regression(
                    p_series_id,
                    p_release_set_id,
                    p_as_of_time,
                    p_cutoff_time,
                    p_horizon_steps,
                    p_step_interval,
                    p_min_training_points
                )
            ),
            actuals AS (
                SELECT base.observed_at, base.metric_value
                FROM agri.forecast_timeseries_base(p_release_set_id, p_as_of_time) AS base
                WHERE base.series_id = p_series_id
                  AND base.observed_at > p_cutoff_time
                  AND base.observed_at <= p_cutoff_time + (p_step_interval * p_horizon_steps)
            ),
            last_training AS (
                SELECT (
                    SELECT base.metric_value
                    FROM agri.forecast_timeseries_base(p_release_set_id, p_as_of_time) AS base
                    WHERE base.series_id = p_series_id
                      AND base.observed_at <= p_cutoff_time
                    ORDER BY base.observed_at DESC, base.observation_id DESC
                    LIMIT 1
                ) AS naive_value
            ),
            compared AS (
                SELECT
                    prediction.training_point_count,
                    prediction.eligible,
                    prediction.gate_reason,
                    prediction.forecast_value,
                    actual.metric_value AS actual_value,
                    prediction.forecast_value - actual.metric_value AS error,
                    last_training.naive_value - actual.metric_value AS naive_error
                FROM predictions AS prediction
                CROSS JOIN last_training
                LEFT JOIN actuals AS actual ON actual.observed_at = prediction.valid_time
            ),
            aggregated AS (
                SELECT
                    max(compared.training_point_count) AS training_point_count,
                    count(compared.actual_value) AS backtest_point_count,
                    avg(abs(compared.error)) FILTER (WHERE compared.actual_value IS NOT NULL) AS mae,
                    sqrt(avg(compared.error * compared.error)
                        FILTER (WHERE compared.actual_value IS NOT NULL)) AS rmse,
                    sqrt(avg(compared.naive_error * compared.naive_error)
                        FILTER (WHERE compared.actual_value IS NOT NULL)) AS naive_rmse,
                    avg(compared.error) FILTER (WHERE compared.actual_value IS NOT NULL) AS bias,
                    avg(abs(compared.error / NULLIF(compared.actual_value, 0)))
                        FILTER (WHERE compared.actual_value IS NOT NULL AND compared.actual_value <> 0) AS mape,
                    bool_and(compared.eligible) AS regression_eligible,
                    min(compared.gate_reason) AS regression_gate_reason
                FROM compared
            )
            SELECT
                aggregated.training_point_count,
                aggregated.backtest_point_count,
                p_horizon_steps,
                aggregated.mae,
                aggregated.rmse,
                aggregated.naive_rmse,
                CASE
                    WHEN aggregated.naive_rmse IS NULL OR aggregated.naive_rmse = 0 THEN NULL
                    ELSE 1 - (aggregated.rmse / aggregated.naive_rmse)
                END,
                aggregated.bias,
                aggregated.mape,
                aggregated.backtest_point_count::double precision / p_horizon_steps,
                aggregated.regression_eligible AND aggregated.backtest_point_count > 0,
                CASE
                    WHEN NOT aggregated.regression_eligible THEN aggregated.regression_gate_reason
                    WHEN aggregated.backtest_point_count = 0 THEN 'no_aligned_holdout_points'
                    ELSE 'passed'
                END
            FROM aggregated
        $$;

        CREATE FUNCTION agri.forecast_linear_residual_bands(
            p_series_id uuid,
            p_release_set_id uuid,
            p_as_of_time timestamptz,
            p_cutoff_time timestamptz,
            p_horizon_steps integer,
            p_step_interval interval,
            p_min_training_points integer DEFAULT 30
        )
        RETURNS TABLE(
            training_point_count bigint,
            backtest_point_count bigint,
            residual_p10 double precision,
            residual_p50 double precision,
            residual_p90 double precision,
            eligible boolean,
            gate_reason text
        )
        LANGUAGE sql
        STABLE
        AS $$
            WITH predictions AS (
                SELECT *
                FROM agri.forecast_linear_regression(
                    p_series_id,
                    p_release_set_id,
                    p_as_of_time,
                    p_cutoff_time,
                    p_horizon_steps,
                    p_step_interval,
                    p_min_training_points
                )
            ),
            actuals AS (
                SELECT base.observed_at, base.metric_value
                FROM agri.forecast_timeseries_base(p_release_set_id, p_as_of_time) AS base
                WHERE base.series_id = p_series_id
                  AND base.observed_at > p_cutoff_time
                  AND base.observed_at <= p_cutoff_time + (p_step_interval * p_horizon_steps)
            ),
            residuals AS (
                SELECT
                    prediction.training_point_count,
                    prediction.eligible,
                    prediction.gate_reason,
                    actual.metric_value - prediction.forecast_value AS residual
                FROM predictions AS prediction
                LEFT JOIN actuals AS actual ON actual.observed_at = prediction.valid_time
            )
            SELECT
                max(residuals.training_point_count),
                count(residuals.residual),
                percentile_cont(0.1) WITHIN GROUP (ORDER BY residuals.residual),
                percentile_cont(0.5) WITHIN GROUP (ORDER BY residuals.residual),
                percentile_cont(0.9) WITHIN GROUP (ORDER BY residuals.residual),
                bool_and(residuals.eligible) AND count(residuals.residual) > 0,
                CASE
                    WHEN NOT bool_and(residuals.eligible) THEN min(residuals.gate_reason)
                    WHEN count(residuals.residual) = 0 THEN 'no_aligned_holdout_points'
                    ELSE 'passed'
                END
            FROM residuals
        $$;
        """
    )

    op.execute(
        r"""
        CREATE FUNCTION agri.validate_forecast_feature_snapshot(p_snapshot_id uuid)
        RETURNS agri.forecast_feature_snapshot
        LANGUAGE plpgsql
        AS $$
        DECLARE
            snapshot agri.forecast_feature_snapshot;
            pinned_release agri.release_set;
            feature_job agri.job_run;
        BEGIN
            SELECT * INTO snapshot
              FROM agri.forecast_feature_snapshot
             WHERE id = p_snapshot_id
             FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'unknown forecast feature snapshot %', p_snapshot_id;
            END IF;
            IF snapshot.status NOT IN ('draft', 'validated') THEN
                RAISE EXCEPTION 'forecast feature snapshot is not eligible for validation';
            END IF;

            SELECT * INTO pinned_release
              FROM agri.release_set
             WHERE id = snapshot.release_set_id;
            SELECT * INTO feature_job
              FROM agri.job_run
             WHERE id = snapshot.job_run_id;
            IF pinned_release.state NOT IN ('validated', 'published')
               OR pinned_release.validated_at IS NULL THEN
                RAISE EXCEPTION 'feature snapshot release set is not validated';
            END IF;
            IF pinned_release.manifest_checksum <> snapshot.input_release_checksum THEN
                RAISE EXCEPTION 'feature snapshot input release checksum mismatch';
            END IF;
            IF feature_job.status <> 'succeeded'
               OR feature_job.release_set_id <> snapshot.release_set_id THEN
                RAISE EXCEPTION 'feature snapshot job lineage is not complete';
            END IF;

            IF snapshot.status = 'draft' THEN
                UPDATE agri.forecast_feature_snapshot
                   SET status = 'validated', validated_at = now()
                 WHERE id = p_snapshot_id
                 RETURNING * INTO snapshot;
            END IF;
            RETURN snapshot;
        END
        $$;

        CREATE FUNCTION agri.validate_forecast_training_run(
            p_training_run_id uuid,
            p_model_checksum varchar,
            p_validation_checksum varchar,
            p_validation_metrics jsonb
        )
        RETURNS agri.forecast_training_run
        LANGUAGE plpgsql
        AS $$
        DECLARE
            training agri.forecast_training_run;
            snapshot agri.forecast_feature_snapshot;
            model agri.forecast_model;
            job agri.job_run;
            output agri.job_output;
            model_artifact agri.artifact;
        BEGIN
            IF p_model_checksum !~ '^[0-9a-f]{64}$'
               OR p_validation_checksum !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'training validation requires SHA-256 checksums';
            END IF;
            SELECT * INTO training
              FROM agri.forecast_training_run
             WHERE id = p_training_run_id
             FOR UPDATE;
            IF NOT FOUND OR training.status NOT IN ('gated', 'running', 'validated') THEN
                RAISE EXCEPTION 'training run is missing or not eligible for validation';
            END IF;
            SELECT * INTO snapshot FROM agri.forecast_feature_snapshot WHERE id = training.feature_snapshot_id;
            SELECT * INTO model FROM agri.forecast_model WHERE id = training.model_id;
            SELECT * INTO job FROM agri.job_run WHERE id = training.job_run_id;
            SELECT * INTO output FROM agri.job_output WHERE id = training.job_output_id;
            SELECT * INTO model_artifact FROM agri.artifact WHERE id = model.artifact_id;
            IF model.model_kind <> 'ml' THEN
                RAISE EXCEPTION 'only ML models have local training runs';
            END IF;
            IF snapshot.status <> 'validated'
               OR snapshot.input_release_checksum <> training.input_release_checksum
               OR snapshot.feature_checksum <> training.feature_checksum THEN
                RAISE EXCEPTION 'training run feature lineage is not validated';
            END IF;
            IF job.status <> 'succeeded' OR job.completed_at IS NULL THEN
                RAISE EXCEPTION 'local training job has not succeeded';
            END IF;
            IF job.release_set_id <> snapshot.release_set_id THEN
                RAISE EXCEPTION 'local training job release set does not match its features';
            END IF;
            IF output.job_run_id <> training.job_run_id
               OR output.state NOT IN ('validated', 'published')
               OR output.artifact_id <> model.artifact_id
               OR output.checksum_sha256 <> p_model_checksum
               OR output.metadata_json ->> 'validation_checksum' <> p_validation_checksum
               OR output.row_count IS NULL
               OR output.row_count <> 1 THEN
                RAISE EXCEPTION 'local training validation output lineage mismatch';
            END IF;
            IF model_artifact.checksum_sha256 <> p_model_checksum THEN
                RAISE EXCEPTION 'local training model artifact checksum mismatch';
            END IF;

            IF training.status <> 'validated' THEN
                UPDATE agri.forecast_training_run
                   SET status = 'validated',
                       model_checksum = p_model_checksum,
                       validation_checksum = p_validation_checksum,
                       validation_metrics = p_validation_metrics,
                       completed_at = coalesce(completed_at, job.completed_at),
                       validated_at = now()
                 WHERE id = p_training_run_id
                 RETURNING * INTO training;
            ELSIF training.model_checksum <> p_model_checksum
               OR training.validation_checksum <> p_validation_checksum
               OR training.validation_metrics IS DISTINCT FROM p_validation_metrics THEN
                RAISE EXCEPTION 'validated training receipt fields do not match verified evidence';
            END IF;
            RETURN training;
        END
        $$;

        CREATE FUNCTION agri.validate_forecast_run(p_forecast_run_id uuid)
        RETURNS agri.forecast_run
        LANGUAGE plpgsql
        AS $$
        DECLARE
            target_run agri.forecast_run;
            snapshot agri.forecast_feature_snapshot;
            model agri.forecast_model;
            training agri.forecast_training_run;
            policy agri.forecast_quality_policy;
            backtest_count integer;
            failed_count integer;
            forecast_job_status varchar;
            forecast_job_release_set_id uuid;
            snapshot_release_as_of timestamptz;
        BEGIN
            SELECT * INTO target_run
              FROM agri.forecast_run
             WHERE id = p_forecast_run_id
             FOR UPDATE;
            IF NOT FOUND OR target_run.status NOT IN ('staged', 'validated') THEN
                RAISE EXCEPTION 'forecast run is missing or not staged';
            END IF;
            SELECT * INTO snapshot FROM agri.forecast_feature_snapshot WHERE id = target_run.feature_snapshot_id;
            SELECT * INTO model FROM agri.forecast_model WHERE id = target_run.model_id;
            SELECT * INTO policy FROM agri.forecast_quality_policy WHERE id = target_run.quality_policy_id;
            SELECT as_of_time INTO snapshot_release_as_of
              FROM agri.release_set
             WHERE id = snapshot.release_set_id;
            SELECT status, release_set_id
              INTO forecast_job_status, forecast_job_release_set_id
              FROM agri.job_run
             WHERE id = target_run.job_run_id;
            IF snapshot.status <> 'validated'
               OR snapshot.input_release_checksum <> target_run.input_release_checksum
               OR snapshot.feature_checksum <> target_run.feature_checksum THEN
                RAISE EXCEPTION 'forecast run feature lineage is not validated';
            END IF;
            IF NOT policy.is_active THEN
                RAISE EXCEPTION 'forecast quality policy is inactive';
            END IF;
            IF model.model_purpose <> 'metric_forecast' THEN
                RAISE EXCEPTION 'strategy-selection models require a separate reviewed selection contract';
            END IF;
            IF forecast_job_status <> 'succeeded' THEN
                RAISE EXCEPTION 'forecast job has not succeeded';
            END IF;
            IF forecast_job_release_set_id <> snapshot.release_set_id THEN
                RAISE EXCEPTION 'forecast job release set does not match the feature snapshot';
            END IF;
            IF target_run.issue_time < snapshot_release_as_of
               OR target_run.valid_from < target_run.issue_time THEN
                RAISE EXCEPTION 'forecast issue or valid time precedes its pinned inputs';
            END IF;
            IF target_run.valid_from <> target_run.issue_time + target_run.step_interval
               OR target_run.valid_to <>
                    target_run.issue_time + (target_run.step_interval * (target_run.horizon_steps + 1)) THEN
                RAISE EXCEPTION 'forecast run valid window does not match its temporal grid';
            END IF;
            IF target_run.forecast_method = 'sql_linear' THEN
                IF model.model_kind <> 'sql_linear'
                   OR target_run.model_checksum <> model.model_code_checksum THEN
                    RAISE EXCEPTION 'SQL forecast model checksum mismatch';
                END IF;
            ELSE
                SELECT * INTO training
                  FROM agri.forecast_training_run
                 WHERE id = target_run.training_run_id;
                IF model.model_kind <> 'ml'
                   OR training.status <> 'validated'
                   OR training.execution_mode <> 'local'
                   OR training.model_id <> target_run.model_id
                   OR training.feature_snapshot_id <> target_run.feature_snapshot_id
                   OR training.model_checksum <> target_run.model_checksum THEN
                    RAISE EXCEPTION 'ML execution remains gated without a validated local training run';
                END IF;
            END IF;

            IF target_run.status = 'staged' THEN
                UPDATE agri.forecast_backtest_metric AS metric
                   SET passed = metric.training_point_count >= policy.min_training_points
                        AND metric.backtest_point_count >= policy.min_backtest_points
                        AND metric.coverage_fraction >= policy.min_coverage_fraction
                        AND (policy.max_mae IS NULL OR metric.mae <= policy.max_mae)
                        AND (policy.max_rmse IS NULL OR metric.rmse <= policy.max_rmse)
                        AND (policy.max_mape IS NULL
                            OR (metric.mape IS NOT NULL AND metric.mape <= policy.max_mape))
                        AND (policy.min_skill_score IS NULL
                            OR (metric.skill_score IS NOT NULL AND metric.skill_score >= policy.min_skill_score))
                 WHERE metric.forecast_run_id = target_run.id;
            END IF;

            SELECT
                count(*),
                count(*) FILTER (
                    WHERE NOT (
                            metric.training_point_count >= policy.min_training_points
                        AND metric.backtest_point_count >= policy.min_backtest_points
                        AND metric.coverage_fraction >= policy.min_coverage_fraction
                        AND (policy.max_mae IS NULL OR metric.mae <= policy.max_mae)
                        AND (policy.max_rmse IS NULL OR metric.rmse <= policy.max_rmse)
                        AND (policy.max_mape IS NULL
                            OR (metric.mape IS NOT NULL AND metric.mape <= policy.max_mape))
                        AND (policy.min_skill_score IS NULL
                            OR (metric.skill_score IS NOT NULL AND metric.skill_score >= policy.min_skill_score))
                    )
                       OR NOT metric.passed
                       OR output.state NOT IN ('validated', 'published')
                       OR output.job_run_id <> target_run.job_run_id
                       OR output.checksum_sha256 <> metric.metrics_checksum
                       OR output.row_count IS NULL
                       OR output.row_count <> 1
                )
              INTO backtest_count, failed_count
              FROM agri.forecast_backtest_metric AS metric
              INNER JOIN agri.job_output AS output ON output.id = metric.job_output_id
             WHERE metric.forecast_run_id = target_run.id;
            IF backtest_count = 0 OR failed_count > 0 THEN
                RAISE EXCEPTION 'forecast backtest quality gates did not pass';
            END IF;

            IF target_run.status = 'validated'
               AND (NOT target_run.backtest_passed OR target_run.validated_at IS NULL) THEN
                RAISE EXCEPTION 'validated forecast run requires recorded backtest approval';
            END IF;

            IF target_run.status = 'staged' THEN
                UPDATE agri.forecast_run
                   SET status = 'validated', backtest_passed = true, validated_at = now()
                 WHERE id = target_run.id
                 RETURNING * INTO target_run;
            END IF;
            RETURN target_run;
        END
        $$;

        CREATE FUNCTION agri.finalize_forecast_receipt(
            p_receipt_id uuid,
            p_expected_checksum varchar
        )
        RETURNS agri.forecast_receipt
        LANGUAGE plpgsql
        AS $$
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
            IF p_expected_checksum !~ '^[0-9a-f]{64}$' THEN
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
        $$;

        CREATE FUNCTION agri.publish_forecast_publication(
            p_publication_id uuid,
            p_expected_manifest_checksum varchar
        )
        RETURNS agri.forecast_publication
        LANGUAGE plpgsql
        AS $$
        DECLARE
            publication agri.forecast_publication;
            item_count integer;
            invalid_count integer;
            actual_checksum varchar;
            output_state varchar;
            output_run_id uuid;
            output_checksum varchar;
            output_row_count bigint;
            mismatched_release_count integer;
        BEGIN
            IF p_expected_manifest_checksum !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'publication manifest checksum must be SHA-256';
            END IF;
            SELECT * INTO publication
              FROM agri.forecast_publication
             WHERE id = p_publication_id
             FOR UPDATE;
            IF NOT FOUND OR publication.state NOT IN ('draft', 'published') THEN
                RAISE EXCEPTION 'forecast publication is missing or not eligible for publication';
            END IF;

            SELECT
                count(*),
                count(*) FILTER (WHERE receipt.status <> 'finalized'),
                count(*) FILTER (WHERE snapshot.release_set_id <> publication.release_set_id),
                encode(
                    digest(
                        coalesce(string_agg(receipt.receipt_checksum, E'\n' ORDER BY receipt.receipt_checksum), ''),
                        'sha256'
                    ),
                    'hex'
                )
              INTO item_count, invalid_count, mismatched_release_count, actual_checksum
              FROM agri.forecast_publication_item AS item
              INNER JOIN agri.forecast_receipt AS receipt ON receipt.id = item.forecast_receipt_id
              INNER JOIN agri.forecast_run AS run ON run.id = receipt.forecast_run_id
              INNER JOIN agri.forecast_feature_snapshot AS snapshot ON snapshot.id = run.feature_snapshot_id
             WHERE item.publication_id = publication.id;
            IF item_count = 0 OR invalid_count > 0 OR mismatched_release_count > 0 THEN
                RAISE EXCEPTION 'forecast publication requires finalized receipts';
            END IF;
            IF actual_checksum <> p_expected_manifest_checksum THEN
                RAISE EXCEPTION 'forecast publication manifest checksum mismatch';
            END IF;
            SELECT state, job_run_id, checksum_sha256, row_count
              INTO output_state, output_run_id, output_checksum, output_row_count
              FROM agri.job_output
             WHERE id = publication.job_output_id;
            IF output_state NOT IN ('validated', 'published')
               OR output_run_id <> publication.job_run_id
               OR output_checksum <> actual_checksum
               OR output_row_count IS NULL
               OR output_row_count <> item_count THEN
                RAISE EXCEPTION 'forecast publication job output lineage mismatch';
            END IF;
            IF publication.release_set_id <> (
                SELECT release_set_id FROM agri.job_run WHERE id = publication.job_run_id
            ) THEN
                RAISE EXCEPTION 'forecast publication release set does not match its job';
            END IF;

            IF publication.state = 'draft' THEN
                UPDATE agri.forecast_publication
                   SET state = 'published', manifest_checksum = actual_checksum, published_at = now()
                 WHERE id = publication.id
                 RETURNING * INTO publication;
            ELSIF publication.manifest_checksum <> actual_checksum THEN
                RAISE EXCEPTION 'published forecast manifest checksum changed';
            END IF;
            RETURN publication;
        END
        $$;
        """
    )

    op.execute(
        r"""
        CREATE FUNCTION agri.guard_forecast_immutable_rows()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% rows are immutable; append a superseding version', TG_TABLE_NAME;
        END
        $$;

        CREATE TRIGGER forecast_entity_state_immutable
            BEFORE UPDATE OR DELETE ON agri.forecast_entity_state
            FOR EACH ROW EXECUTE FUNCTION agri.guard_forecast_immutable_rows();
        CREATE TRIGGER forecast_observation_immutable
            BEFORE UPDATE OR DELETE ON agri.forecast_observation
            FOR EACH ROW EXECUTE FUNCTION agri.guard_forecast_immutable_rows();
        CREATE TRIGGER forecast_series_immutable
            BEFORE UPDATE OR DELETE ON agri.forecast_series
            FOR EACH ROW EXECUTE FUNCTION agri.guard_forecast_immutable_rows();
        CREATE TRIGGER forecast_model_immutable
            BEFORE UPDATE OR DELETE ON agri.forecast_model
            FOR EACH ROW EXECUTE FUNCTION agri.guard_forecast_immutable_rows();

        CREATE FUNCTION agri.require_initial_forecast_state()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE initial_state text;
        BEGIN
            initial_state := coalesce(to_jsonb(NEW) ->> 'status', to_jsonb(NEW) ->> 'state');
            IF (TG_TABLE_NAME = 'forecast_feature_snapshot' AND initial_state <> 'draft')
               OR (TG_TABLE_NAME = 'forecast_training_run' AND initial_state <> 'gated')
               OR (TG_TABLE_NAME = 'forecast_run' AND initial_state <> 'staged')
               OR (TG_TABLE_NAME = 'forecast_receipt' AND initial_state <> 'staging')
               OR (TG_TABLE_NAME = 'forecast_publication' AND initial_state <> 'draft') THEN
                RAISE EXCEPTION '% must be inserted in its non-terminal initial state', TG_TABLE_NAME;
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER forecast_feature_snapshot_initial_state
            BEFORE INSERT ON agri.forecast_feature_snapshot
            FOR EACH ROW EXECUTE FUNCTION agri.require_initial_forecast_state();
        CREATE TRIGGER forecast_training_run_initial_state
            BEFORE INSERT ON agri.forecast_training_run
            FOR EACH ROW EXECUTE FUNCTION agri.require_initial_forecast_state();
        CREATE TRIGGER forecast_run_initial_state
            BEFORE INSERT ON agri.forecast_run
            FOR EACH ROW EXECUTE FUNCTION agri.require_initial_forecast_state();
        CREATE TRIGGER forecast_receipt_initial_state
            BEFORE INSERT ON agri.forecast_receipt
            FOR EACH ROW EXECUTE FUNCTION agri.require_initial_forecast_state();
        CREATE TRIGGER forecast_publication_initial_state
            BEFORE INSERT ON agri.forecast_publication
            FOR EACH ROW EXECUTE FUNCTION agri.require_initial_forecast_state();

        CREATE FUNCTION agri.enforce_forecast_input_lineage()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            series_adapter varchar;
            series_source uuid;
            series_transform varchar;
            release_source uuid;
            release_transform varchar;
            release_available_at timestamptz;
            state_series_id uuid;
            state_release_id uuid;
            state_valid_from timestamptz;
            state_valid_to timestamptz;
            state_available_at timestamptz;
        BEGIN
            SELECT input_adapter, data_source_id, source_transform_version
              INTO series_adapter, series_source, series_transform
              FROM agri.forecast_series
             WHERE id = NEW.series_id;
            SELECT data_source_id, transform_version, data_available_at
              INTO release_source, release_transform, release_available_at
              FROM agri.source_release
             WHERE id = NEW.source_release_id;
            IF series_source <> release_source OR series_transform <> release_transform THEN
                RAISE EXCEPTION 'forecast input source variant does not match its series';
            END IF;
            IF NEW.data_available_at < release_available_at THEN
                RAISE EXCEPTION 'forecast input cannot be available before its source release';
            END IF;
            IF TG_TABLE_NAME = 'forecast_observation' AND series_adapter <> 'forecast_observation' THEN
                RAISE EXCEPTION 'generic forecast observation requires the forecast_observation adapter';
            END IF;
            IF TG_TABLE_NAME = 'forecast_observation'
               AND to_jsonb(NEW) ->> 'entity_state_id' IS NOT NULL THEN
                SELECT series_id, source_release_id, valid_from, valid_to, data_available_at
                  INTO state_series_id, state_release_id, state_valid_from, state_valid_to, state_available_at
                  FROM agri.forecast_entity_state
                 WHERE id = (to_jsonb(NEW) ->> 'entity_state_id')::uuid;
                IF state_series_id <> NEW.series_id
                   OR state_release_id <> NEW.source_release_id
                   OR state_available_at > NEW.data_available_at
                   OR (to_jsonb(NEW) ->> 'observed_at')::timestamptz < state_valid_from
                   OR (
                       state_valid_to IS NOT NULL
                       AND (to_jsonb(NEW) ->> 'observed_at')::timestamptz >= state_valid_to
                   ) THEN
                    RAISE EXCEPTION 'forecast observation entity state lineage mismatch';
                END IF;
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER forecast_entity_state_lineage_guard
            BEFORE INSERT ON agri.forecast_entity_state
            FOR EACH ROW EXECUTE FUNCTION agri.enforce_forecast_input_lineage();
        CREATE TRIGGER forecast_observation_lineage_guard
            BEFORE INSERT ON agri.forecast_observation
            FOR EACH ROW EXECUTE FUNCTION agri.enforce_forecast_input_lineage();

        CREATE FUNCTION agri.prevent_forecast_entity_state_overlap()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(hashtextextended(NEW.series_id::text, 0));
            IF EXISTS (
                SELECT 1
                FROM agri.forecast_entity_state AS existing
                WHERE existing.series_id = NEW.series_id
                  AND tstzrange(existing.valid_from, existing.valid_to, '[)')
                      && tstzrange(NEW.valid_from, NEW.valid_to, '[)')
            ) THEN
                RAISE EXCEPTION 'forecast entity state windows may not overlap within a series';
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER forecast_entity_state_overlap_guard
            BEFORE INSERT ON agri.forecast_entity_state
            FOR EACH ROW EXECUTE FUNCTION agri.prevent_forecast_entity_state_overlap();

        CREATE FUNCTION agri.guard_forecast_terminal_lineage()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.status IN ('validated', 'rejected') THEN
                RAISE EXCEPTION 'terminal % rows are immutable', TG_TABLE_NAME;
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER forecast_feature_snapshot_terminal_guard
            BEFORE UPDATE OR DELETE ON agri.forecast_feature_snapshot
            FOR EACH ROW EXECUTE FUNCTION agri.guard_forecast_terminal_lineage();
        CREATE TRIGGER forecast_training_run_terminal_guard
            BEFORE UPDATE OR DELETE ON agri.forecast_training_run
            FOR EACH ROW EXECUTE FUNCTION agri.guard_forecast_terminal_lineage();
        CREATE TRIGGER forecast_run_terminal_guard
            BEFORE UPDATE OR DELETE ON agri.forecast_run
            FOR EACH ROW EXECUTE FUNCTION agri.guard_forecast_terminal_lineage();

        CREATE FUNCTION agri.verify_forecast_validated_transition()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.status IS DISTINCT FROM NEW.status AND NEW.status = 'validated' THEN
                IF TG_TABLE_NAME = 'forecast_feature_snapshot' THEN
                    PERFORM agri.validate_forecast_feature_snapshot(NEW.id);
                ELSIF TG_TABLE_NAME = 'forecast_training_run' THEN
                    PERFORM agri.validate_forecast_training_run(
                        NEW.id,
                        NEW.model_checksum,
                        NEW.validation_checksum,
                        NEW.validation_metrics
                    );
                ELSIF TG_TABLE_NAME = 'forecast_run' THEN
                    PERFORM agri.validate_forecast_run(NEW.id);
                END IF;
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER forecast_feature_snapshot_validated_verify
            AFTER UPDATE OF status ON agri.forecast_feature_snapshot
            FOR EACH ROW EXECUTE FUNCTION agri.verify_forecast_validated_transition();
        CREATE TRIGGER forecast_training_run_validated_verify
            AFTER UPDATE OF status ON agri.forecast_training_run
            FOR EACH ROW EXECUTE FUNCTION agri.verify_forecast_validated_transition();
        CREATE TRIGGER forecast_run_validated_verify
            AFTER UPDATE OF status ON agri.forecast_run
            FOR EACH ROW EXECUTE FUNCTION agri.verify_forecast_validated_transition();

        CREATE FUNCTION agri.guard_forecast_backtest_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE run_status varchar;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'forecast backtest metrics are append-only';
            END IF;
            IF TG_OP = 'UPDATE' AND NEW.forecast_run_id <> OLD.forecast_run_id THEN
                RAISE EXCEPTION 'forecast backtest run identity is immutable';
            END IF;
            IF TG_OP = 'UPDATE' AND ROW(
                NEW.id, NEW.forecast_run_id, NEW.job_output_id, NEW.series_id, NEW.cutoff_time,
                NEW.training_point_count, NEW.backtest_point_count, NEW.mae, NEW.rmse,
                NEW.naive_rmse, NEW.skill_score, NEW.bias, NEW.mape, NEW.coverage_fraction,
                NEW.metrics_checksum, NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.id, OLD.forecast_run_id, OLD.job_output_id, OLD.series_id, OLD.cutoff_time,
                OLD.training_point_count, OLD.backtest_point_count, OLD.mae, OLD.rmse,
                OLD.naive_rmse, OLD.skill_score, OLD.bias, OLD.mape, OLD.coverage_fraction,
                OLD.metrics_checksum, OLD.created_at
            ) THEN
                RAISE EXCEPTION 'forecast backtest evidence is immutable except for its evaluated pass flag';
            END IF;
            SELECT status INTO run_status
              FROM agri.forecast_run
             WHERE id = CASE WHEN TG_OP = 'UPDATE' THEN OLD.forecast_run_id ELSE NEW.forecast_run_id END
             FOR SHARE;
            IF run_status <> 'staged' THEN
                RAISE EXCEPTION 'forecast backtest metrics freeze with their validated run';
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER forecast_backtest_change_guard
            BEFORE INSERT OR UPDATE OR DELETE ON agri.forecast_backtest_metric
            FOR EACH ROW EXECUTE FUNCTION agri.guard_forecast_backtest_change();

        CREATE FUNCTION agri.guard_forecast_value_write()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE receipt_status varchar;
        BEGIN
            SELECT status INTO receipt_status
              FROM agri.forecast_receipt
             WHERE id = coalesce(NEW.forecast_receipt_id, OLD.forecast_receipt_id)
             FOR SHARE;
            IF TG_OP = 'INSERT' AND receipt_status = 'staging' THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'forecast values are writable only while their receipt is staging';
        END
        $$;

        CREATE TRIGGER forecast_value_write_guard
            BEFORE INSERT OR UPDATE OR DELETE ON agri.forecast_value
            FOR EACH ROW EXECUTE FUNCTION agri.guard_forecast_value_write();

        CREATE FUNCTION agri.guard_forecast_receipt_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.status = 'finalized' THEN
                RAISE EXCEPTION 'finalized forecast receipts are immutable';
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER forecast_receipt_change_guard
            BEFORE UPDATE OR DELETE ON agri.forecast_receipt
            FOR EACH ROW EXECUTE FUNCTION agri.guard_forecast_receipt_change();

        CREATE FUNCTION agri.verify_forecast_receipt_finalization()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.status IS DISTINCT FROM NEW.status AND NEW.status = 'finalized' THEN
                PERFORM agri.finalize_forecast_receipt(NEW.id, NEW.receipt_checksum);
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER forecast_receipt_finalized_verify
            AFTER UPDATE OF status ON agri.forecast_receipt
            FOR EACH ROW EXECUTE FUNCTION agri.verify_forecast_receipt_finalization();

        CREATE FUNCTION agri.guard_forecast_publication_item_write()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE publication_state varchar;
        BEGIN
            SELECT state INTO publication_state
              FROM agri.forecast_publication
             WHERE id = CASE
                 WHEN TG_OP = 'DELETE' THEN OLD.publication_id
                 ELSE NEW.publication_id
             END
             FOR SHARE;
            IF TG_OP = 'INSERT' AND publication_state = 'draft' THEN
                RETURN NEW;
            END IF;
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION 'forecast publication membership is replaced by draft delete and insert';
            END IF;
            IF TG_OP = 'DELETE' AND publication_state = 'draft' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'published forecast publication membership is immutable';
        END
        $$;

        CREATE TRIGGER forecast_publication_item_write_guard
            BEFORE INSERT OR UPDATE OR DELETE ON agri.forecast_publication_item
            FOR EACH ROW EXECUTE FUNCTION agri.guard_forecast_publication_item_write();

        CREATE FUNCTION agri.guard_forecast_publication_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.state = 'retired' THEN
                RAISE EXCEPTION 'published forecast publications are immutable';
            END IF;
            IF OLD.state = 'published' THEN
                IF NEW.state = 'retired'
                   AND NEW.retired_at IS NOT NULL
                   AND NEW.publication_key = OLD.publication_key
                   AND NEW.job_run_id = OLD.job_run_id
                   AND NEW.job_output_id = OLD.job_output_id
                   AND NEW.release_set_id = OLD.release_set_id
                   AND NEW.scope_key = OLD.scope_key
                   AND NEW.manifest_checksum = OLD.manifest_checksum
                   AND NEW.published_at = OLD.published_at
                   AND NEW.created_at = OLD.created_at THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION 'published forecast publications may only be retired';
            END IF;
            IF OLD.state = 'draft' AND NEW.state IN ('draft', 'published') THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'draft forecast publications may only remain draft or be published';
        END
        $$;

        CREATE TRIGGER forecast_publication_change_guard
            BEFORE UPDATE OR DELETE ON agri.forecast_publication
            FOR EACH ROW EXECUTE FUNCTION agri.guard_forecast_publication_change();

        CREATE FUNCTION agri.verify_forecast_publication_transition()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.state IS DISTINCT FROM NEW.state AND NEW.state = 'published' THEN
                PERFORM agri.publish_forecast_publication(NEW.id, NEW.manifest_checksum);
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER forecast_publication_published_verify
            AFTER UPDATE OF state ON agri.forecast_publication
            FOR EACH ROW EXECUTE FUNCTION agri.verify_forecast_publication_transition();
        """
    )

    op.execute(
        r"""
        CREATE VIEW agri.v_forecast_series_serving AS
        SELECT
            pointer.id AS publication_pointer_id,
            publication.id AS publication_id,
            publication.publication_key,
            publication.scope_key,
            publication.manifest_checksum AS publication_manifest_checksum,
            publication.published_at,
            receipt.id AS forecast_receipt_id,
            receipt.receipt_checksum,
            receipt.issue_time,
            value.valid_time,
            value.horizon_step,
            value.point_value,
            value.p10_value,
            value.p50_value,
            value.p90_value,
            value.quantile_values,
            series.id AS series_id,
            series.series_key,
            series.source_variant_key,
            series.entity_type,
            series.entity_key,
            series.metric_name,
            series.metric_unit,
            series.spatial_cell_id,
            series.representation_kind,
            series.spatial_support_kind,
            series.source_spatial_resolution_m,
            series.output_spatial_resolution_m,
            series.source_temporal_support,
            series.output_temporal_support,
            series.aggregation_method,
            run.id AS forecast_run_id,
            snapshot.release_set_id,
            run.forecast_method,
            run.input_release_checksum,
            run.feature_checksum,
            run.model_checksum,
            run.parameter_checksum,
            run.quality_summary,
            training.id AS training_run_id,
            training.training_code_checksum,
            training.validation_checksum,
            receipt.quality_summary AS receipt_quality_summary,
            'forecast_point'::text AS serving_representation
        FROM agri.publication_pointer AS pointer
        INNER JOIN agri.forecast_publication AS publication
            ON publication.job_output_id = pointer.job_output_id
           AND publication.scope_key = pointer.scope_key
           AND publication.release_set_id = pointer.release_set_id
           AND pointer.product = 'forecast_series'
        INNER JOIN agri.forecast_publication_item AS item
            ON item.publication_id = publication.id
        INNER JOIN agri.forecast_receipt AS receipt
            ON receipt.id = item.forecast_receipt_id
        INNER JOIN agri.forecast_value AS value
            ON value.forecast_receipt_id = receipt.id
        INNER JOIN agri.forecast_series AS series
            ON series.id = receipt.series_id
        INNER JOIN agri.forecast_run AS run
            ON run.id = receipt.forecast_run_id
        INNER JOIN agri.forecast_feature_snapshot AS snapshot
            ON snapshot.id = run.feature_snapshot_id
        LEFT JOIN agri.forecast_training_run AS training
            ON training.id = run.training_run_id
        WHERE publication.state = 'published'
          AND receipt.status = 'finalized'
          AND run.status = 'validated'
          AND pointer.release_set_id = snapshot.release_set_id;

        CREATE MATERIALIZED VIEW agri.mv_forecast_ml_daily_serving AS
        SELECT
            serving.publication_id,
            serving.forecast_receipt_id,
            serving.receipt_checksum,
            serving.series_id,
            serving.series_key,
            serving.source_variant_key,
            serving.entity_type,
            serving.entity_key,
            serving.metric_name,
            serving.metric_unit,
            serving.spatial_cell_id,
            date_trunc('day', serving.valid_time) AS valid_day,
            max(serving.issue_time) AS issue_time,
            avg(serving.point_value) AS mean_point_value,
            min(serving.p10_value) AS lower_p10_value,
            avg(serving.p50_value) AS median_p50_value,
            max(serving.p90_value) AS upper_p90_value,
            count(*) AS contributing_forecast_points,
            serving.representation_kind AS source_representation_kind,
            serving.spatial_support_kind AS source_spatial_support_kind,
            serving.source_spatial_resolution_m,
            serving.output_spatial_resolution_m,
            serving.source_temporal_support,
            interval '1 day' AS output_temporal_support,
            'daily_mean_min_max_band'::text AS aggregation_method,
            serving.input_release_checksum,
            serving.feature_checksum,
            serving.model_checksum,
            serving.training_run_id,
            serving.training_code_checksum,
            serving.validation_checksum,
            'preaggregated_forecast'::text AS serving_representation
        FROM agri.v_forecast_series_serving AS serving
        INNER JOIN agri.forecast_series AS configured_series
            ON configured_series.id = serving.series_id
        WHERE serving.forecast_method = 'ml'
          AND configured_series.allow_ml_daily_aggregate
        GROUP BY
            serving.publication_id,
            serving.forecast_receipt_id,
            serving.receipt_checksum,
            serving.series_id,
            serving.series_key,
            serving.source_variant_key,
            serving.entity_type,
            serving.entity_key,
            serving.metric_name,
            serving.metric_unit,
            serving.spatial_cell_id,
            date_trunc('day', serving.valid_time),
            serving.representation_kind,
            serving.spatial_support_kind,
            serving.source_spatial_resolution_m,
            serving.output_spatial_resolution_m,
            serving.source_temporal_support,
            serving.input_release_checksum,
            serving.feature_checksum,
            serving.model_checksum,
            serving.training_run_id,
            serving.training_code_checksum,
            serving.validation_checksum
        WITH NO DATA;

        CREATE UNIQUE INDEX uq_mv_forecast_ml_daily_serving_identity
            ON agri.mv_forecast_ml_daily_serving(
                publication_id, forecast_receipt_id, series_id, valid_day
            );

        CREATE FUNCTION agri.refresh_forecast_ml_daily_serving()
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, agri
        AS $$
        BEGIN
            REFRESH MATERIALIZED VIEW agri.mv_forecast_ml_daily_serving;
        END
        $$;

        REVOKE ALL PRIVILEGES ON TABLE
            agri.forecast_series,
            agri.forecast_entity_state,
            agri.forecast_observation,
            agri.forecast_feature_snapshot,
            agri.forecast_model,
            agri.forecast_training_run,
            agri.forecast_quality_policy,
            agri.forecast_run,
            agri.forecast_backtest_metric,
            agri.forecast_receipt,
            agri.forecast_value,
            agri.forecast_publication,
            agri.forecast_publication_item,
            agri.v_forecast_series_serving,
            agri.mv_forecast_ml_daily_serving
        FROM PUBLIC;
        REVOKE ALL PRIVILEGES ON SEQUENCE
            agri.forecast_observation_id_seq,
            agri.forecast_value_id_seq
        FROM PUBLIC;

        REVOKE EXECUTE ON FUNCTION agri.forecast_quantiles_valid(double precision[]) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.v_signal_timeseries_contract(timestamptz, uuid) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.forecast_timeseries_base(uuid, timestamptz) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.forecast_percentile(
            uuid, uuid, timestamptz, timestamptz, timestamptz, double precision
        ) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.forecast_normalized_series(
            uuid, uuid, timestamptz, timestamptz, timestamptz, interval
        ) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.forecast_rolling_stats(uuid, uuid, timestamptz, integer)
        FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.forecast_linear_regression(
            uuid, uuid, timestamptz, timestamptz, integer, interval, integer
        ) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.forecast_linear_backtest(
            uuid, uuid, timestamptz, timestamptz, integer, interval, integer
        ) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.forecast_linear_residual_bands(
            uuid, uuid, timestamptz, timestamptz, integer, interval, integer
        ) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.validate_forecast_feature_snapshot(uuid) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.validate_forecast_training_run(uuid, varchar, varchar, jsonb)
        FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.validate_forecast_run(uuid) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.finalize_forecast_receipt(uuid, varchar) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.publish_forecast_publication(uuid, varchar) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.guard_forecast_immutable_rows() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.require_initial_forecast_state() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.enforce_forecast_input_lineage() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.prevent_forecast_entity_state_overlap() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.guard_forecast_terminal_lineage() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.verify_forecast_validated_transition() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.guard_forecast_backtest_change() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.guard_forecast_value_write() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.guard_forecast_receipt_change() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.verify_forecast_receipt_finalization() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.guard_forecast_publication_item_write() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.guard_forecast_publication_change() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.verify_forecast_publication_transition() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.refresh_forecast_ml_daily_serving() FROM PUBLIC;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("Forecast lineage and receipts are append-only; restore a verified backup to roll back.")
