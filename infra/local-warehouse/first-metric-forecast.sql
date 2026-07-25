\set ON_ERROR_STOP on

BEGIN;

DO $first_metric_forecast$
#variable_conflict use_variable
DECLARE
    evaluation_only constant boolean := true;
    publication_authorized constant boolean := false;
    target_release_set_id constant uuid := '10f6933b-c048-4dbc-9c33-68e00d2e6d87';
    target_manifest constant text := '7586ea8eae24f9e1e6adfc26f42bb89e2abbe3066be119e63536dbfa1fb8e6fc';
    target_data_source_id constant uuid := 'fb4902f5-0ea4-49af-8924-7ff039bd3f5f';
    target_source_release_id constant uuid := '31486536-de3b-4e54-989e-50ba5248fc83';
    target_cell_id constant uuid := '1d5f67c7-9c4f-4755-bc68-3895b0d55ce9';
    issue_time timestamptz;
    run_stamp text;
    holdout_cutoff constant timestamptz := '2026-04-23 00:00:00+00';
    horizon_steps constant integer := 7;
    min_training_points constant integer := 1095;
    min_backtest_points constant integer := 7;
    min_coverage constant double precision := 1.0;
    max_mae constant double precision := 0.75;
    max_rmse constant double precision := 1.0;
    max_mape constant double precision := 0.35;
    min_skill constant double precision := 0.10;
    min_hindcast_pass_fraction constant double precision := 0.50;
    min_interval_coverage constant double precision := 0.70;
    target_release_set agri.release_set;
    target_source_release agri.source_release;
    series_id uuid;
    job_definition_id uuid;
    job_run_id uuid;
    snapshot_id uuid;
    model_id uuid;
    policy_id uuid;
    forecast_run_id uuid;
    backtest_output_id uuid;
    forecast_output_id uuid;
    publication_output_id uuid;
    receipt_id uuid;
    publication_id uuid;
    feature_checksum text;
    feature_code_checksum text;
    model_checksum text;
    parameter_checksum text;
    metrics_checksum text;
    receipt_checksum text;
    publication_checksum text;
    history_count bigint;
    history_start timestamptz;
    history_end timestamptz;
    maximum_gap interval;
    backtest record;
    bands record;
    gates_passed boolean;
    forecast_value_count integer;
    hindcast_id uuid;
    hindcast_cutoff timestamptz;
    hindcast_checksum text;
    hindcast_manifest_checksum text;
    hindcast_training_count integer;
    hindcast_run_count integer;
    hindcast_pass_count integer;
    hindcast_value_count integer;
    hindcast_mae double precision;
    hindcast_rmse double precision;
    hindcast_naive_rmse double precision;
    hindcast_skill double precision;
    hindcast_mape double precision;
    hindcast_coverage double precision;
    hindcast_interval_coverage double precision;
    hindcast_gates_passed boolean;
    terminal_gates_passed boolean;
    hindcast_output_id uuid;
