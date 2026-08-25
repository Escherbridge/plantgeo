"""Add the generic deterministic forecast-iteration pipeline.

Revision ID: 20260723_0010
Revises: 20260723_0009
"""

from collections.abc import Sequence

from alembic import op

revision = "20260723_0010"
down_revision = "20260723_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE TABLE agri.forecast_input_recorded_at (
            input_kind varchar(64) NOT NULL,
            input_key text NOT NULL,
            recorded_at timestamptz NOT NULL,
            PRIMARY KEY(input_kind, input_key)
        );

        WITH recording_boundary AS (
            SELECT clock_timestamp() AS recorded_at
        )
        INSERT INTO agri.forecast_input_recorded_at(input_kind, input_key, recorded_at)
        SELECT 'data_source', id::text, recording_boundary.recorded_at
        FROM agri.data_source CROSS JOIN recording_boundary
        UNION ALL
        SELECT 'source_release', id::text, recording_boundary.recorded_at
        FROM agri.source_release CROSS JOIN recording_boundary
        UNION ALL
        SELECT 'release_set', id::text, recording_boundary.recorded_at
        FROM agri.release_set CROSS JOIN recording_boundary
        UNION ALL
        SELECT 'forecast_series', id::text, recording_boundary.recorded_at
        FROM agri.forecast_series CROSS JOIN recording_boundary;

        CREATE FUNCTION agri.record_forecast_input_change()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, agri
        AS $$
        BEGIN
            INSERT INTO agri.forecast_input_recorded_at(
                input_kind,
                input_key,
                recorded_at
            )
            VALUES (TG_TABLE_NAME, NEW.id::text, clock_timestamp())
            ON CONFLICT (input_kind, input_key) DO UPDATE
                SET recorded_at = EXCLUDED.recorded_at;
            RETURN NEW;
        END
        $$;

        CREATE FUNCTION agri.record_forecast_release_content_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, agri
        AS $$
        BEGIN
            INSERT INTO agri.forecast_input_recorded_at(
                input_kind,
                input_key,
                recorded_at
            )
            SELECT
                'source_release',
                changed.source_release_id::text,
                boundary.recorded_at
            FROM (
                SELECT DISTINCT source_release_id FROM new_rows
            ) AS changed
            CROSS JOIN LATERAL (SELECT clock_timestamp() AS recorded_at) AS boundary
            ON CONFLICT (input_kind, input_key) DO UPDATE
                SET recorded_at = EXCLUDED.recorded_at;
            RETURN NULL;
        END
        $$;

        CREATE FUNCTION agri.record_forecast_release_content_update()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, agri
        AS $$
        BEGIN
            INSERT INTO agri.forecast_input_recorded_at(
                input_kind,
                input_key,
                recorded_at
            )
            SELECT
                'source_release',
                changed.source_release_id::text,
                boundary.recorded_at
            FROM (
                SELECT source_release_id FROM new_rows
                UNION
                SELECT source_release_id FROM old_rows
            ) AS changed
            CROSS JOIN LATERAL (SELECT clock_timestamp() AS recorded_at) AS boundary
            ON CONFLICT (input_kind, input_key) DO UPDATE
                SET recorded_at = EXCLUDED.recorded_at;
            RETURN NULL;
        END
        $$;

        CREATE FUNCTION agri.record_forecast_release_content_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, agri
        AS $$
        BEGIN
            INSERT INTO agri.forecast_input_recorded_at(
                input_kind,
                input_key,
                recorded_at
            )
            SELECT
                'source_release',
                changed.source_release_id::text,
                boundary.recorded_at
            FROM (
                SELECT DISTINCT source_release_id FROM old_rows
            ) AS changed
            CROSS JOIN LATERAL (SELECT clock_timestamp() AS recorded_at) AS boundary
            ON CONFLICT (input_kind, input_key) DO UPDATE
                SET recorded_at = EXCLUDED.recorded_at;
            RETURN NULL;
        END
        $$;

        CREATE FUNCTION agri.record_forecast_release_set_item_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, agri
        AS $$
        BEGIN
            INSERT INTO agri.forecast_input_recorded_at(
                input_kind,
                input_key,
                recorded_at
            )
            SELECT
                'release_set',
                changed.release_set_id::text,
                boundary.recorded_at
            FROM (
                SELECT DISTINCT release_set_id FROM new_rows
            ) AS changed
            CROSS JOIN LATERAL (SELECT clock_timestamp() AS recorded_at) AS boundary
            ON CONFLICT (input_kind, input_key) DO UPDATE
                SET recorded_at = EXCLUDED.recorded_at;
            RETURN NULL;
        END
        $$;

        CREATE FUNCTION agri.record_forecast_release_set_item_update()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, agri
        AS $$
        BEGIN
            INSERT INTO agri.forecast_input_recorded_at(
                input_kind,
                input_key,
                recorded_at
            )
            SELECT
                'release_set',
                changed.release_set_id::text,
                boundary.recorded_at
            FROM (
                SELECT release_set_id FROM new_rows
                UNION
                SELECT release_set_id FROM old_rows
            ) AS changed
            CROSS JOIN LATERAL (SELECT clock_timestamp() AS recorded_at) AS boundary
            ON CONFLICT (input_kind, input_key) DO UPDATE
                SET recorded_at = EXCLUDED.recorded_at;
            RETURN NULL;
        END
        $$;

        CREATE FUNCTION agri.record_forecast_release_set_item_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, agri
        AS $$
        BEGIN
            INSERT INTO agri.forecast_input_recorded_at(
                input_kind,
                input_key,
                recorded_at
            )
            SELECT
                'release_set',
                changed.release_set_id::text,
                boundary.recorded_at
            FROM (
                SELECT DISTINCT release_set_id FROM old_rows
            ) AS changed
            CROSS JOIN LATERAL (SELECT clock_timestamp() AS recorded_at) AS boundary
            ON CONFLICT (input_kind, input_key) DO UPDATE
                SET recorded_at = EXCLUDED.recorded_at;
            RETURN NULL;
        END
        $$;

        CREATE TRIGGER forecast_input_record_data_source
            AFTER INSERT OR UPDATE ON agri.data_source
            FOR EACH ROW EXECUTE FUNCTION agri.record_forecast_input_change();
        CREATE TRIGGER forecast_input_record_source_release
            AFTER INSERT OR UPDATE ON agri.source_release
            FOR EACH ROW EXECUTE FUNCTION agri.record_forecast_input_change();
        CREATE TRIGGER forecast_input_record_release_set
            AFTER INSERT OR UPDATE ON agri.release_set
            FOR EACH ROW EXECUTE FUNCTION agri.record_forecast_input_change();
        CREATE TRIGGER forecast_input_record_series
            AFTER INSERT OR UPDATE ON agri.forecast_series
            FOR EACH ROW EXECUTE FUNCTION agri.record_forecast_input_change();

        CREATE TRIGGER forecast_input_record_release_set_item_insert
            AFTER INSERT ON agri.release_set_item
            REFERENCING NEW TABLE AS new_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION agri.record_forecast_release_set_item_insert();
        CREATE TRIGGER forecast_input_record_release_set_item_update
            AFTER UPDATE ON agri.release_set_item
            REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION agri.record_forecast_release_set_item_update();
        CREATE TRIGGER forecast_input_record_release_set_item_delete
            AFTER DELETE ON agri.release_set_item
            REFERENCING OLD TABLE AS old_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION agri.record_forecast_release_set_item_delete();

        CREATE TRIGGER forecast_input_record_entity_state_insert
            AFTER INSERT ON agri.forecast_entity_state
            REFERENCING NEW TABLE AS new_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION agri.record_forecast_release_content_insert();
        CREATE TRIGGER forecast_input_record_entity_state_update
            AFTER UPDATE ON agri.forecast_entity_state
            REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION agri.record_forecast_release_content_update();
        CREATE TRIGGER forecast_input_record_entity_state_delete
            AFTER DELETE ON agri.forecast_entity_state
            REFERENCING OLD TABLE AS old_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION agri.record_forecast_release_content_delete();

        CREATE TRIGGER forecast_input_record_observation_insert
            AFTER INSERT ON agri.forecast_observation
            REFERENCING NEW TABLE AS new_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION agri.record_forecast_release_content_insert();
        CREATE TRIGGER forecast_input_record_observation_update
            AFTER UPDATE ON agri.forecast_observation
            REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION agri.record_forecast_release_content_update();
        CREATE TRIGGER forecast_input_record_observation_delete
            AFTER DELETE ON agri.forecast_observation
            REFERENCING OLD TABLE AS old_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION agri.record_forecast_release_content_delete();

        CREATE TRIGGER forecast_input_record_signal_observation_insert
            AFTER INSERT ON agri.signal_observation
            REFERENCING NEW TABLE AS new_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION agri.record_forecast_release_content_insert();
        CREATE TRIGGER forecast_input_record_signal_observation_update
            AFTER UPDATE ON agri.signal_observation
            REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION agri.record_forecast_release_content_update();
        CREATE TRIGGER forecast_input_record_signal_observation_delete
            AFTER DELETE ON agri.signal_observation
            REFERENCING OLD TABLE AS old_rows
            FOR EACH STATEMENT
            EXECUTE FUNCTION agri.record_forecast_release_content_delete();

        CREATE VIEW agri.v_forecast_timeseries_contract AS
        WITH enriched AS (
            SELECT
                series.id AS series_id,
                series.series_key,
                series.source_variant_key,
                series.input_adapter,
                series.data_source_id,
                source.key AS data_source_key,
                source.name AS data_source_name,
                source.owner AS data_source_owner,
                source.license_name,
                source.license_url,
                source.citation,
                source.review_state AS data_source_review_state,
                to_char(
                    source.created_at AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                ) AS data_source_registered_at,
                to_char(
                    source.updated_at AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                ) AS data_source_updated_at,
                to_char(
                    source_record.recorded_at AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                ) AS data_source_recorded_at,
                series.source_transform_version,
                series.entity_type,
                series.entity_key,
                series.metric_name,
                series.metric_unit,
                series.signal_name,
                series.source_parameter,
                series.support_key,
                series.spatial_cell_id,
                series.representation_kind,
                series.spatial_support_kind,
                series.source_spatial_resolution_m,
                series.output_spatial_resolution_m,
                series.source_temporal_support,
                series.output_temporal_support AS desired_temporal_grain,
                series.aggregation_method,
                series.metadata_json,
                to_char(
                    series.created_at AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                ) AS contract_registered_at,
                to_char(
                    series_record.recorded_at AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                ) AS contract_recorded_at
            FROM agri.forecast_series AS series
            INNER JOIN agri.data_source AS source ON source.id = series.data_source_id
            INNER JOIN agri.forecast_input_recorded_at AS source_record
                ON source_record.input_kind = 'data_source'
               AND source_record.input_key = source.id::text
            INNER JOIN agri.forecast_input_recorded_at AS series_record
                ON series_record.input_kind = 'forecast_series'
               AND series_record.input_key = series.id::text
        ),
        canonical AS (
            SELECT
                enriched.*,
                (
                    to_jsonb(enriched)
                    - 'source_temporal_support'
                    - 'desired_temporal_grain'
                ) || jsonb_build_object(
                    'source_temporal_support',
                    extract(epoch FROM enriched.source_temporal_support),
                    'desired_temporal_grain',
                    extract(epoch FROM enriched.desired_temporal_grain)
                ) AS contract_snapshot
            FROM enriched
        )
        SELECT
            canonical.*,
            encode(
                digest(
                    concat_ws(
                        '|',
                        'forecast_timeseries_contract_v1',
                        canonical.contract_snapshot::text
                    ),
                    'sha256'
                ),
                'hex'
            ) AS contract_checksum,
            'forecast_timeseries_contract_v1'::text AS contract_version
        FROM canonical;

        CREATE FUNCTION agri.forecast_timeseries_contract(
            p_release_set_id uuid,
            p_as_of_time timestamptz
        )
        RETURNS TABLE(
            observation_id bigint,
            series_id uuid,
            series_key text,
            source_variant_key text,
            input_adapter text,
            data_source_id uuid,
            data_source_key text,
            data_source_name text,
            data_source_owner text,
            data_source_review_state text,
            license_name text,
            license_url text,
            citation text,
            entity_type text,
            entity_key text,
            entity_state_id uuid,
            entity_state_checksum text,
            metric_name text,
            metric_unit text,
            observed_at timestamptz,
            metric_value double precision,
            data_available_at timestamptz,
            representation_kind text,
            spatial_support_kind text,
            source_spatial_resolution_m integer,
            output_spatial_resolution_m integer,
            source_temporal_support interval,
            desired_temporal_grain interval,
            aggregation_method text,
            spatial_cell_id uuid,
            source_release_id uuid,
            source_release_license_snapshot text,
            input_release_checksum text,
            observation_checksum text,
            series_metadata jsonb,
            contract_snapshot jsonb,
            contract_checksum text,
            contract_version text
        )
        LANGUAGE plpgsql
        STABLE
        SET timezone = 'UTC'
        SET datestyle = 'ISO, MDY'
        SET intervalstyle = 'postgres'
        SET extra_float_digits = 1
        AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM agri.forecast_timeseries_base(p_release_set_id, p_as_of_time) AS base
                INNER JOIN agri.v_forecast_timeseries_contract AS contract
                    ON contract.series_id = base.series_id
                WHERE base.spatial_support_kind IS DISTINCT FROM contract.spatial_support_kind
                   OR base.source_spatial_resolution_m
                        IS DISTINCT FROM contract.source_spatial_resolution_m
                   OR base.output_spatial_resolution_m
                        IS DISTINCT FROM contract.output_spatial_resolution_m
            ) THEN
                RAISE EXCEPTION
                    'governed source support conflicts with the registered forecast series contract';
            END IF;

            RETURN QUERY
            SELECT
                base.observation_id,
                contract.series_id,
                contract.series_key::text,
                contract.source_variant_key::text,
                contract.input_adapter::text,
                contract.data_source_id,
                contract.data_source_key::text,
                contract.data_source_name::text,
                contract.data_source_owner::text,
                contract.data_source_review_state::text,
                contract.license_name::text,
                contract.license_url::text,
                contract.citation::text,
                contract.entity_type::text,
                contract.entity_key::text,
                base.entity_state_id,
                base.entity_state_checksum,
                contract.metric_name::text,
                contract.metric_unit::text,
                base.observed_at,
                base.metric_value,
                greatest(
                    source_release.data_available_at,
                    source_release_record.recorded_at,
                    coalesce(observation.data_available_at, source_release.data_available_at),
                    coalesce(signal_observation.data_available_at, source_release.data_available_at),
                    coalesce(entity_state.data_available_at, source_release.data_available_at),
                    release_set.as_of_time,
                    release_set.validated_at,
                    release_set_record.recorded_at,
                    contract.data_source_recorded_at::timestamptz,
                    contract.contract_recorded_at::timestamptz
                ),
                base.representation_kind::text,
                base.spatial_support_kind::text,
                base.source_spatial_resolution_m,
                base.output_spatial_resolution_m,
                base.source_temporal_support,
                base.output_temporal_support,
                contract.aggregation_method::text,
                base.spatial_cell_id,
                base.source_release_id,
                source_release.license_snapshot::text,
                base.input_release_checksum,
                base.observation_checksum,
                contract.metadata_json,
                contract.contract_snapshot,
                contract.contract_checksum,
                contract.contract_version
            FROM agri.forecast_timeseries_base(p_release_set_id, p_as_of_time) AS base
            INNER JOIN agri.v_forecast_timeseries_contract AS contract
                ON contract.series_id = base.series_id
            INNER JOIN agri.source_release AS source_release
                ON source_release.id = base.source_release_id
            INNER JOIN agri.forecast_input_recorded_at AS source_release_record
                ON source_release_record.input_kind = 'source_release'
               AND source_release_record.input_key = source_release.id::text
            INNER JOIN agri.release_set AS release_set
                ON release_set.id = p_release_set_id
            INNER JOIN agri.forecast_input_recorded_at AS release_set_record
                ON release_set_record.input_kind = 'release_set'
               AND release_set_record.input_key = release_set.id::text
            LEFT JOIN agri.forecast_observation AS observation
                ON observation.id = base.observation_id
            LEFT JOIN agri.forecast_entity_state AS entity_state
                ON entity_state.id = base.entity_state_id
            LEFT JOIN agri.signal_observation AS signal_observation
                ON contract.input_adapter = 'signal_observation'
               AND signal_observation.source_release_id = base.source_release_id
               AND signal_observation.cell_id = base.spatial_cell_id
               AND signal_observation.signal_name = contract.signal_name
               AND signal_observation.source_parameter = contract.source_parameter
               AND signal_observation.support_key = contract.support_key
               AND signal_observation.observed_at = base.observed_at
               AND signal_observation.normalized_value IS NOT DISTINCT FROM base.metric_value
            WHERE source_release_record.recorded_at <= p_as_of_time
              AND release_set_record.recorded_at <= p_as_of_time
              AND contract.data_source_recorded_at::timestamptz <= p_as_of_time
              AND contract.contract_recorded_at::timestamptz <= p_as_of_time
              AND (
                    contract.input_adapter <> 'signal_observation'
                    OR signal_observation.id IS NOT NULL
              )
              AND (
                    contract.input_adapter <> 'forecast_observation'
                    OR observation.id IS NOT NULL
              );
        END
        $$;

        CREATE FUNCTION agri.forecast_date_spine(
            p_start_time timestamptz,
            p_end_time timestamptz,
            p_grain interval DEFAULT interval '1 day'
        )
        RETURNS TABLE(
            grain_index integer,
            bucket_start timestamptz,
            bucket_end timestamptz
        )
        LANGUAGE plpgsql
        IMMUTABLE
        SET timezone = 'UTC'
        SET datestyle = 'ISO, MDY'
        SET intervalstyle = 'postgres'
        SET extra_float_digits = 1
        AS $$
        BEGIN
            IF p_grain <> interval '1 day' THEN
                RAISE EXCEPTION 'forecast date spine v1 supports exactly one-day UTC grain';
            END IF;
            IF p_start_time <> date_trunc('day', p_start_time AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
               OR p_end_time <> date_trunc('day', p_end_time AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' THEN
                RAISE EXCEPTION 'forecast date spine boundaries must be UTC day starts';
            END IF;
            IF p_end_time <= p_start_time THEN
                RAISE EXCEPTION 'forecast date spine window must be non-empty';
            END IF;

            RETURN QUERY
            SELECT
                generated.grain_index,
                p_start_time + make_interval(days => generated.grain_index),
                p_start_time + make_interval(days => generated.grain_index + 1)
            FROM generate_series(
                0,
                ((extract(epoch FROM (p_end_time - p_start_time)) / 86400)::integer - 1)
            ) AS generated(grain_index)
            ORDER BY generated.grain_index;
        END
        $$;

        CREATE FUNCTION agri.forecast_aligned_daily_series(
            p_series_id uuid,
            p_release_set_id uuid,
            p_as_of_time timestamptz,
            p_window_start timestamptz,
            p_window_end timestamptz,
            p_gap_policy varchar DEFAULT 'strict'
        )
        RETURNS TABLE(
            grain_index integer,
            bucket_start timestamptz,
            bucket_end timestamptz,
            metric_value double precision,
            source_sample_count bigint,
            is_missing boolean,
            is_imputed boolean,
            source_release_ids uuid[],
            observation_checksums text[],
            data_available_at timestamptz,
            alignment_checksum text
        )
        LANGUAGE plpgsql
        STABLE
        SET timezone = 'UTC'
        SET datestyle = 'ISO, MDY'
        SET intervalstyle = 'postgres'
        SET extra_float_digits = 1
        AS $$
        DECLARE
            source_support interval;
            desired_grain interval;
            configured_aggregation text;
        BEGIN
            IF p_gap_policy NOT IN ('strict', 'locf') THEN
                RAISE EXCEPTION 'daily alignment gap policy must be strict or locf';
            END IF;

            SELECT
                contract.source_temporal_support,
                contract.desired_temporal_grain,
                contract.aggregation_method
              INTO source_support, desired_grain, configured_aggregation
              FROM agri.v_forecast_timeseries_contract AS contract
             WHERE contract.series_id = p_series_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'forecast series % is not registered', p_series_id;
            END IF;
            IF source_support > interval '1 day' OR desired_grain > interval '1 day' THEN
                RAISE EXCEPTION
                    'daily alignment refuses to fabricate daily precision from source support % or desired grain %',
                    source_support,
                    desired_grain;
            END IF;
            IF source_support < interval '1 day'
               AND lower(coalesce(configured_aggregation, ''))
                    NOT IN ('mean', 'avg', 'daily_mean') THEN
                RAISE EXCEPTION
                    'daily alignment v1 requires explicit mean aggregation for subdaily sources';
            END IF;

            RETURN QUERY
            WITH daily AS (
                SELECT
                    spine.grain_index,
                    spine.bucket_start,
                    spine.bucket_end,
                    avg(contract.metric_value) AS metric_value,
                    count(contract.metric_value) AS source_sample_count,
                    coalesce(
                        array_agg(DISTINCT contract.source_release_id ORDER BY contract.source_release_id)
                            FILTER (WHERE contract.source_release_id IS NOT NULL),
                        ARRAY[]::uuid[]
                    ) AS source_release_ids,
                    coalesce(
                        array_agg(DISTINCT contract.observation_checksum ORDER BY contract.observation_checksum)
                            FILTER (WHERE contract.observation_checksum IS NOT NULL),
                        ARRAY[]::text[]
                    ) AS observation_checksums,
                    max(contract.data_available_at) AS data_available_at
                FROM agri.forecast_date_spine(
                    p_window_start,
                    p_window_end,
                    interval '1 day'
                ) AS spine
                LEFT JOIN agri.forecast_timeseries_contract(
                    p_release_set_id,
                    p_as_of_time
                ) AS contract
                  ON contract.series_id = p_series_id
                 AND contract.observed_at >= spine.bucket_start
                 AND contract.observed_at < spine.bucket_end
                GROUP BY spine.grain_index, spine.bucket_start, spine.bucket_end
            ),
            selected AS (
                SELECT
                    daily.grain_index,
                    daily.bucket_start,
                    daily.bucket_end,
                    coalesce(daily.metric_value, carried.metric_value) AS metric_value,
                    daily.source_sample_count,
                    coalesce(daily.metric_value, carried.metric_value) IS NULL AS is_missing,
                    daily.metric_value IS NULL AND carried.metric_value IS NOT NULL AS is_imputed,
                    CASE
                        WHEN daily.metric_value IS NOT NULL THEN daily.source_release_ids
                        ELSE coalesce(carried.source_release_ids, ARRAY[]::uuid[])
                    END AS source_release_ids,
                    CASE
                        WHEN daily.metric_value IS NOT NULL THEN daily.observation_checksums
                        ELSE coalesce(carried.observation_checksums, ARRAY[]::text[])
                    END AS observation_checksums,
                    coalesce(daily.data_available_at, carried.data_available_at) AS data_available_at
                FROM daily
                LEFT JOIN LATERAL (
                    SELECT
                        previous.metric_value,
                        previous.source_release_ids,
                        previous.observation_checksums,
                        previous.data_available_at
                    FROM daily AS previous
                    WHERE p_gap_policy = 'locf'
                      AND daily.metric_value IS NULL
                      AND previous.bucket_start = daily.bucket_start - interval '1 day'
                      AND previous.metric_value IS NOT NULL
                    LIMIT 1
                ) AS carried ON true
            )
            SELECT
                selected.grain_index,
                selected.bucket_start,
                selected.bucket_end,
                selected.metric_value,
                selected.source_sample_count,
                selected.is_missing,
                selected.is_imputed,
                selected.source_release_ids,
                selected.observation_checksums,
                selected.data_available_at,
                encode(
                    digest(
                        concat_ws(
                            '|',
                            'forecast_daily_alignment_v1',
                            p_series_id::text,
                            p_release_set_id::text,
                            selected.bucket_start::text,
                            coalesce(selected.metric_value::text, ''),
                            selected.source_sample_count::text,
                            selected.is_missing::text,
                            selected.is_imputed::text,
                            selected.source_release_ids::text,
                            selected.observation_checksums::text,
                            coalesce(selected.data_available_at::text, ''),
                            p_gap_policy,
                            source_support::text,
                            desired_grain::text,
                            coalesce(configured_aggregation, '')
                        ),
                        'sha256'
                    ),
                    'hex'
                )
            FROM selected
            ORDER BY selected.bucket_start;
        END
        $$;

        CREATE FUNCTION agri.forecast_iteration_value_checksum(
            p_valid_time timestamptz,
            p_horizon_step integer,
            p_low_value double precision,
            p_median_value double precision,
            p_high_value double precision,
            p_increment_count integer,
            p_parameter_checksum varchar
        )
        RETURNS varchar
        LANGUAGE sql
        IMMUTABLE
        SET timezone = 'UTC'
        SET datestyle = 'ISO, MDY'
        SET intervalstyle = 'postgres'
        SET extra_float_digits = 1
        AS $$
            SELECT encode(
                digest(
                    concat_ws(
                        '|',
                        'forecast_iteration_value_v1',
                        p_valid_time::text,
                        p_horizon_step::text,
                        p_low_value::text,
                        p_median_value::text,
                        p_high_value::text,
                        p_increment_count::text,
                        p_parameter_checksum
                    ),
                    'sha256'
                ),
                'hex'
            )::varchar
        $$;

        CREATE FUNCTION agri.forecast_daily_bootstrap(
            p_series_id uuid,
            p_release_set_id uuid,
            p_as_of_time timestamptz,
            p_cutoff_time timestamptz,
            p_history_start timestamptz DEFAULT NULL,
            p_horizon_days integer DEFAULT 30,
            p_simulation_count integer DEFAULT 1000,
            p_seed bigint DEFAULT 0,
            p_gap_policy varchar DEFAULT 'strict',
            p_lower_bound double precision DEFAULT NULL,
            p_upper_bound double precision DEFAULT NULL
        )
        RETURNS TABLE(
            horizon_step integer,
            valid_time timestamptz,
            low_value double precision,
            median_value double precision,
            high_value double precision,
            increment_count integer,
            parameter_checksum text
        )
        LANGUAGE plpgsql
        STABLE
        SET timezone = 'UTC'
        SET datestyle = 'ISO, MDY'
        SET intervalstyle = 'postgres'
        SET extra_float_digits = 1
        AS $$
        DECLARE
            effective_history_start timestamptz;
            baseline_value double precision;
            available_increment_count integer;
            release_checksum text;
            series_contract_checksum text;
            aligned_history_checksum text;
            computed_parameter_checksum text;
        BEGIN
            IF p_cutoff_time <> date_trunc('day', p_cutoff_time AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
               OR (
                    p_history_start IS NOT NULL
                    AND p_history_start <>
                        date_trunc('day', p_history_start AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
               ) THEN
                RAISE EXCEPTION 'bootstrap cutoff and history start must be UTC day starts';
            END IF;
            IF p_cutoff_time > p_as_of_time THEN
                RAISE EXCEPTION 'bootstrap cutoff cannot be later than the as-of boundary';
            END IF;
            IF p_horizon_days < 1 OR p_horizon_days > 366 THEN
                RAISE EXCEPTION 'bootstrap horizon must be between 1 and 366 days';
            END IF;
            IF p_simulation_count < 100 OR p_simulation_count > 10000 THEN
                RAISE EXCEPTION 'bootstrap simulation count must be between 100 and 10000';
            END IF;
            IF p_as_of_time > clock_timestamp() THEN
                RAISE EXCEPTION 'bootstrap as-of boundary cannot be in the future';
            END IF;
            IF p_gap_policy NOT IN ('strict', 'locf') THEN
                RAISE EXCEPTION 'bootstrap gap policy must be strict or locf';
            END IF;
            IF p_lower_bound IS NOT NULL AND p_upper_bound IS NOT NULL
               AND p_lower_bound > p_upper_bound THEN
                RAISE EXCEPTION 'bootstrap lower bound cannot exceed upper bound';
            END IF;
            IF p_lower_bound::text IN ('NaN', 'Infinity', '-Infinity')
               OR p_upper_bound::text IN ('NaN', 'Infinity', '-Infinity') THEN
                RAISE EXCEPTION 'bootstrap bounds must be finite when supplied';
            END IF;

            SELECT release_set.manifest_checksum
              INTO release_checksum
              FROM agri.release_set AS release_set
             WHERE release_set.id = p_release_set_id
               AND release_set.state IN ('validated', 'published')
               AND release_set.validated_at <= p_as_of_time
               AND release_set.as_of_time <= p_as_of_time;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'bootstrap release set is not validated by the as-of boundary';
            END IF;

            SELECT contract.contract_checksum
              INTO series_contract_checksum
              FROM agri.v_forecast_timeseries_contract AS contract
             WHERE contract.series_id = p_series_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'bootstrap series % is not registered', p_series_id;
            END IF;

            SELECT coalesce(
                       p_history_start,
                       min(
                           date_trunc('day', contract.observed_at AT TIME ZONE 'UTC')
                               AT TIME ZONE 'UTC'
                       )
                   )
              INTO effective_history_start
              FROM agri.forecast_timeseries_contract(
                  p_release_set_id,
                  p_as_of_time
              ) AS contract
             WHERE contract.series_id = p_series_id
               AND contract.observed_at < p_cutoff_time + interval '1 day';
            IF effective_history_start IS NULL OR effective_history_start > p_cutoff_time THEN
                RAISE EXCEPTION 'bootstrap history is empty at the requested cutoff';
            END IF;

            SELECT aligned.metric_value
              INTO baseline_value
              FROM agri.forecast_aligned_daily_series(
                  p_series_id,
                  p_release_set_id,
                  p_as_of_time,
                  effective_history_start,
                  p_cutoff_time + interval '1 day',
                  p_gap_policy
              ) AS aligned
             WHERE aligned.bucket_start = p_cutoff_time
               AND NOT aligned.is_missing;
            IF baseline_value IS NULL THEN
                RAISE EXCEPTION 'bootstrap cutoff day has no eligible value under gap policy %', p_gap_policy;
            END IF;

            WITH aligned AS (
                SELECT *
                FROM agri.forecast_aligned_daily_series(
                    p_series_id,
                    p_release_set_id,
                    p_as_of_time,
                    effective_history_start,
                    p_cutoff_time + interval '1 day',
                    p_gap_policy
                )
            ),
            lagged AS (
                SELECT
                    bucket_start,
                    metric_value,
                    lag(bucket_start) OVER (ORDER BY bucket_start) AS previous_bucket,
                    lag(metric_value) OVER (ORDER BY bucket_start) AS previous_value
                FROM aligned
                WHERE NOT is_missing
            )
            SELECT count(*)::integer
              INTO available_increment_count
              FROM lagged
             WHERE previous_value IS NOT NULL
               AND bucket_start = previous_bucket + interval '1 day';
            IF available_increment_count < 2 THEN
                RAISE EXCEPTION 'bootstrap requires at least two consecutive daily increments';
            END IF;

            SELECT encode(
                       digest(
                           string_agg(
                               aligned.alignment_checksum,
                               '|'
                               ORDER BY aligned.bucket_start
                           ),
                           'sha256'
                       ),
                       'hex'
                   )
              INTO aligned_history_checksum
              FROM agri.forecast_aligned_daily_series(
                  p_series_id,
                  p_release_set_id,
                  p_as_of_time,
                  effective_history_start,
                  p_cutoff_time + interval '1 day',
                  p_gap_policy
              ) AS aligned;

            computed_parameter_checksum := encode(
                digest(
                    concat_ws(
                        '|',
                        'forecast_daily_bootstrap_v1',
                        p_series_id::text,
                        p_release_set_id::text,
                        release_checksum,
                        series_contract_checksum,
                        aligned_history_checksum,
                        p_as_of_time::text,
                        p_cutoff_time::text,
                        effective_history_start::text,
                        p_horizon_days::text,
                        p_simulation_count::text,
                        p_seed::text,
                        p_gap_policy,
                        coalesce(p_lower_bound::text, ''),
                        coalesce(p_upper_bound::text, ''),
                        'daily_mean',
                        'p10_p50_p90'
                    ),
                    'sha256'
                ),
                'hex'
            );

            RETURN QUERY
            WITH aligned AS (
                SELECT *
                FROM agri.forecast_aligned_daily_series(
                    p_series_id,
                    p_release_set_id,
                    p_as_of_time,
                    effective_history_start,
                    p_cutoff_time + interval '1 day',
                    p_gap_policy
                )
            ),
            lagged AS (
                SELECT
                    bucket_start,
                    metric_value,
                    lag(bucket_start) OVER (ORDER BY bucket_start) AS previous_bucket,
                    lag(metric_value) OVER (ORDER BY bucket_start) AS previous_value
                FROM aligned
                WHERE NOT is_missing
            ),
            increments AS (
                SELECT
                    row_number() OVER (ORDER BY bucket_start)::bigint AS increment_index,
                    metric_value - previous_value AS increment_value
                FROM lagged
                WHERE previous_value IS NOT NULL
                  AND bucket_start = previous_bucket + interval '1 day'
            ),
            sample_hashes AS (
                SELECT
                    simulation_id,
                    generated_horizon,
                    (
                        ('x' || substr(
                            encode(
                                digest(
                                    concat_ws(
                                        '|',
                                        'forecast_bootstrap_sample_v1',
                                        p_seed::text,
                                        p_series_id::text,
                                        p_release_set_id::text,
                                        computed_parameter_checksum,
                                        simulation_id::text,
                                        generated_horizon::text
                                    ),
                                    'sha256'
                                ),
                                'hex'
                            ),
                            1,
                            15
                        ))::bit(60)::bigint
                    ) AS sample_hash
                FROM generate_series(1, p_simulation_count) AS simulation(simulation_id)
                CROSS JOIN generate_series(1, p_horizon_days) AS horizon(generated_horizon)
            ),
            sampled AS (
                SELECT
                    sample_hashes.simulation_id,
                    sample_hashes.generated_horizon,
                    increments.increment_value
                FROM sample_hashes
                INNER JOIN increments
                  ON increments.increment_index =
                     1 + mod(sample_hashes.sample_hash, available_increment_count::bigint)
            ),
            paths AS (
                SELECT
                    sampled.simulation_id,
                    sampled.generated_horizon,
                    baseline_value
                        + sum(sampled.increment_value) OVER (
                            PARTITION BY sampled.simulation_id
                            ORDER BY sampled.generated_horizon
                            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                        ) AS path_value
                FROM sampled
            ),
            bounded AS (
                SELECT
                    paths.generated_horizon,
                    least(
                        coalesce(p_upper_bound, 'Infinity'::double precision),
                        greatest(
                            coalesce(p_lower_bound, '-Infinity'::double precision),
                            paths.path_value
                        )
                    ) AS path_value
                FROM paths
            )
            SELECT
                bounded.generated_horizon,
                p_cutoff_time + make_interval(days => bounded.generated_horizon),
                percentile_cont(0.1) WITHIN GROUP (ORDER BY bounded.path_value),
                percentile_cont(0.5) WITHIN GROUP (ORDER BY bounded.path_value),
                percentile_cont(0.9) WITHIN GROUP (ORDER BY bounded.path_value),
                available_increment_count,
                computed_parameter_checksum
            FROM bounded
            GROUP BY bounded.generated_horizon
            ORDER BY bounded.generated_horizon;
        END
        $$;

        CREATE TABLE agri.forecast_iteration (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            iteration_key varchar(255) NOT NULL UNIQUE,
            series_id uuid NOT NULL REFERENCES agri.forecast_series(id),
            release_set_id uuid NOT NULL REFERENCES agri.release_set(id),
            purpose varchar(32) NOT NULL DEFAULT 'evaluation_only',
            availability_mode varchar(40) NOT NULL,
            method varchar(64) NOT NULL DEFAULT 'daily_increment_bootstrap_v1',
            as_of_time timestamptz NOT NULL,
            cutoff_time timestamptz NOT NULL,
            history_start timestamptz NOT NULL,
            horizon_days integer NOT NULL DEFAULT 30,
            simulation_count integer NOT NULL DEFAULT 1000,
            simulation_seed bigint NOT NULL DEFAULT 0,
            grain interval NOT NULL DEFAULT interval '1 day',
            gap_policy varchar(24) NOT NULL DEFAULT 'strict',
            lower_bound double precision,
            upper_bound double precision,
            input_release_checksum varchar(64) NOT NULL,
            input_license_snapshots jsonb NOT NULL,
            contract_snapshot jsonb NOT NULL,
            contract_checksum varchar(64) NOT NULL,
            history_checksum varchar(64) NOT NULL,
            parameter_checksum varchar(64) NOT NULL,
            training_day_count integer NOT NULL,
            increment_count integer NOT NULL,
            expected_value_count integer NOT NULL,
            receipt_checksum varchar(64),
            status varchar(24) NOT NULL DEFAULT 'staging',
            recorded_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_forecast_iteration_method
                CHECK (method = 'daily_increment_bootstrap_v1'),
            CONSTRAINT ck_forecast_iteration_purpose
                CHECK (purpose = 'evaluation_only'),
            CONSTRAINT ck_forecast_iteration_availability
                CHECK (availability_mode IN (
                    'as_of_pinned_release',
                    'retrospective_pinned_release'
                )),
            CONSTRAINT ck_forecast_iteration_ordered_history
                CHECK (history_start <= cutoff_time AND cutoff_time <= as_of_time),
            CONSTRAINT ck_forecast_iteration_horizon
                CHECK (
                    horizon_days BETWEEN 1 AND 366
                    AND expected_value_count = horizon_days
                    AND grain = interval '1 day'
                ),
            CONSTRAINT ck_forecast_iteration_simulations
                CHECK (simulation_count BETWEEN 100 AND 10000),
            CONSTRAINT ck_forecast_iteration_gap_policy
                CHECK (gap_policy IN ('strict', 'locf')),
            CONSTRAINT ck_forecast_iteration_bounds
                CHECK (
                    (lower_bound IS NULL OR lower_bound::text NOT IN ('NaN', 'Infinity', '-Infinity'))
                    AND (upper_bound IS NULL OR upper_bound::text NOT IN ('NaN', 'Infinity', '-Infinity'))
                    AND (lower_bound IS NULL OR upper_bound IS NULL OR lower_bound <= upper_bound)
                ),
            CONSTRAINT ck_forecast_iteration_counts
                CHECK (training_day_count >= 3 AND increment_count >= 2),
            CONSTRAINT ck_forecast_iteration_checksums CHECK (
                input_release_checksum ~ '^[0-9a-f]{64}$'
                AND contract_checksum ~ '^[0-9a-f]{64}$'
                AND history_checksum ~ '^[0-9a-f]{64}$'
                AND parameter_checksum ~ '^[0-9a-f]{64}$'
            ),
            CONSTRAINT ck_forecast_iteration_snapshots CHECK (
                jsonb_typeof(input_license_snapshots) = 'array'
                AND jsonb_array_length(input_license_snapshots) > 0
                AND jsonb_typeof(contract_snapshot) = 'object'
            ),
            CONSTRAINT ck_forecast_iteration_status
                CHECK (status IN ('staging', 'finalized')),
            CONSTRAINT ck_forecast_iteration_finalized_evidence CHECK (
                status <> 'finalized'
                OR (
                    receipt_checksum ~ '^[0-9a-f]{64}$'
                    AND recorded_at IS NOT NULL
                )
            )
        );

        CREATE INDEX ix_forecast_iteration_series_cutoff
            ON agri.forecast_iteration(series_id, cutoff_time DESC);

        CREATE TABLE agri.forecast_iteration_value (
            id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            iteration_id uuid NOT NULL REFERENCES agri.forecast_iteration(id),
            valid_time timestamptz NOT NULL,
            horizon_step integer NOT NULL,
            low_value double precision NOT NULL,
            median_value double precision NOT NULL,
            high_value double precision NOT NULL,
            increment_count integer NOT NULL,
            parameter_checksum varchar(64) NOT NULL,
            value_checksum varchar(64) GENERATED ALWAYS AS (
                agri.forecast_iteration_value_checksum(
                    valid_time,
                    horizon_step,
                    low_value,
                    median_value,
                    high_value,
                    increment_count,
                    parameter_checksum
                )
            ) STORED,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_forecast_iteration_value_time
                UNIQUE(iteration_id, valid_time),
            CONSTRAINT uq_forecast_iteration_value_horizon
                UNIQUE(iteration_id, horizon_step),
            CONSTRAINT ck_forecast_iteration_value_horizon
                CHECK (horizon_step > 0),
            CONSTRAINT ck_forecast_iteration_value_ordered
                CHECK (low_value <= median_value AND median_value <= high_value),
            CONSTRAINT ck_forecast_iteration_value_increment_count
                CHECK (increment_count >= 2),
            CONSTRAINT ck_forecast_iteration_value_parameter_checksum
                CHECK (parameter_checksum ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_forecast_iteration_value_finite CHECK (
                low_value::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND median_value::text NOT IN ('NaN', 'Infinity', '-Infinity')
                AND high_value::text NOT IN ('NaN', 'Infinity', '-Infinity')
            )
        );

        CREATE INDEX ix_forecast_iteration_value_iteration_time
            ON agri.forecast_iteration_value(iteration_id, valid_time);

        CREATE TABLE agri.forecast_iteration_actual (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            iteration_value_id bigint NOT NULL UNIQUE
                REFERENCES agri.forecast_iteration_value(id),
            actual_release_set_id uuid NOT NULL REFERENCES agri.release_set(id),
            actual_input_release_checksum varchar(64) NOT NULL,
            actual_value double precision NOT NULL,
            source_sample_count integer NOT NULL,
            data_available_at timestamptz NOT NULL,
            actual_digest_version varchar(40) NOT NULL
                DEFAULT 'forecast_iteration_actual_v2',
            actual_checksum varchar(64) NOT NULL,
            recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_forecast_iteration_actual_samples
                CHECK (source_sample_count > 0),
            CONSTRAINT ck_forecast_iteration_actual_digest_version
                CHECK (actual_digest_version = 'forecast_iteration_actual_v2'),
            CONSTRAINT ck_forecast_iteration_actual_checksum
                CHECK (
                    actual_input_release_checksum ~ '^[0-9a-f]{64}$'
                    AND actual_checksum ~ '^[0-9a-f]{64}$'
                ),
            CONSTRAINT ck_forecast_iteration_actual_finite
                CHECK (actual_value::text NOT IN ('NaN', 'Infinity', '-Infinity')),
            CONSTRAINT ck_forecast_iteration_actual_availability
                CHECK (recorded_at >= data_available_at)
        );

        CREATE TABLE agri.forecast_iteration_actual_input (
            actual_id uuid NOT NULL REFERENCES agri.forecast_iteration_actual(id),
            source_release_id uuid NOT NULL REFERENCES agri.source_release(id),
            observation_checksum varchar(64) NOT NULL,
            license_snapshot text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY(actual_id, source_release_id, observation_checksum),
            CONSTRAINT ck_forecast_iteration_actual_input_checksum
                CHECK (
                    observation_checksum ~ '^[0-9a-f]{64}$'
                    AND btrim(license_snapshot) <> ''
                )
        );

        CREATE FUNCTION agri.forecast_iteration_receipt_checksum(p_iteration_id uuid)
        RETURNS varchar
        LANGUAGE sql
        STABLE
        SET timezone = 'UTC'
        SET datestyle = 'ISO, MDY'
        SET intervalstyle = 'postgres'
        SET extra_float_digits = 1
        AS $$
            SELECT encode(
                digest(
                    concat_ws(
                        '|',
                        'forecast_iteration_receipt_v1',
                        iteration.iteration_key,
                        iteration.series_id::text,
                        iteration.release_set_id::text,
                        iteration.purpose,
                        iteration.availability_mode,
                        iteration.method,
                        iteration.as_of_time::text,
                        iteration.cutoff_time::text,
                        iteration.history_start::text,
                        iteration.horizon_days::text,
                        iteration.simulation_count::text,
                        iteration.simulation_seed::text,
                        iteration.grain::text,
                        iteration.gap_policy,
                        coalesce(iteration.lower_bound::text, ''),
                        coalesce(iteration.upper_bound::text, ''),
                        iteration.input_release_checksum,
                        iteration.input_license_snapshots::text,
                        iteration.contract_snapshot::text,
                        iteration.contract_checksum,
                        iteration.history_checksum,
                        iteration.parameter_checksum,
                        iteration.training_day_count::text,
                        iteration.increment_count::text,
                        string_agg(value.value_checksum, '|' ORDER BY value.horizon_step)
                    ),
                    'sha256'
                ),
                'hex'
            )::varchar
            FROM agri.forecast_iteration AS iteration
            INNER JOIN agri.forecast_iteration_value AS value
                ON value.iteration_id = iteration.id
            WHERE iteration.id = p_iteration_id
            GROUP BY iteration.id
        $$;

        CREATE FUNCTION agri.guard_forecast_iteration_change()
        RETURNS trigger
        LANGUAGE plpgsql
        SET timezone = 'UTC'
        SET datestyle = 'ISO, MDY'
        SET intervalstyle = 'postgres'
        SET extra_float_digits = 1
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'forecast iteration evidence is append-only';
            END IF;
            IF OLD.status = 'finalized' THEN
                RAISE EXCEPTION 'finalized forecast iterations are immutable';
            END IF;
            IF ROW(
                NEW.id,
                NEW.iteration_key,
                NEW.series_id,
                NEW.release_set_id,
                NEW.purpose,
                NEW.availability_mode,
                NEW.method,
                NEW.as_of_time,
                NEW.cutoff_time,
                NEW.history_start,
                NEW.horizon_days,
                NEW.simulation_count,
                NEW.simulation_seed,
                NEW.grain,
                NEW.gap_policy,
                NEW.lower_bound,
                NEW.upper_bound,
                NEW.input_release_checksum,
                NEW.input_license_snapshots,
                NEW.contract_snapshot,
                NEW.contract_checksum,
                NEW.history_checksum,
                NEW.parameter_checksum,
                NEW.training_day_count,
                NEW.increment_count,
                NEW.expected_value_count,
                NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.id,
                OLD.iteration_key,
                OLD.series_id,
                OLD.release_set_id,
                OLD.purpose,
                OLD.availability_mode,
                OLD.method,
                OLD.as_of_time,
                OLD.cutoff_time,
                OLD.history_start,
                OLD.horizon_days,
                OLD.simulation_count,
                OLD.simulation_seed,
                OLD.grain,
                OLD.gap_policy,
                OLD.lower_bound,
                OLD.upper_bound,
                OLD.input_release_checksum,
                OLD.input_license_snapshots,
                OLD.contract_snapshot,
                OLD.contract_checksum,
                OLD.history_checksum,
                OLD.parameter_checksum,
                OLD.training_day_count,
                OLD.increment_count,
                OLD.expected_value_count,
                OLD.created_at
            )
               OR NEW.status <> 'finalized'
               OR OLD.status <> 'staging'
               OR NEW.receipt_checksum !~ '^[0-9a-f]{64}$'
               OR NEW.recorded_at IS NULL THEN
                RAISE EXCEPTION 'only verified staging-to-finalized iteration transition is allowed';
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER forecast_iteration_change_guard
            BEFORE UPDATE OR DELETE ON agri.forecast_iteration
            FOR EACH ROW EXECUTE FUNCTION agri.guard_forecast_iteration_change();

        CREATE FUNCTION agri.guard_forecast_iteration_value_write()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            parent agri.forecast_iteration;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'forecast iteration values are append-only';
            END IF;
            SELECT *
              INTO parent
              FROM agri.forecast_iteration
             WHERE id = NEW.iteration_id
             FOR SHARE;
            IF parent.status <> 'staging' THEN
                RAISE EXCEPTION 'forecast iteration values can only be added while staging';
            END IF;
            IF NEW.horizon_step > parent.horizon_days
               OR NEW.valid_time <> parent.cutoff_time + make_interval(days => NEW.horizon_step)
               OR NEW.increment_count <> parent.increment_count
               OR NEW.parameter_checksum <> parent.parameter_checksum THEN
                RAISE EXCEPTION 'forecast iteration value does not match its parent contract';
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER forecast_iteration_value_write_guard
            BEFORE INSERT OR UPDATE OR DELETE ON agri.forecast_iteration_value
            FOR EACH ROW EXECUTE FUNCTION agri.guard_forecast_iteration_value_write();

        CREATE FUNCTION agri.verify_forecast_iteration_finalization()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            value_count integer;
            minimum_horizon integer;
            maximum_horizon integer;
            expected_checksum varchar;
        BEGIN
            IF NEW.status = 'finalized' AND OLD.status <> 'finalized' THEN
                SELECT count(*), min(horizon_step), max(horizon_step)
                  INTO value_count, minimum_horizon, maximum_horizon
                  FROM agri.forecast_iteration_value
                 WHERE iteration_id = NEW.id;
                IF value_count <> NEW.expected_value_count
                   OR minimum_horizon <> 1
                   OR maximum_horizon <> NEW.horizon_days THEN
                    RAISE EXCEPTION 'forecast iteration value extent is incomplete';
                END IF;
                expected_checksum := agri.forecast_iteration_receipt_checksum(NEW.id);
                IF expected_checksum IS DISTINCT FROM NEW.receipt_checksum THEN
                    RAISE EXCEPTION 'forecast iteration receipt checksum mismatch';
                END IF;
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER forecast_iteration_finalized_verify
            AFTER UPDATE OF status ON agri.forecast_iteration
            FOR EACH ROW EXECUTE FUNCTION agri.verify_forecast_iteration_finalization();

        CREATE FUNCTION agri.guard_forecast_iteration_actual_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'forecast iteration actual evidence is append-only';
            END IF;
            IF TG_TABLE_NAME = 'forecast_iteration_actual'
               AND NEW.actual_digest_version <> 'forecast_iteration_actual_v2' THEN
                RAISE EXCEPTION 'new forecast iteration actuals require digest version v2';
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE TRIGGER forecast_iteration_actual_change_guard
            BEFORE UPDATE OR DELETE ON agri.forecast_iteration_actual
            FOR EACH ROW EXECUTE FUNCTION agri.guard_forecast_iteration_actual_change();
        CREATE TRIGGER forecast_iteration_actual_input_change_guard
            BEFORE UPDATE OR DELETE ON agri.forecast_iteration_actual_input
            FOR EACH ROW EXECUTE FUNCTION agri.guard_forecast_iteration_actual_change();

        CREATE FUNCTION agri.verify_forecast_iteration_actual_lineage()
        RETURNS trigger
        LANGUAGE plpgsql
        SET timezone = 'UTC'
        SET datestyle = 'ISO, MDY'
        SET intervalstyle = 'postgres'
        SET extra_float_digits = 1
        AS $$
        DECLARE
            target_actual_id uuid;
            target agri.forecast_iteration_actual;
            target_value agri.forecast_iteration_value;
            parent agri.forecast_iteration;
            expected_value double precision;
            expected_sample_count integer;
            expected_available_at timestamptz;
            expected_inputs text;
            stored_inputs text;
            expected_checksum text;
            expected_release_checksum text;
        BEGIN
            IF TG_TABLE_NAME = 'forecast_iteration_actual' THEN
                target_actual_id := NEW.id;
            ELSE
                target_actual_id := NEW.actual_id;
            END IF;
            SELECT * INTO target
              FROM agri.forecast_iteration_actual
             WHERE id = target_actual_id;
            SELECT * INTO target_value
              FROM agri.forecast_iteration_value
             WHERE id = target.iteration_value_id;
            SELECT * INTO parent
              FROM agri.forecast_iteration
             WHERE id = target_value.iteration_id;
            SELECT release_set.manifest_checksum
              INTO expected_release_checksum
              FROM agri.release_set AS release_set
             WHERE release_set.id = target.actual_release_set_id
               AND release_set.state IN ('validated', 'published')
               AND release_set.validated_at <= target.data_available_at
               AND release_set.as_of_time <= target.data_available_at;

            SELECT
                avg(contract.metric_value),
                count(*)::integer,
                max(contract.data_available_at),
                string_agg(
                    DISTINCT concat_ws(
                        ':',
                        contract.source_release_id::text,
                        contract.observation_checksum,
                        contract.source_release_license_snapshot
                    ),
                    '|'
                    ORDER BY concat_ws(
                        ':',
                        contract.source_release_id::text,
                        contract.observation_checksum,
                        contract.source_release_license_snapshot
                    )
                )
              INTO
                expected_value,
                expected_sample_count,
                expected_available_at,
                expected_inputs
              FROM agri.forecast_timeseries_contract(
                  target.actual_release_set_id,
                  target.data_available_at
              ) AS contract
             WHERE contract.series_id = parent.series_id
               AND contract.observed_at >= target_value.valid_time
               AND contract.observed_at < target_value.valid_time + interval '1 day';

            SELECT string_agg(
                       concat_ws(
                           ':',
                           input.source_release_id::text,
                           input.observation_checksum,
                           input.license_snapshot
                       ),
                       '|'
                       ORDER BY input.source_release_id, input.observation_checksum
                   )
              INTO stored_inputs
              FROM agri.forecast_iteration_actual_input AS input
             WHERE input.actual_id = target.id;

            expected_checksum := encode(
                digest(
                    concat_ws(
                        '|',
                        'forecast_iteration_actual_v2',
                        parent.iteration_key,
                        parent.parameter_checksum,
                        target_value.horizon_step::text,
                        target_value.valid_time::text,
                        target.actual_release_set_id::text,
                        expected_release_checksum,
                        expected_value::text,
                        expected_sample_count::text,
                        expected_available_at::text,
                        expected_inputs
                    ),
                    'sha256'
                ),
                'hex'
            );

            IF target.actual_digest_version <> 'forecast_iteration_actual_v2'
               OR parent.status <> 'finalized'
               OR target.recorded_at < parent.recorded_at
               OR expected_release_checksum IS NULL
               OR target.actual_input_release_checksum <> expected_release_checksum
               OR expected_value IS NULL
               OR target.actual_value IS DISTINCT FROM expected_value
               OR target.source_sample_count <> expected_sample_count
               OR target.data_available_at <> expected_available_at
               OR target.actual_checksum <> expected_checksum
               OR stored_inputs IS DISTINCT FROM expected_inputs THEN
                RAISE EXCEPTION 'forecast iteration actual lineage does not match governed inputs';
            END IF;
            RETURN NEW;
        END
        $$;

        CREATE CONSTRAINT TRIGGER forecast_iteration_actual_lineage_verify
            AFTER INSERT ON agri.forecast_iteration_actual
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION agri.verify_forecast_iteration_actual_lineage();
        CREATE CONSTRAINT TRIGGER forecast_iteration_actual_input_lineage_verify
            AFTER INSERT ON agri.forecast_iteration_actual_input
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION agri.verify_forecast_iteration_actual_lineage();

        CREATE PROCEDURE agri.materialize_forecast_iteration(
            INOUT p_iteration_id uuid,
            IN p_iteration_key varchar,
            IN p_series_id uuid,
            IN p_release_set_id uuid,
            IN p_as_of_time timestamptz,
            IN p_cutoff_time timestamptz,
            IN p_history_start timestamptz DEFAULT NULL,
            IN p_horizon_days integer DEFAULT 30,
            IN p_simulation_count integer DEFAULT 1000,
            IN p_seed bigint DEFAULT 0,
            IN p_gap_policy varchar DEFAULT 'strict',
            IN p_lower_bound double precision DEFAULT NULL,
            IN p_upper_bound double precision DEFAULT NULL
        )
        LANGUAGE plpgsql
        SET timezone = 'UTC'
        SET datestyle = 'ISO, MDY'
        SET intervalstyle = 'postgres'
        SET extra_float_digits = 1
        AS $$
        DECLARE
            existing agri.forecast_iteration;
            effective_history_start timestamptz;
            release_checksum varchar;
            series_contract_checksum varchar;
            series_contract_snapshot jsonb;
            computed_license_snapshots jsonb;
            licenses_match_approved_contract boolean;
            computed_history_checksum varchar;
            computed_parameter_checksum varchar;
            computed_increment_count integer;
            computed_training_day_count integer;
            computed_value_count integer;
            computed_receipt_checksum varchar;
        BEGIN
            IF p_iteration_key IS NULL OR btrim(p_iteration_key) = '' THEN
                RAISE EXCEPTION 'forecast iteration key is required';
            END IF;
            IF p_as_of_time > clock_timestamp() THEN
                RAISE EXCEPTION 'forecast iteration as-of boundary cannot be in the future';
            END IF;

            PERFORM pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'forecast_iteration|' || p_iteration_key,
                    0
                )
            );
            SELECT *
              INTO existing
              FROM agri.forecast_iteration
             WHERE iteration_key = p_iteration_key
             FOR SHARE;

            SELECT
                release_set.manifest_checksum,
                contract.contract_checksum,
                contract.contract_snapshot
              INTO
                release_checksum,
                series_contract_checksum,
                series_contract_snapshot
              FROM agri.release_set AS release_set
              CROSS JOIN agri.v_forecast_timeseries_contract AS contract
             WHERE release_set.id = p_release_set_id
               AND contract.series_id = p_series_id
               AND contract.data_source_review_state = 'approved'
               AND btrim(contract.license_name) <> ''
               AND btrim(coalesce(contract.license_url, '')) <> ''
               AND btrim(contract.citation) <> ''
               AND release_set.state IN ('validated', 'published')
               AND release_set.validated_at <= p_as_of_time
               AND release_set.as_of_time <= p_as_of_time
               AND release_set.created_at <= p_as_of_time
               AND contract.data_source_recorded_at::timestamptz <= p_as_of_time
               AND contract.contract_recorded_at::timestamptz <= p_as_of_time;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'forecast iteration requires approved free/open source governance at the as-of boundary';
            END IF;

            SELECT coalesce(
                       p_history_start,
                       min(
                           date_trunc('day', contract.observed_at AT TIME ZONE 'UTC')
                               AT TIME ZONE 'UTC'
                       )
                   )
              INTO effective_history_start
              FROM agri.forecast_timeseries_contract(
                  p_release_set_id,
                  p_as_of_time
              ) AS contract
             WHERE contract.series_id = p_series_id
               AND contract.observed_at < p_cutoff_time + interval '1 day';
            IF effective_history_start IS NULL THEN
                RAISE EXCEPTION 'forecast iteration has no time-honest observations';
            END IF;

            SELECT
                jsonb_agg(
                    jsonb_build_object(
                        'source_release_id',
                        governed.source_release_id,
                        'license_snapshot',
                        governed.source_release_license_snapshot
                    )
                    ORDER BY governed.source_release_id
                ),
                bool_and(
                    btrim(governed.source_release_license_snapshot) <> ''
                    AND governed.source_release_license_snapshot = governed.license_name
                )
              INTO computed_license_snapshots, licenses_match_approved_contract
              FROM (
                    SELECT DISTINCT
                        contract.source_release_id,
                        contract.source_release_license_snapshot,
                        contract.license_name
                    FROM agri.forecast_timeseries_contract(
                        p_release_set_id,
                        p_as_of_time
                    ) AS contract
                    WHERE contract.series_id = p_series_id
                      AND contract.observed_at >= effective_history_start
                      AND contract.observed_at < p_cutoff_time + interval '1 day'
              ) AS governed;
            IF computed_license_snapshots IS NULL
               OR licenses_match_approved_contract IS DISTINCT FROM true THEN
                RAISE EXCEPTION
                    'forecast iteration source-release license snapshots do not match the approved contract';
            END IF;

            SELECT
                count(*)::integer,
                min(bootstrap.increment_count),
                min(bootstrap.parameter_checksum)
              INTO
                computed_value_count,
                computed_increment_count,
                computed_parameter_checksum
              FROM agri.forecast_daily_bootstrap(
                  p_series_id,
                  p_release_set_id,
                  p_as_of_time,
                  p_cutoff_time,
                  effective_history_start,
                  p_horizon_days,
                  p_simulation_count,
                  p_seed,
                  p_gap_policy,
                  p_lower_bound,
                  p_upper_bound
              ) AS bootstrap;
            IF computed_value_count <> p_horizon_days THEN
                RAISE EXCEPTION 'bootstrap did not return its declared horizon';
            END IF;

            SELECT count(*)::integer
              INTO computed_training_day_count
              FROM agri.forecast_aligned_daily_series(
                  p_series_id,
                  p_release_set_id,
                  p_as_of_time,
                  effective_history_start,
                  p_cutoff_time + interval '1 day',
                  p_gap_policy
              ) AS aligned
             WHERE NOT aligned.is_missing;

            SELECT encode(
                       digest(
                           string_agg(
                               aligned.alignment_checksum,
                               '|'
                               ORDER BY aligned.bucket_start
                           ),
                           'sha256'
                       ),
                       'hex'
                   )
              INTO computed_history_checksum
              FROM agri.forecast_aligned_daily_series(
                  p_series_id,
                  p_release_set_id,
                  p_as_of_time,
                  effective_history_start,
                  p_cutoff_time + interval '1 day',
                  p_gap_policy
              ) AS aligned;

            IF existing.id IS NOT NULL THEN
                IF existing.status <> 'finalized'
                   OR (p_iteration_id IS NOT NULL AND p_iteration_id <> existing.id)
                   OR existing.series_id <> p_series_id
                   OR existing.release_set_id <> p_release_set_id
                   OR existing.as_of_time <> p_as_of_time
                   OR existing.cutoff_time <> p_cutoff_time
                   OR existing.history_start <> effective_history_start
                   OR existing.horizon_days <> p_horizon_days
                   OR existing.simulation_count <> p_simulation_count
                   OR existing.simulation_seed <> p_seed
                   OR existing.gap_policy <> p_gap_policy
                   OR existing.lower_bound IS DISTINCT FROM p_lower_bound
                   OR existing.upper_bound IS DISTINCT FROM p_upper_bound
                   OR existing.input_release_checksum <> release_checksum
                   OR existing.input_license_snapshots IS DISTINCT FROM computed_license_snapshots
                   OR existing.contract_snapshot IS DISTINCT FROM series_contract_snapshot
                   OR existing.contract_checksum <> series_contract_checksum
                   OR existing.history_checksum <> computed_history_checksum
                   OR existing.parameter_checksum <> computed_parameter_checksum
                   OR existing.training_day_count <> computed_training_day_count
                   OR existing.increment_count <> computed_increment_count THEN
                    RAISE EXCEPTION
                        'forecast iteration key already has different immutable parameters or evidence';
                END IF;
                p_iteration_id := existing.id;
                RETURN;
            END IF;

            p_iteration_id := coalesce(p_iteration_id, gen_random_uuid());
            INSERT INTO agri.forecast_iteration(
                id,
                iteration_key,
                series_id,
                release_set_id,
                availability_mode,
                as_of_time,
                cutoff_time,
                history_start,
                horizon_days,
                simulation_count,
                simulation_seed,
                gap_policy,
                lower_bound,
                upper_bound,
                input_release_checksum,
                input_license_snapshots,
                contract_snapshot,
                contract_checksum,
                history_checksum,
                parameter_checksum,
                training_day_count,
                increment_count,
                expected_value_count
            )
            VALUES (
                p_iteration_id,
                p_iteration_key,
                p_series_id,
                p_release_set_id,
                CASE
                    WHEN p_as_of_time > p_cutoff_time + interval '1 day'
                        THEN 'retrospective_pinned_release'
                    ELSE 'as_of_pinned_release'
                END,
                p_as_of_time,
                p_cutoff_time,
                effective_history_start,
                p_horizon_days,
                p_simulation_count,
                p_seed,
                p_gap_policy,
                p_lower_bound,
                p_upper_bound,
                release_checksum,
                computed_license_snapshots,
                series_contract_snapshot,
                series_contract_checksum,
                computed_history_checksum,
                computed_parameter_checksum,
                computed_training_day_count,
                computed_increment_count,
                p_horizon_days
            );

            INSERT INTO agri.forecast_iteration_value(
                iteration_id,
                valid_time,
                horizon_step,
                low_value,
                median_value,
                high_value,
                increment_count,
                parameter_checksum
            )
            SELECT
                p_iteration_id,
                bootstrap.valid_time,
                bootstrap.horizon_step,
                bootstrap.low_value,
                bootstrap.median_value,
                bootstrap.high_value,
                bootstrap.increment_count,
                bootstrap.parameter_checksum
            FROM agri.forecast_daily_bootstrap(
                p_series_id,
                p_release_set_id,
                p_as_of_time,
                p_cutoff_time,
                effective_history_start,
                p_horizon_days,
                p_simulation_count,
                p_seed,
                p_gap_policy,
                p_lower_bound,
                p_upper_bound
            ) AS bootstrap;

            computed_receipt_checksum :=
                agri.forecast_iteration_receipt_checksum(p_iteration_id);
            UPDATE agri.forecast_iteration
               SET
                   receipt_checksum = computed_receipt_checksum,
                   recorded_at = clock_timestamp(),
                   status = 'finalized'
             WHERE id = p_iteration_id;
        END
        $$;

        CREATE PROCEDURE agri.reconcile_forecast_iteration_actuals(
            INOUT p_inserted_count integer,
            IN p_iteration_id uuid,
            IN p_actual_release_set_id uuid,
            IN p_as_of_time timestamptz
        )
        LANGUAGE plpgsql
        SET timezone = 'UTC'
        SET datestyle = 'ISO, MDY'
        SET intervalstyle = 'postgres'
        SET extra_float_digits = 1
        AS $$
        DECLARE
            parent agri.forecast_iteration;
            actual_release_checksum varchar;
        BEGIN
            PERFORM pg_advisory_xact_lock(
                pg_catalog.hashtextextended(
                    'forecast_iteration_actual|' || p_iteration_id::text,
                    0
                )
            );
            SELECT *
              INTO parent
              FROM agri.forecast_iteration
             WHERE id = p_iteration_id
             FOR SHARE;
            IF NOT FOUND OR parent.status <> 'finalized' THEN
                RAISE EXCEPTION 'actual reconciliation requires a finalized forecast iteration';
            END IF;
            IF p_as_of_time < parent.as_of_time THEN
                RAISE EXCEPTION 'actual reconciliation as-of cannot precede the forecast as-of';
            END IF;
            IF p_as_of_time > clock_timestamp() THEN
                RAISE EXCEPTION 'actual reconciliation as-of boundary cannot be in the future';
            END IF;
            SELECT release_set.manifest_checksum
              INTO actual_release_checksum
              FROM agri.release_set AS release_set
             WHERE release_set.id = p_actual_release_set_id
               AND release_set.state IN ('validated', 'published')
               AND release_set.validated_at <= p_as_of_time
               AND release_set.as_of_time <= p_as_of_time;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'actual release set is not governed at the reconciliation as-of';
            END IF;

            WITH candidates AS (
                SELECT
                    gen_random_uuid() AS actual_id,
                    value.id AS iteration_value_id,
                    value.horizon_step,
                    value.valid_time,
                    avg(contract.metric_value) AS actual_value,
                    count(*)::integer AS source_sample_count,
                    max(contract.data_available_at) AS data_available_at,
                    string_agg(
                        DISTINCT concat_ws(
                            ':',
                            contract.source_release_id::text,
                            contract.observation_checksum,
                            contract.source_release_license_snapshot
                        ),
                        '|'
                        ORDER BY concat_ws(
                            ':',
                            contract.source_release_id::text,
                            contract.observation_checksum,
                            contract.source_release_license_snapshot
                        )
                    ) AS source_inputs
                FROM agri.forecast_iteration_value AS value
                INNER JOIN LATERAL agri.forecast_timeseries_contract(
                    p_actual_release_set_id,
                    p_as_of_time
                ) AS contract
                  ON contract.series_id = parent.series_id
                 AND contract.observed_at >= value.valid_time
                 AND contract.observed_at < value.valid_time + interval '1 day'
                LEFT JOIN agri.forecast_iteration_actual AS actual
                  ON actual.iteration_value_id = value.id
                WHERE value.iteration_id = parent.id
                  AND value.valid_time + interval '1 day' <= p_as_of_time
                  AND actual.id IS NULL
                GROUP BY value.id
                HAVING count(DISTINCT contract.observed_at) >= greatest(
                    1,
                    ceil(
                        86400.0
                        / extract(epoch FROM min(contract.source_temporal_support))
                    )::integer
                )
                AND bool_and(
                    btrim(contract.source_release_license_snapshot) <> ''
                    AND contract.source_release_license_snapshot = contract.license_name
                    AND contract.data_source_review_state = 'approved'
                    AND btrim(coalesce(contract.license_url, '')) <> ''
                    AND btrim(contract.citation) <> ''
                )
            ),
            inserted AS (
                INSERT INTO agri.forecast_iteration_actual(
                    id,
                    iteration_value_id,
                    actual_release_set_id,
                    actual_input_release_checksum,
                    actual_value,
                    source_sample_count,
                    data_available_at,
                    actual_digest_version,
                    actual_checksum
                )
                SELECT
                    candidates.actual_id,
                    candidates.iteration_value_id,
                    p_actual_release_set_id,
                    actual_release_checksum,
                    candidates.actual_value,
                    candidates.source_sample_count,
                    candidates.data_available_at,
                    'forecast_iteration_actual_v2',
                    encode(
                        digest(
                            concat_ws(
                                '|',
                                'forecast_iteration_actual_v2',
                                parent.iteration_key,
                                parent.parameter_checksum,
                                candidates.horizon_step::text,
                                candidates.valid_time::text,
                                p_actual_release_set_id::text,
                                actual_release_checksum,
                                candidates.actual_value::text,
                                candidates.source_sample_count::text,
                                candidates.data_available_at::text,
                                candidates.source_inputs
                            ),
                            'sha256'
                        ),
                        'hex'
                    )
                FROM candidates
                RETURNING id, iteration_value_id
            )
            SELECT count(*)::integer
              INTO p_inserted_count
              FROM inserted;

            INSERT INTO agri.forecast_iteration_actual_input(
                actual_id,
                source_release_id,
                observation_checksum,
                license_snapshot
            )
            SELECT DISTINCT
                actual.id,
                contract.source_release_id,
                contract.observation_checksum,
                contract.source_release_license_snapshot
            FROM agri.forecast_iteration_actual AS actual
            INNER JOIN agri.forecast_iteration_value AS value
                ON value.id = actual.iteration_value_id
            INNER JOIN LATERAL agri.forecast_timeseries_contract(
                actual.actual_release_set_id,
                actual.data_available_at
            ) AS contract
              ON contract.series_id = parent.series_id
             AND contract.observed_at >= value.valid_time
             AND contract.observed_at < value.valid_time + interval '1 day'
            WHERE value.iteration_id = parent.id
            ON CONFLICT DO NOTHING;
        END
        $$;

        CREATE VIEW agri.v_forecast_iteration_outcome AS
        SELECT
            iteration.id AS iteration_id,
            iteration.iteration_key,
            iteration.purpose,
            iteration.availability_mode,
            iteration.method,
            iteration.series_id,
            series.series_key,
            series.entity_type,
            series.entity_key,
            series.metric_name,
            series.metric_unit,
            series.spatial_cell_id,
            series.spatial_support_kind,
            series.source_spatial_resolution_m,
            series.output_spatial_resolution_m,
            iteration.release_set_id,
            iteration.input_release_checksum,
            iteration.contract_checksum,
            iteration.history_checksum,
            iteration.parameter_checksum,
            iteration.as_of_time,
            iteration.cutoff_time,
            iteration.history_start,
            iteration.horizon_days,
            iteration.simulation_count,
            iteration.simulation_seed,
            iteration.gap_policy,
            iteration.lower_bound,
            iteration.upper_bound,
            iteration.recorded_at AS forecast_available_at,
            iteration.receipt_checksum,
            value.valid_time,
            value.horizon_step,
            value.low_value,
            value.median_value,
            value.high_value,
            actual.actual_value,
            actual.actual_release_set_id,
            actual.actual_input_release_checksum,
            actual.actual_digest_version,
            actual.data_available_at AS actual_data_available_at,
            actual.recorded_at AS actual_recorded_at,
            actual.actual_value - value.median_value AS residual_actual_minus_forecast,
            value.median_value - actual.actual_value AS forecast_error,
            abs(actual.actual_value - value.median_value) AS absolute_error,
            (actual.actual_value - value.median_value)
                * (actual.actual_value - value.median_value) AS squared_error,
            actual.actual_value >= value.low_value
                AND actual.actual_value <= value.high_value AS interval_covered,
            value.value_checksum,
            actual.actual_checksum,
            'forecast_iteration_signal_v1'::text AS signal_contract_version
        FROM agri.forecast_iteration AS iteration
        INNER JOIN agri.forecast_iteration_value AS value
            ON value.iteration_id = iteration.id
        INNER JOIN agri.forecast_series AS series
            ON series.id = iteration.series_id
        LEFT JOIN agri.forecast_iteration_actual AS actual
            ON actual.iteration_value_id = value.id
        WHERE iteration.status = 'finalized';

        CREATE FUNCTION agri.forecast_iteration_signal_timeseries(
            p_series_id uuid,
            p_signal_kind varchar,
            p_horizon_step integer,
            p_as_of_time timestamptz
        )
        RETURNS TABLE(
            observed_at timestamptz,
            metric_value double precision,
            signal_kind text,
            iteration_id uuid,
            cutoff_time timestamptz,
            horizon_step integer,
            signal_available_at timestamptz,
            receipt_checksum text,
            value_checksum text,
            actual_checksum text
        )
        LANGUAGE sql
        STABLE
        AS $$
            SELECT
                outcome.valid_time,
                CASE p_signal_kind
                    WHEN 'forecast_low' THEN outcome.low_value
                    WHEN 'forecast_median' THEN outcome.median_value
                    WHEN 'forecast_high' THEN outcome.high_value
                    WHEN 'actual' THEN outcome.actual_value
                    WHEN 'residual_actual_minus_forecast' THEN
                        outcome.residual_actual_minus_forecast
                    WHEN 'forecast_error' THEN outcome.forecast_error
                    WHEN 'absolute_error' THEN outcome.absolute_error
                    WHEN 'squared_error' THEN outcome.squared_error
                    WHEN 'interval_covered' THEN
                        CASE WHEN outcome.interval_covered THEN 1.0 ELSE 0.0 END
                END,
                p_signal_kind::text,
                outcome.iteration_id,
                outcome.cutoff_time,
                outcome.horizon_step,
                CASE
                    WHEN p_signal_kind IN (
                        'actual',
                        'residual_actual_minus_forecast',
                        'forecast_error',
                        'absolute_error',
                        'squared_error',
                        'interval_covered'
                    ) THEN outcome.actual_recorded_at
                    ELSE outcome.forecast_available_at
                END,
                outcome.receipt_checksum::text,
                outcome.value_checksum::text,
                outcome.actual_checksum::text
            FROM agri.v_forecast_iteration_outcome AS outcome
            WHERE outcome.series_id = p_series_id
              AND outcome.horizon_step = p_horizon_step
              AND p_signal_kind IN (
                  'forecast_low',
                  'forecast_median',
                  'forecast_high',
                  'actual',
                  'residual_actual_minus_forecast',
                  'forecast_error',
                  'absolute_error',
                  'squared_error',
                  'interval_covered'
              )
              AND CASE
                    WHEN p_signal_kind IN (
                        'actual',
                        'residual_actual_minus_forecast',
                        'forecast_error',
                        'absolute_error',
                        'squared_error',
                        'interval_covered'
                    ) THEN
                        outcome.actual_recorded_at IS NOT NULL
                        AND outcome.actual_recorded_at <= p_as_of_time
                    ELSE outcome.forecast_available_at <= p_as_of_time
                  END
            ORDER BY outcome.valid_time, outcome.iteration_id
        $$;

        REVOKE ALL PRIVILEGES ON TABLE
            agri.forecast_input_recorded_at,
            agri.v_forecast_timeseries_contract,
            agri.forecast_iteration,
            agri.forecast_iteration_value,
            agri.forecast_iteration_actual,
            agri.forecast_iteration_actual_input,
            agri.v_forecast_iteration_outcome
        FROM PUBLIC;
        DO $privileges$
        DECLARE
            role_name text;
        BEGIN
            FOREACH role_name IN ARRAY ARRAY[
                'plantgeo_local_developer',
                'plantgeo_loader',
                'plantgeo_forecast_mv_refresher',
                'plantgeo_forecast_refresh_operator',
                'plantgeo_local_viewer'
            ]
            LOOP
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
                    EXECUTE format(
                        'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON '
                        'agri.forecast_input_recorded_at FROM %I',
                        role_name
                    );
                    EXECUTE format(
                        'GRANT SELECT ON agri.forecast_input_recorded_at TO %I',
                        role_name
                    );
                END IF;
            END LOOP;
        END
        $privileges$;
        REVOKE ALL PRIVILEGES ON SEQUENCE
            agri.forecast_iteration_value_id_seq
        FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.forecast_timeseries_contract(uuid, timestamptz)
        FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.forecast_date_spine(timestamptz, timestamptz, interval)
        FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.forecast_aligned_daily_series(
            uuid, uuid, timestamptz, timestamptz, timestamptz, varchar
        ) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.forecast_iteration_value_checksum(
            timestamptz, integer, double precision, double precision, double precision,
            integer, varchar
        ) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.forecast_daily_bootstrap(
            uuid, uuid, timestamptz, timestamptz, timestamptz, integer, integer,
            bigint, varchar, double precision, double precision
        ) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.forecast_iteration_receipt_checksum(uuid)
        FROM PUBLIC;
        REVOKE EXECUTE ON PROCEDURE agri.materialize_forecast_iteration(
            uuid, varchar, uuid, uuid, timestamptz, timestamptz, timestamptz,
            integer, integer, bigint, varchar, double precision, double precision
        ) FROM PUBLIC;
        REVOKE EXECUTE ON PROCEDURE agri.reconcile_forecast_iteration_actuals(
            integer, uuid, uuid, timestamptz
        ) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.forecast_iteration_signal_timeseries(
            uuid, varchar, integer, timestamptz
        ) FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.record_forecast_input_change() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.record_forecast_release_content_insert()
        FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.record_forecast_release_content_update()
        FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.record_forecast_release_content_delete()
        FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.record_forecast_release_set_item_insert()
        FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.record_forecast_release_set_item_update()
        FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.record_forecast_release_set_item_delete()
        FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.guard_forecast_iteration_change() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.guard_forecast_iteration_value_write() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.verify_forecast_iteration_finalization() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.guard_forecast_iteration_actual_change() FROM PUBLIC;
        REVOKE EXECUTE ON FUNCTION agri.verify_forecast_iteration_actual_lineage() FROM PUBLIC;
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Forecast iterations and realization evidence are append-only; restore a verified backup into a fresh database."
    )
