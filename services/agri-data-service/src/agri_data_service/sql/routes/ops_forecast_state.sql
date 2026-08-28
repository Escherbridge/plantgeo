-- Purpose: one row per STAGE of the forecast/ML pipeline -- how many rows that stage holds, when
--          it last wrote, and how far forward in time it reasons -- so /ops/backfill can show
--          which parts of the plane have ever executed and which have never run at all.
-- Loaded by: agri_data_service.routes.ops
-- Params: none.
--
-- Parameter names would appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, and a colon glued to a
-- word anywhere in this file -- comment or statement -- becomes a bind parameter nobody supplies
-- and the statement fails before it reaches the database. This file binds nothing, and the test
-- test_every_ops_statement_binds_only_the_parameters_its_caller_supplies keeps it that way.
--
-- WHY THIS STATEMENT EXISTS
--
-- Every other panel on this page answers "did the data land". This one answers the question one
-- floor up: given that data landed, has anything been DONE with it. That question had no answer
-- anywhere -- there is no ledger row, no job definition and no lane for the forecast plane, so a
-- dashboard built on agri.job_* is structurally blind to it, and a handoff can claim the ML layer
-- has never executed (or that it has) with nothing on the page able to contradict either claim.
--
-- Measured against production 2026-08-09, the state this panel exists to make unmissable is:
-- exactly one method has ever run (ndvi_seasonal_anomaly_bootstrap_v1), it covers one metric on
-- one entity type, its newest iteration was recorded on 2026-08-05, and every governed table
-- above it -- model registry, training runs, forecast runs, backtest metrics, quality policies,
-- receipts, serving values, publications -- holds zero rows, as does every strategy and
-- intervention table. Nothing is being trained, published, served or scored for skill. That is a
-- real operational finding and not a transient one, and the only way it stops being invisible is
-- if the page counts the rows itself on every read.
--
-- It follows the same rule as its neighbours: reconstruct state from what actually landed, never
-- from what a ledger claims. There is no configuration table saying which stages are "enabled";
-- the count IS the evidence, and a stage with zero rows has demonstrably never executed no matter
-- what any plan, schedule or handoff note says about it.
--
-- WHAT THE FIRST STAGE IS, BECAUSE IT IS EASY TO MISREAD AS EMPTY
--
-- agri.forecast_observation is the GOVERNED FORECAST-INPUT PLANE and it is a different table from
-- agri.signal_observation, which the data-loads panel above reports on. The Sentinel-2 NDVI
-- registration path writes here and never touches signal_observation -- that is deliberate and
-- documented (sql/execution/load_observations.sql, execution/AGENTS.md) -- so its 184,409 rows are
-- invisible to any audit that looks only at the warehouse signal plane. This stage is therefore
-- fed. What it is NOT is scheduled: as of 2026-08-09 the verb that fills it,
-- `agri-service forecast vegetation-register`, is a manual CLI command with no launcher and no entry in
-- jobs/registry.py, while the raw Sentinel-2 ingest beneath it runs on a real schedule -- that
-- half is a claim about CODE and can be armed at any time, unlike the counts below it, which are
-- read live. Measured 2026-08-09 the consequence is visible right
-- here as the two clocks disagreeing with the world: this stage's newest write and its frontier
-- are both several days behind an upstream that is current. A stale first stage above a plane of
-- zeroes is an unarmed promotion step, not a dry upstream, and the panel's job is to show the
-- difference rather than to diagnose it.
--
-- WHY THE STAGE LIST IS A LITERAL AND THE NUMBERS ARE NOT
--
-- The stages are spelled out below as a fixed VALUES list because the pipeline's SHAPE is a
-- design decision -- which tables exist, what order they feed each other in, which plane each one
-- belongs to -- and that shape lives in the migrations, not in the data. Deriving it from the
-- catalog would list every table in the schema in alphabetical order and say nothing about which
-- stage feeds which. What must never be a literal is a COUNT: every number and every timestamp
-- below is a scalar subquery against the real table, so a stage that starts running changes this
-- panel on the next read without anyone editing this file. A new stage table added by a future
-- migration is simply absent from the panel until it is added here -- which is the honest failure
-- mode, since a stage nobody has described has no known place in the pipeline either.
--
-- THE TWO CLOCKS, AND WHY BOTH ARE HERE
--
-- last_activity_at is when a row was WRITTEN. frontier_at is the newest point in time the stage
-- REASONS ABOUT -- an iteration's as-of time, a value's valid time, a backtest's cutoff, a
-- publication's published-at. They are different questions and they diverge in the way that
-- matters: a plane whose newest write is days old is not running, and a plane whose writes are
-- current but whose frontier is stale is running on old inputs. frontier_label travels beside the
-- value because "newest as-of" and "newest valid time" are not comparable quantities and a column
-- header cannot say which one a given row holds. Both are NULL-honest: a stage with no rows has
-- neither, and a stage whose table carries no domain time (the model registry, the quality
-- policies) has no frontier at all rather than a fabricated one.
--
-- WHY THE ACTUALS RATIO IS ON THE PAGE
--
-- A forecast plane with no backtest metric is already unfalsifiable -- nothing has ever compared
-- a prediction to what happened. The one thing that could still ground it is the actuals join:
-- agri.forecast_iteration_actual records what a predicted point turned out to be. Measured
-- 2026-08-09 that table holds 473 rows -- and those 473 rows are 473 distinct predicted VALUES
-- belonging to only 118 distinct iterations, against 1,676 iterations in total. So 7% of
-- iterations have any scored actual at all, not the 28% a straight row-count-over-iteration-count
-- division suggests, and the other 93% are predictions nobody has ever checked. That factor-of-four
-- gap between the two readings is exactly why this column counts parents rather than rows, and it
-- is the reason the ratio is on the page at all: a panel that printed only "473 actuals" would
-- read as evidence of scoring. coverage_label travels with the pair for the same reason
-- frontier_label does -- a bare "118 / 1676" does not say what is being divided by what.
--
-- How this query works, clause by clause:
--
--   WITH pipeline_stage (...) AS (VALUES (...), (...))
--     A CTE ("common table expression") -- a named subquery written up front and referenced below
--     like a table. VALUES builds a literal table from the rows listed inside it, and the names in
--     parentheses after the CTE name are its column names. This is the stage list: one row per
--     stage, in pipeline order.
--
--   (SELECT count(*) FROM agri.forecast_iteration) inside a VALUES row
--     A scalar subquery -- a SELECT that returns exactly one row and one column, so it can be used
--     anywhere a single value can. PostgreSQL allows these inside a VALUES list, which is what lets
--     the stage list stay a readable literal while every number in it is read live from the table
--     it names. Each one is evaluated once per statement, not once per row.
--
--   ::text, ::bigint, ::timestamptz, ::text[] casts in the FIRST row only
--     VALUES infers each column's type from the first row it sees, so a column that starts NULL
--     would otherwise be typed as the "unknown" pseudo-type and fail when a later row puts real
--     text in it. Casting the first row pins every column's type once; later rows inherit it.
--
--   array(SELECT DISTINCT ... ORDER BY 1 LIMIT 8)
--     The ARRAY constructor turns a subquery's rows into a single array value, so one stage row can
--     carry the handful of distinct values that describe how BROAD that stage is -- which methods
--     have run, which entity/metric pairs are registered. DISTINCT collapses duplicates, ORDER BY 1
--     makes the array deterministic between reads (without it the same data could render in a
--     different order each refresh), and LIMIT 8 bounds it: this renders into a table cell, and a
--     stage that one day holds a thousand methods must not put a thousand of them on the page.
--     A truncated list is detectable because the count beside it is the real one.
--
--   count(DISTINCT value.iteration_id) on the actuals row
--     "How many ITERATIONS have at least one scored actual", which is not the same as how many
--     actuals exist: an actual is recorded per predicted value, and one iteration holds many
--     values, so counting actual rows would overstate coverage the moment any iteration is scored
--     more than once. DISTINCT on the parent id is what turns a row count into a coverage count.
--
--   ORDER BY stage_ordinal
--     Pipeline order, and a total one, so the panel's rows never shuffle between refreshes. The
--     ordinal is also what keeps each plane's stages CONTIGUOUS: the route draws one band per
--     run of same-plane rows and heads it with that plane's own executed tally, so this ordering
--     is what makes the boundary between the planes that have run and the planes that never have
--     land in one place on the page. Interleaving the planes here would still render correctly --
--     the route groups runs rather than keys, so a split plane honestly draws as two bands -- but
--     it would scatter the finding.
WITH pipeline_stage (
    stage_ordinal,
    plane,
    stage,
    relation,
    row_count,
    last_activity_at,
    frontier_at,
    frontier_label,
    breadth,
    breadth_label,
    covered_count,
    coverage_of_count,
    coverage_label
) AS (
    VALUES
        (
            1,
            'evidence'::text,
            'observations ingested'::text,
            'agri.forecast_observation'::text,
            (SELECT count(*) FROM agri.forecast_observation)::bigint,
            (SELECT max(created_at) FROM agri.forecast_observation)::timestamptz,
            (SELECT max(observed_at) FROM agri.forecast_observation)::timestamptz,
            'observed through'::text,
            NULL::text[],
            NULL::text,
            NULL::bigint,
            NULL::bigint,
            NULL::text
        ),
        (
            2,
            'evidence',
            'input recorded-at ledger',
            'agri.forecast_input_recorded_at',
            (SELECT count(*) FROM agri.forecast_input_recorded_at),
            (SELECT max(recorded_at) FROM agri.forecast_input_recorded_at),
            NULL,
            NULL,
            array(SELECT DISTINCT input_kind FROM agri.forecast_input_recorded_at ORDER BY 1 LIMIT 8),
            'input kinds',
            NULL,
            NULL,
            NULL
        ),
        (
            3,
            'evidence',
            'series registered',
            'agri.forecast_series',
            (SELECT count(*) FROM agri.forecast_series),
            (SELECT max(created_at) FROM agri.forecast_series),
            NULL,
            NULL,
            array(
                SELECT DISTINCT entity_type || '/' || metric_name
                FROM agri.forecast_series
                ORDER BY 1
                LIMIT 8
            ),
            'entity/metric',
            NULL,
            NULL,
            NULL
        ),
        (
            4,
            'evidence',
            'entity states',
            'agri.forecast_entity_state',
            (SELECT count(*) FROM agri.forecast_entity_state),
            (SELECT max(created_at) FROM agri.forecast_entity_state),
            (SELECT max(valid_to) FROM agri.forecast_entity_state),
            'valid through',
            NULL,
            NULL,
            NULL,
            NULL,
            NULL
        ),
        (
            5,
            'iteration',
            'iterations',
            'agri.forecast_iteration',
            (SELECT count(*) FROM agri.forecast_iteration),
            (SELECT max(recorded_at) FROM agri.forecast_iteration),
            (SELECT max(as_of_time) FROM agri.forecast_iteration),
            'as-of',
            array(SELECT DISTINCT method FROM agri.forecast_iteration ORDER BY 1 LIMIT 8),
            'methods',
            NULL,
            NULL,
            NULL
        ),
        (
            6,
            'iteration',
            'iteration values',
            'agri.forecast_iteration_value',
            (SELECT count(*) FROM agri.forecast_iteration_value),
            (SELECT max(created_at) FROM agri.forecast_iteration_value),
            (SELECT max(valid_time) FROM agri.forecast_iteration_value),
            'valid through',
            NULL,
            NULL,
            NULL,
            NULL,
            NULL
        ),
        (
            7,
            'iteration',
            'actuals scored',
            'agri.forecast_iteration_actual',
            (SELECT count(*) FROM agri.forecast_iteration_actual),
            (SELECT max(recorded_at) FROM agri.forecast_iteration_actual),
            (SELECT max(data_available_at) FROM agri.forecast_iteration_actual),
            'data available through',
            NULL,
            NULL,
            (
                SELECT count(DISTINCT value.iteration_id)
                FROM agri.forecast_iteration_actual AS actual
                INNER JOIN agri.forecast_iteration_value AS value
                    ON value.id = actual.iteration_value_id
            ),
            (SELECT count(*) FROM agri.forecast_iteration),
            'iterations scored'
        ),
        (
            8,
            'governed',
            'feature snapshots',
            'agri.forecast_feature_snapshot',
            (SELECT count(*) FROM agri.forecast_feature_snapshot),
            (SELECT max(created_at) FROM agri.forecast_feature_snapshot),
            (SELECT max(training_window_end) FROM agri.forecast_feature_snapshot),
            'training window end',
            NULL,
            NULL,
            NULL,
            NULL,
            NULL
        ),
        (
            9,
            'governed',
            'model registry',
            'agri.forecast_model',
            (SELECT count(*) FROM agri.forecast_model),
            (SELECT max(created_at) FROM agri.forecast_model),
            NULL,
            NULL,
            array(SELECT DISTINCT model_key FROM agri.forecast_model ORDER BY 1 LIMIT 8),
            'models',
            NULL,
            NULL,
            NULL
        ),
        (
            10,
            'governed',
            'training runs',
            'agri.forecast_training_run',
            (SELECT count(*) FROM agri.forecast_training_run),
            (SELECT max(created_at) FROM agri.forecast_training_run),
            (SELECT max(completed_at) FROM agri.forecast_training_run),
            'completed',
            NULL,
            NULL,
            NULL,
            NULL,
            NULL
        ),
        (
            11,
            'governed',
            'forecast runs',
            'agri.forecast_run',
            (SELECT count(*) FROM agri.forecast_run),
            (SELECT max(created_at) FROM agri.forecast_run),
            (SELECT max(issue_time) FROM agri.forecast_run),
            'issued',
            NULL,
            NULL,
            NULL,
            NULL,
            NULL
        ),
        (
            12,
            'governed',
            'backtest metrics',
            'agri.forecast_backtest_metric',
            (SELECT count(*) FROM agri.forecast_backtest_metric),
            (SELECT max(created_at) FROM agri.forecast_backtest_metric),
            (SELECT max(cutoff_time) FROM agri.forecast_backtest_metric),
            'cutoff',
            NULL,
            NULL,
            NULL,
            NULL,
            NULL
        ),
        (
            13,
            'governed',
            'quality policies',
            'agri.forecast_quality_policy',
            (SELECT count(*) FROM agri.forecast_quality_policy),
            (SELECT max(created_at) FROM agri.forecast_quality_policy),
            NULL,
            NULL,
            NULL,
            NULL,
            NULL,
            NULL,
            NULL
        ),
        (
            14,
            'governed',
            'receipts',
            'agri.forecast_receipt',
            (SELECT count(*) FROM agri.forecast_receipt),
            (SELECT max(created_at) FROM agri.forecast_receipt),
            (SELECT max(issue_time) FROM agri.forecast_receipt),
            'issued',
            NULL,
            NULL,
            NULL,
            NULL,
            NULL
        ),
        (
            15,
            'governed',
            'serving values',
            'agri.forecast_value',
            (SELECT count(*) FROM agri.forecast_value),
            (SELECT max(created_at) FROM agri.forecast_value),
            (SELECT max(valid_time) FROM agri.forecast_value),
            'valid through',
            NULL,
            NULL,
            NULL,
            NULL,
            NULL
        ),
        (
            16,
            'governed',
            'publications',
            'agri.forecast_publication',
            (SELECT count(*) FROM agri.forecast_publication),
            (SELECT max(created_at) FROM agri.forecast_publication),
            (SELECT max(published_at) FROM agri.forecast_publication),
            'published',
            NULL,
            NULL,
            NULL,
            NULL,
            NULL
        ),
        (
            17,
            'recommendation',
            'expert label releases',
            'agri.strategy_label_release',
            (SELECT count(*) FROM agri.strategy_label_release),
            (SELECT max(created_at) FROM agri.strategy_label_release),
            (SELECT max(as_of_time) FROM agri.strategy_label_release),
            'as-of',
            NULL,
            NULL,
            NULL,
            NULL,
            NULL
        ),
        (
            18,
            'recommendation',
            'strategy selections',
            'agri.strategy_selection_receipt',
            (SELECT count(*) FROM agri.strategy_selection_receipt),
            (SELECT max(created_at) FROM agri.strategy_selection_receipt),
            (SELECT max(issue_time) FROM agri.strategy_selection_receipt),
            'issued',
            NULL,
            NULL,
            NULL,
            NULL,
            NULL
        ),
        (
            19,
            'recommendation',
            'intervention analyses',
            'agri.intervention_analysis_run',
            (SELECT count(*) FROM agri.intervention_analysis_run),
            (SELECT max(created_at) FROM agri.intervention_analysis_run),
            (SELECT max(finalized_at) FROM agri.intervention_analysis_run),
            'finalized',
            NULL,
            NULL,
            NULL,
            NULL,
            NULL
        )
)

SELECT
    stage_ordinal,
    plane,
    stage,
    relation,
    row_count,
    last_activity_at,
    frontier_at,
    frontier_label,
    breadth,
    breadth_label,
    covered_count,
    coverage_of_count,
    coverage_label
FROM pipeline_stage
ORDER BY stage_ordinal