BEGIN
    issue_time := statement_timestamp();
    run_stamp := to_char(issue_time AT TIME ZONE 'UTC', 'YYYYMMDD"T"HH24MISS"Z"');
    IF (SELECT version_num FROM alembic_version)
       NOT IN ('20260722_0008', '20260723_0009', '20260723_0010') THEN
        RAISE EXCEPTION
            'first metric forecast requires Alembic 20260722_0008, 20260723_0009, or 20260723_0010';
    END IF;

    SELECT * INTO STRICT target_release_set
    FROM agri.release_set
    WHERE id = target_release_set_id;
    IF target_release_set.state NOT IN ('validated', 'published')
       OR target_release_set.manifest_checksum <> target_manifest
       OR target_release_set.validated_at IS NULL
       OR issue_time < target_release_set.validated_at THEN
        RAISE EXCEPTION 'target release set is not the reviewed validated NASA POWER fixture';
    END IF;

    SELECT * INTO STRICT target_source_release
    FROM agri.source_release
    WHERE id = target_source_release_id;
    IF target_source_release.data_source_id <> target_data_source_id
       OR target_source_release.source_version <>
            'nasa-power-daily-v1:20220430-20260430:na-sample:1deg:p040.00:m105.00'
       OR target_source_release.transform_version <> 'nasa-power-point-sample-normalization-v2'
       OR target_source_release.validation_state <> 'valid'
       OR NOT EXISTS (
            SELECT 1 FROM agri.release_set_item
            WHERE release_set_id = target_release_set_id
              AND source_release_id = target_source_release_id
       ) THEN
        RAISE EXCEPTION 'target source release identity or pinned membership changed';
    END IF;

    WITH history AS (
        SELECT observed_at
        FROM agri.v_signal_timeseries_contract(issue_time, target_release_set_id)
        WHERE cell_id = target_cell_id
          AND signal_name = 'wind_speed'
          AND source_parameter = 'WS2M'
          AND support_key = 'surface'
          AND normalized_unit = 'm/s'
          AND transform_version = 'nasa-power-point-sample-normalization-v2'
          AND spatial_support_kind = 'point_sample'
          AND native_resolution_m = 55660
          AND analysis_resolution_m = 55660
          AND normalized_value IS NOT NULL
    ), gaps AS (
        SELECT observed_at - lag(observed_at) OVER (ORDER BY observed_at) AS gap
        FROM history
    )
    SELECT
        (SELECT count(*) FROM history),
        (SELECT min(observed_at) FROM history),
        (SELECT max(observed_at) FROM history),
        (SELECT max(gap) FROM gaps)
      INTO history_count, history_start, history_end, maximum_gap;
    IF history_count <> 1462
       OR history_start <> '2022-04-30 00:00:00+00'::timestamptz
       OR history_end <> '2026-04-30 00:00:00+00'::timestamptz
       OR maximum_gap <> interval '1 day' THEN
        RAISE EXCEPTION 'target history no longer matches the reviewed complete daily fixture';
    END IF;

    SELECT id INTO STRICT series_id
    FROM agri.forecast_series
    WHERE source_variant_key = target_source_release.source_version
      AND input_adapter = 'signal_observation'
      AND data_source_id = target_data_source_id
      AND signal_name = 'wind_speed'
      AND source_parameter = 'WS2M'
      AND support_key = 'surface'
      AND source_transform_version = target_source_release.transform_version
      AND entity_type = 'nasa_power_point'
      AND entity_key = 'na-sample:1deg:p040.00:m105.00'
      AND metric_name = 'wind_speed'
      AND metric_unit = 'm/s'
      AND spatial_cell_id = target_cell_id
      AND representation_kind = 'raw_native'
      AND spatial_support_kind = 'point_sample'
      AND source_spatial_resolution_m = 55660
      AND output_spatial_resolution_m = 55660
      AND source_temporal_support = interval '1 day'
      AND output_temporal_support = interval '1 day'
      AND metadata_json ->> 'source_release_id' = target_source_release_id::text
      AND metadata_json ->> 'release_set_id' = target_release_set_id::text
      AND metadata_json ->> 'release_manifest_checksum' = target_manifest;

    SELECT encode(digest(coalesce(string_agg(
        concat_ws('|',
            to_char(base.observed_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
            base.metric_value::text,
            base.observation_checksum
        ), E'\n' ORDER BY base.observed_at, base.source_release_id
    ), ''), 'sha256'), 'hex')
      INTO feature_checksum
      FROM agri.forecast_timeseries_base(target_release_set_id, issue_time) AS base
     WHERE base.series_id = series_id;
    feature_code_checksum := encode(digest(
        'nasa-power-ws2m-corrected-v2|agri.forecast_timeseries_base', 'sha256'
    ), 'hex');
    SELECT encode(digest(pg_get_functiondef(to_regprocedure(
        'agri.forecast_linear_regression(uuid,uuid,timestamptz,timestamptz,integer,interval,integer)'
    )), 'sha256'), 'hex') INTO model_checksum;
    parameter_checksum := encode(digest(
        concat_ws('|', holdout_cutoff::text, horizon_steps::text, '1 day',
            min_training_points::text, min_backtest_points::text,
            min_coverage::text, max_mae::text, max_rmse::text,
            max_mape::text, min_skill::text,
            min_hindcast_pass_fraction::text, min_interval_coverage::text),
        'sha256'
    ), 'hex');

    INSERT INTO agri.job_definition(
        name, version, handler, enabled, max_attempts, lease_seconds, time_budget_seconds,
        parameters
    ) VALUES (
        'first-metric-forecast', 'v2', 'sql:infra/local-warehouse/first-metric-forecast.sql',
        false, 1, 300, 1800,
        jsonb_build_object(
            'candidate_scope_key', 'nasa-power-ws2m-denver-point-v2',
            'source_series_id', series_id,
            'evaluation_only', evaluation_only,
            'publication_authorized', publication_authorized
        )
    ) RETURNING id INTO job_definition_id;
    INSERT INTO agri.job_run(
        job_definition_id, release_set_id, logical_run_key, scheduled_for,
        recipe_version, model_version, status, requested_by,
        total_work_items, succeeded_work_items, completed_at
    ) VALUES (
        job_definition_id, target_release_set_id,
        'first-metric-forecast:nasa-power-ws2m-denver-point-v2:' || run_stamp,
        issue_time, 'nasa-power-ws2m-corrected-v2', 'sql-linear-v2',
        'succeeded', 'plantgeo-local-evaluation', 1, 1, clock_timestamp()
    ) RETURNING id INTO job_run_id;

    INSERT INTO agri.forecast_feature_snapshot(
        snapshot_key, job_run_id, release_set_id, input_release_checksum,
        feature_recipe_version, feature_code_checksum, feature_checksum,
        training_window_start, training_window_end, row_count,
        validation_metrics
    ) VALUES (
        'nasa-power-ws2m-denver-point-v2:features:' || run_stamp,
        job_run_id, target_release_set_id, target_manifest,
        'nasa-power-ws2m-corrected-v2', feature_code_checksum, feature_checksum,
        history_start, history_end, history_count,
        jsonb_build_object(
            'complete_daily_points', history_count,
            'maximum_gap_seconds', extract(epoch FROM maximum_gap),
            'source_latest_observation', history_end,
            'source_age_at_issue_days', extract(epoch FROM issue_time - history_end) / 86400.0
        )
    ) RETURNING id INTO snapshot_id;
    PERFORM agri.validate_forecast_feature_snapshot(snapshot_id);

    INSERT INTO agri.forecast_model(
        model_key, model_version, model_kind, model_purpose,
        algorithm, model_code_checksum, metadata_json
    ) VALUES (
        'plantgeo-sql-elapsed-time-linear', 'v2', 'sql_linear', 'metric_forecast',
        'postgresql_regr_slope_elapsed_utc', model_checksum,
        jsonb_build_object('operational_weather', false, 'strategy_selection', false)
    ) RETURNING id INTO model_id;
    INSERT INTO agri.forecast_quality_policy(
        policy_key, min_training_points, min_backtest_points,
        min_coverage_fraction, max_mae, max_rmse, max_mape,
        min_skill_score, required_quantiles
    ) VALUES (
        'nasa-power-ws2m-denver-7d-baseline-v2',
        min_training_points, min_backtest_points, min_coverage,
        max_mae, max_rmse, max_mape, min_skill,
        ARRAY[0.1, 0.5, 0.9]::double precision[]
    ) RETURNING id INTO policy_id;

    SELECT * INTO STRICT backtest
    FROM agri.forecast_linear_backtest(
        series_id, target_release_set_id, issue_time, holdout_cutoff,
        horizon_steps, interval '1 day', min_training_points
    );
    SELECT * INTO STRICT bands
    FROM agri.forecast_linear_residual_bands(
        series_id, target_release_set_id, issue_time, holdout_cutoff,
        horizon_steps, interval '1 day', min_training_points
    );
    metrics_checksum := encode(digest(concat_ws('|',
        holdout_cutoff::text, backtest.training_point_count::text,
        backtest.backtest_point_count::text, backtest.expected_point_count::text,
        backtest.mae::text, backtest.rmse::text, backtest.naive_rmse::text,
        backtest.skill_score::text, backtest.bias::text, backtest.mape::text,
        backtest.coverage_fraction::text,
        bands.residual_p10::text, bands.residual_p50::text, bands.residual_p90::text
    ), 'sha256'), 'hex');

    INSERT INTO agri.forecast_run(
        run_key, job_run_id, feature_snapshot_id, model_id, quality_policy_id,
        forecast_method, issue_time, valid_from, valid_to, horizon_steps,
        step_interval, input_release_checksum, feature_checksum,
        model_checksum, parameter_checksum, quality_summary
    ) VALUES (
        'nasa-power-ws2m-denver-point-v2:run:' || run_stamp,
        job_run_id, snapshot_id, model_id, policy_id,
        'sql_linear', issue_time, issue_time + interval '1 day',
        issue_time + interval '8 days', horizon_steps, interval '1 day',
        target_manifest, feature_checksum, model_checksum, parameter_checksum,
        jsonb_build_object(
            'classification', 'historical_sql_baseline_rehearsal',
            'evaluation_only', evaluation_only,
            'publication_authorized', publication_authorized,
            'operational_weather', false,
            'strategy_recommendation', false,
            'source_latest_observation', history_end,
            'source_age_at_issue_days', extract(epoch FROM issue_time - history_end) / 86400.0,
            'holdout_cutoff', holdout_cutoff
        )
    ) RETURNING id INTO forecast_run_id;
    INSERT INTO agri.job_output(
        job_run_id, output_key, kind, state, checksum_sha256,
        row_count, metadata_json, validated_at
    ) VALUES (
        job_run_id, 'nasa-power-ws2m-denver-point-v2:backtest',
        'forecast_backtest', 'validated', metrics_checksum, 1,
        jsonb_build_object(
            'mae', backtest.mae, 'rmse', backtest.rmse,
            'naive_rmse', backtest.naive_rmse, 'skill_score', backtest.skill_score,
            'bias', backtest.bias, 'mape', backtest.mape,
            'coverage_fraction', backtest.coverage_fraction,
            'residual_p10', bands.residual_p10,
            'residual_p50', bands.residual_p50,
            'residual_p90', bands.residual_p90
        ), issue_time
    ) RETURNING id INTO backtest_output_id;
    INSERT INTO agri.forecast_backtest_metric(
        forecast_run_id, job_output_id, series_id, cutoff_time,
        training_point_count, backtest_point_count, mae, rmse,
        naive_rmse, skill_score, bias, mape, coverage_fraction, metrics_checksum
    ) VALUES (
        forecast_run_id, backtest_output_id, series_id, holdout_cutoff,
        backtest.training_point_count, backtest.backtest_point_count,
        backtest.mae, backtest.rmse, backtest.naive_rmse,
        backtest.skill_score, backtest.bias, backtest.mape,
        backtest.coverage_fraction, metrics_checksum
    );

    FOR hindcast_cutoff IN
        SELECT cutoff
        FROM generate_series(
            '2025-05-08 00:00:00+00'::timestamptz,
            '2026-04-09 00:00:00+00'::timestamptz,
            interval '28 days'
        ) AS cutoff
        UNION ALL
        SELECT holdout_cutoff
        ORDER BY cutoff
    LOOP
        SELECT count(*)::integer
          INTO hindcast_training_count
          FROM agri.forecast_timeseries_base(target_release_set_id, issue_time) AS base
         WHERE base.series_id = series_id
           AND base.observed_at <= hindcast_cutoff;

        INSERT INTO agri.forecast_hindcast_run(
            hindcast_key, forecast_run_id, series_id, release_set_id,
            simulated_cutoff_time, uncertainty_calibration_cutoff_time,
            horizon_steps, step_interval, minimum_training_points,
            training_point_count, expected_value_count, availability_mode,
            input_release_checksum, model_checksum, parameter_checksum,
            receipt_digest_version
        ) VALUES (
            'nasa-power-ws2m-denver-point-v2:hindcast:'
                || to_char(hindcast_cutoff AT TIME ZONE 'UTC', 'YYYYMMDD'),
            forecast_run_id, series_id, target_release_set_id,
            hindcast_cutoff, hindcast_cutoff - interval '7 days',
            horizon_steps, interval '1 day', min_training_points,
            hindcast_training_count, horizon_steps, 'retrospective_pinned_release',
            target_manifest, model_checksum, parameter_checksum, 'hindcast_v2'
        ) RETURNING id INTO hindcast_id;

        WITH regression AS (
            SELECT *
            FROM agri.forecast_linear_regression(
                series_id, target_release_set_id, issue_time, hindcast_cutoff,
                horizon_steps, interval '1 day', min_training_points
            )
        ), calibration AS (
            SELECT *
            FROM agri.forecast_linear_residual_bands(
                series_id, target_release_set_id, issue_time,
                hindcast_cutoff - interval '7 days', horizon_steps,
                interval '1 day', min_training_points
            )
        ), naive AS (
            SELECT base.metric_value
            FROM agri.forecast_timeseries_base(target_release_set_id, issue_time) AS base
            WHERE base.series_id = series_id
              AND base.observed_at <= hindcast_cutoff
            ORDER BY base.observed_at DESC
            LIMIT 1
        )
        INSERT INTO agri.forecast_hindcast_value(
            hindcast_run_id, valid_time, horizon_step,
            point_value, p10_value, p50_value, p90_value,
            naive_value, actual_value, actual_source_release_id,
            actual_observation_checksum, actual_data_available_at
        )
        SELECT
            hindcast_id, regression.valid_time, regression.horizon_step,
            regression.forecast_value,
            regression.forecast_value + calibration.residual_p10,
            regression.forecast_value + calibration.residual_p50,
            regression.forecast_value + calibration.residual_p90,
            naive.metric_value,
            actual.metric_value,
            actual.source_release_id,
            actual.observation_checksum,
            source_release.data_available_at
        FROM regression
        CROSS JOIN calibration
        CROSS JOIN naive
        INNER JOIN agri.forecast_timeseries_base(target_release_set_id, issue_time) AS actual
            ON actual.series_id = series_id
           AND actual.observed_at = regression.valid_time
        INNER JOIN agri.source_release AS source_release
            ON source_release.id = actual.source_release_id
        WHERE regression.eligible
          AND calibration.eligible
        ORDER BY regression.horizon_step;

        SELECT count(*)::integer
          INTO hindcast_value_count
          FROM agri.forecast_hindcast_value
         WHERE hindcast_run_id = hindcast_id;
        IF hindcast_value_count <> horizon_steps THEN
            RAISE EXCEPTION 'hindcast % did not produce its declared seven-day evaluation grid',
                hindcast_cutoff;
        END IF;
        SELECT agri.forecast_hindcast_receipt_checksum(hindcast_id)
          INTO hindcast_checksum;
        PERFORM agri.finalize_forecast_hindcast_run(hindcast_id, hindcast_checksum);
    END LOOP;

    SELECT
        count(DISTINCT hindcast.id)::integer,
        count(DISTINCT hindcast.id) FILTER (WHERE hindcast.quality_passed)::integer,
        count(value.id)::integer,
        avg(value.absolute_error),
        sqrt(avg(value.squared_error)),
        sqrt(avg((value.naive_value - value.actual_value)
            * (value.naive_value - value.actual_value))),
        avg(value.absolute_error / nullif(abs(value.actual_value), 0)),
        count(value.id)::double precision / (count(DISTINCT hindcast.id) * horizon_steps),
        avg(CASE WHEN value.interval_covered THEN 1.0 ELSE 0.0 END)
      INTO hindcast_run_count, hindcast_pass_count, hindcast_value_count,
           hindcast_mae, hindcast_rmse, hindcast_naive_rmse, hindcast_mape,
           hindcast_coverage, hindcast_interval_coverage
      FROM agri.forecast_hindcast_run AS hindcast
      INNER JOIN agri.forecast_hindcast_value AS value ON value.hindcast_run_id = hindcast.id
     WHERE hindcast.forecast_run_id = forecast_run_id;
    SELECT encode(digest(string_agg(
        hindcast.receipt_checksum, E'\n' ORDER BY hindcast.simulated_cutoff_time
    ), 'sha256'), 'hex')
      INTO hindcast_manifest_checksum
      FROM agri.forecast_hindcast_run AS hindcast
     WHERE hindcast.forecast_run_id = forecast_run_id;
    hindcast_skill := CASE
        WHEN hindcast_naive_rmse = 0 AND hindcast_rmse = 0 THEN 1.0
        WHEN hindcast_naive_rmse = 0 THEN NULL
        ELSE 1.0 - hindcast_rmse / hindcast_naive_rmse
    END;
    hindcast_gates_passed := hindcast_run_count = 14
        AND hindcast_value_count = 98
        AND hindcast_coverage = 1.0
        AND hindcast_mae <= max_mae
        AND hindcast_rmse <= max_rmse
        AND hindcast_mape IS NOT NULL AND hindcast_mape <= max_mape
        AND hindcast_skill IS NOT NULL AND hindcast_skill >= min_skill
        AND hindcast_pass_count::double precision / hindcast_run_count
            >= min_hindcast_pass_fraction
        AND hindcast_interval_coverage >= min_interval_coverage;
    terminal_gates_passed := backtest.eligible
        AND bands.eligible
        AND backtest.training_point_count >= min_training_points
        AND backtest.backtest_point_count >= min_backtest_points
        AND backtest.coverage_fraction >= min_coverage
        AND backtest.mae <= max_mae
        AND backtest.rmse <= max_rmse
        AND backtest.mape IS NOT NULL AND backtest.mape <= max_mape
        AND backtest.skill_score IS NOT NULL AND backtest.skill_score >= min_skill;

    UPDATE agri.forecast_backtest_metric AS metric
       SET passed = terminal_gates_passed
     WHERE metric.forecast_run_id = forecast_run_id
       AND metric.series_id = series_id
       AND metric.cutoff_time = holdout_cutoff;
    INSERT INTO agri.job_output(
        job_run_id, output_key, kind, state, checksum_sha256,
        row_count, metadata_json, validated_at
    ) VALUES (
        job_run_id, 'nasa-power-ws2m-denver-point-v2:hindcast-receipt-manifest',
        'forecast_hindcast_receipts', 'validated', hindcast_manifest_checksum,
        hindcast_run_count,
        jsonb_build_object(
            'hindcast_value_count', hindcast_value_count,
            'terminal_metrics_checksum', metrics_checksum,
            'terminal_gate_passed', terminal_gates_passed,
            'historical_hindcast_gate_passed', hindcast_gates_passed
        ), clock_timestamp()
    ) RETURNING id INTO hindcast_output_id;

    UPDATE agri.forecast_run
       SET quality_summary = quality_summary || jsonb_build_object(
            'historical_hindcast_availability_mode', 'retrospective_pinned_release',
            'historical_hindcast_first_cutoff', '2025-05-08 00:00:00+00',
            'historical_hindcast_last_cutoff', holdout_cutoff,
            'historical_hindcast_run_count', hindcast_run_count,
            'historical_hindcast_value_count', hindcast_value_count,
            'historical_hindcast_pass_count', hindcast_pass_count,
            'historical_hindcast_pass_fraction',
                hindcast_pass_count::double precision / hindcast_run_count,
            'historical_hindcast_mae', hindcast_mae,
            'historical_hindcast_rmse', hindcast_rmse,
            'historical_hindcast_naive_rmse', hindcast_naive_rmse,
            'historical_hindcast_skill_score', hindcast_skill,
            'historical_hindcast_mape', hindcast_mape,
            'historical_hindcast_coverage_fraction', hindcast_coverage,
            'historical_hindcast_interval_coverage_fraction', hindcast_interval_coverage,
            'minimum_hindcast_pass_fraction', min_hindcast_pass_fraction,
            'minimum_interval_coverage_fraction', min_interval_coverage,
            'historical_hindcast_manifest_checksum', hindcast_manifest_checksum,
            'historical_hindcast_gate_passed', hindcast_gates_passed,
            'terminal_holdout_gate_passed', terminal_gates_passed,
            'hindcast_manifest_job_output_id', hindcast_output_id
       )
     WHERE id = forecast_run_id;

    gates_passed := terminal_gates_passed AND hindcast_gates_passed;
    IF NOT gates_passed THEN
        UPDATE agri.forecast_run
        SET status = 'rejected', quality_summary = quality_summary || jsonb_build_object(
            'gate_result', 'rejected',
            'evaluation_disposition', 'retain_evidence_do_not_publish',
            'backtest_metrics_checksum', metrics_checksum
        )
        WHERE id = forecast_run_id;
        RAISE NOTICE 'backtest gates failed; evidence retained in forecast_run %, no publication created',
            forecast_run_id;
        RETURN;
    END IF;

    IF evaluation_only THEN
        UPDATE agri.forecast_run
        SET quality_summary = quality_summary || jsonb_build_object(
            'gate_result', 'passed_evaluation_only',
            'evaluation_disposition', 'validated_without_forecast_receipt_or_publication',
            'backtest_metrics_checksum', metrics_checksum
        )
        WHERE id = forecast_run_id;
        PERFORM agri.validate_forecast_run(forecast_run_id);
        RAISE NOTICE
            'evaluation-only gates passed for forecast_run %; no receipt or publication created',
            forecast_run_id;
        RETURN;
    END IF;

    IF NOT publication_authorized THEN
        RAISE EXCEPTION
            'forecast publication is not explicitly authorized; no receipt or publication created';
    END IF;

    PERFORM agri.validate_forecast_run(forecast_run_id);

    CREATE TEMP TABLE first_metric_forecast_values ON COMMIT DROP AS
    SELECT
        regression.horizon_step,
        regression.valid_time,
        regression.forecast_value AS point_value,
        regression.forecast_value + bands.residual_p10 AS p10_value,
        regression.forecast_value + bands.residual_p50 AS p50_value,
        regression.forecast_value + bands.residual_p90 AS p90_value,
        jsonb_build_object(
            '0.1', regression.forecast_value + bands.residual_p10,
            '0.5', regression.forecast_value + bands.residual_p50,
            '0.9', regression.forecast_value + bands.residual_p90
        ) AS quantile_values
    FROM agri.forecast_linear_regression(
        series_id, target_release_set_id, issue_time, issue_time,
        horizon_steps, interval '1 day', min_training_points
    ) AS regression
    WHERE regression.eligible;
    SELECT count(*) INTO forecast_value_count FROM first_metric_forecast_values;
    IF forecast_value_count <> horizon_steps THEN
        RAISE EXCEPTION 'validated regression did not produce the declared forecast horizon';
    END IF;
    SELECT encode(digest(coalesce(string_agg(
        concat_ws('|',
            to_char(value.valid_time AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
            value.horizon_step::text, value.point_value::text,
            value.p10_value::text, value.p50_value::text, value.p90_value::text,
            value.quantile_values::text
        ), E'\n' ORDER BY value.horizon_step
    ), ''), 'sha256'), 'hex')
      INTO receipt_checksum
      FROM first_metric_forecast_values AS value;

    INSERT INTO agri.job_output(
        job_run_id, output_key, kind, state, checksum_sha256,
        row_count, metadata_json, validated_at
    ) VALUES (
        job_run_id, 'nasa-power-ws2m-denver-point-v2:forecast-values',
        'forecast_values', 'validated', receipt_checksum, horizon_steps,
        jsonb_build_object('metrics_checksum', metrics_checksum), issue_time
    ) RETURNING id INTO forecast_output_id;
    INSERT INTO agri.forecast_receipt(
        forecast_run_id, job_output_id, series_id, issue_time,
        valid_from, valid_to, expected_value_count, quantile_levels,
        quality_summary
    ) VALUES (
        forecast_run_id, forecast_output_id, series_id, issue_time,
        issue_time + interval '1 day', issue_time + interval '8 days',
        horizon_steps, ARRAY[0.1, 0.5, 0.9]::double precision[],
        jsonb_build_object(
            'backtest_metrics_checksum', metrics_checksum,
            'uncertainty_method', 'empirical_terminal_holdout_residual_quantiles',
            'residual_sample_count', bands.backtest_point_count,
            'source_age_at_issue_days', extract(epoch FROM issue_time - history_end) / 86400.0,
            'operational_weather', false
        )
    ) RETURNING id INTO receipt_id;
    INSERT INTO agri.forecast_value(
        forecast_receipt_id, valid_time, horizon_step,
        point_value, p10_value, p50_value, p90_value, quantile_values
    )
    SELECT receipt_id, valid_time, horizon_step,
           point_value, p10_value, p50_value, p90_value, quantile_values
    FROM first_metric_forecast_values
    ORDER BY horizon_step;
    PERFORM agri.finalize_forecast_receipt(receipt_id, receipt_checksum);

    publication_checksum := encode(digest(receipt_checksum, 'sha256'), 'hex');
    INSERT INTO agri.job_output(
        job_run_id, output_key, kind, state, checksum_sha256,
        row_count, metadata_json, validated_at
    ) VALUES (
        job_run_id, 'nasa-power-ws2m-denver-point-v2:publication',
        'forecast_publication', 'validated', publication_checksum, 1,
        jsonb_build_object('receipt_checksum', receipt_checksum), issue_time
    ) RETURNING id INTO publication_output_id;
    INSERT INTO agri.forecast_publication(
        publication_key, job_run_id, job_output_id, release_set_id, scope_key
    ) VALUES (
        'nasa-power-ws2m-denver-point-v2:publication:' || run_stamp,
        job_run_id, publication_output_id, target_release_set_id,
        'nasa-power-ws2m-denver-point-v2'
    ) RETURNING id INTO publication_id;
    INSERT INTO agri.forecast_publication_item(publication_id, forecast_receipt_id)
    VALUES (publication_id, receipt_id);
    PERFORM agri.publish_forecast_publication(publication_id, publication_checksum);
    INSERT INTO agri.publication_pointer(
        product, scope_key, job_output_id, release_set_id, revision, published_by
    ) VALUES (
        'forecast_series', 'nasa-power-ws2m-denver-point-v2',
        publication_output_id, target_release_set_id, 1,
        'plantgeo-local-forecast-publisher'
    );
    RAISE NOTICE 'published local metric forecast receipt %, checksum %, publication %',
        receipt_id, receipt_checksum, publication_id;
END
$first_metric_forecast$;

COMMIT;

SELECT
    run.id AS forecast_run_id,
    run.status,
    metric.training_point_count,
    metric.backtest_point_count,
    metric.mae,
    metric.rmse,
    metric.naive_rmse,
    metric.skill_score,
    metric.bias,
    metric.mape,
    metric.coverage_fraction,
    metric.passed,
    metric.metrics_checksum
FROM agri.forecast_run AS run
JOIN agri.forecast_backtest_metric AS metric ON metric.forecast_run_id = run.id
WHERE run.id = (
    SELECT latest.id FROM agri.forecast_run AS latest
    WHERE latest.run_key LIKE 'nasa-power-ws2m-denver-point-v2:run:%'
    ORDER BY latest.created_at DESC LIMIT 1
);

SELECT
    count(*) AS hindcast_run_count,
    count(*) FILTER (WHERE hindcast.quality_passed) AS quality_passed_run_count,
    min(hindcast.simulated_cutoff_time) AS first_simulated_cutoff,
    max(hindcast.simulated_cutoff_time) AS last_simulated_cutoff,
    sum(hindcast.expected_value_count) AS hindcast_value_count,
    encode(digest(string_agg(
        hindcast.receipt_checksum, E'\n' ORDER BY hindcast.simulated_cutoff_time
    ), 'sha256'), 'hex') AS hindcast_manifest_checksum
FROM agri.forecast_hindcast_run AS hindcast
JOIN agri.forecast_run AS run ON run.id = hindcast.forecast_run_id
WHERE run.id = (
    SELECT latest.id FROM agri.forecast_run AS latest
    WHERE latest.run_key LIKE 'nasa-power-ws2m-denver-point-v2:run:%'
    ORDER BY latest.created_at DESC LIMIT 1
);

SELECT
    publication.id AS publication_id,
    publication.state,
    publication.manifest_checksum,
    receipt.id AS forecast_receipt_id,
    receipt.status AS receipt_status,
    receipt.receipt_checksum,
    receipt.quality_summary,
    count(value.id) AS forecast_value_count,
    min(value.valid_time) AS valid_from,
    max(value.valid_time) AS valid_through
FROM agri.forecast_publication AS publication
JOIN agri.forecast_publication_item AS item ON item.publication_id = publication.id
JOIN agri.forecast_receipt AS receipt ON receipt.id = item.forecast_receipt_id
JOIN agri.forecast_value AS value ON value.forecast_receipt_id = receipt.id
WHERE publication.id = (
    SELECT latest.id FROM agri.forecast_publication AS latest
    WHERE latest.publication_key LIKE 'nasa-power-ws2m-denver-point-v2:publication:%'
    ORDER BY latest.created_at DESC LIMIT 1
)
GROUP BY publication.id, receipt.id;
