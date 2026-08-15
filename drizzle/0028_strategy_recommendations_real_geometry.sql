-- Stop the strategy-recommendation tiles from fabricating coordinates, and stop them from
-- calling a model number a causal benefit.
--
-- 0027 built all three zoom tiers over this expression:
--
--     37.5 + (random() * 5.0) AS lat,
--     -120.5 + (random() * 5.0) AS lon
--
-- Every recommendation was therefore scattered at a uniformly random point inside a 5x5 degree
-- box over California and Nevada, rounded to a fake "cell", wrapped in a fake envelope, and
-- served to the browser by geo.strategy_recommendations_tiles as a located result. The three
-- views hold zero rows today only because agri.strategy_selection_candidate and agri.strategies
-- are empty; the first model write turns them into a map of invented locations that looks
-- exactly like a real one. Nothing downstream could have caught it -- a random point is a valid
-- point, the envelope is a valid polygon, and the tile encodes without complaint.
--
-- What replaces it is the located geometry the warehouse already publishes. A candidate belongs
-- to a selection receipt, a receipt to an agri.analysis_subject, and an analysis subject carries
-- a real, CHECK-validated WGS84 polygon of the thing that was analysed. The cells that subject
-- occupies are the agri.spatial_cell rows whose real geometry intersects it, and a cell's own
-- cell_key is the cell id. No coordinate anywhere below is computed; every one of them is read.
--
-- The zoom tiers differ by spatial generalisation of that real geometry, following the
-- geo.watershed_rollup precedent in 0023: coarser tiers are the ST_Union of the same real cells
-- bucketed onto a coarser index, simplified for the zoom that draws them. A coarse polygon is
-- exactly the union of the detail cells that compose it. Nothing is dropped going out and
-- nothing is approximated into existence going in.
--
-- The second half of the fix is naming. causal_benefit_tau asserted a causal effect. The causal
-- plane (agri 20260725_0013) has never been populated, every recommendation receipt that could
-- feed this is CHECK-pinned evaluation_only with CHECK (NOT publication_authorized), and no
-- owner has signed the label release behind any of it. A column named for a causal quantity is a
-- claim this evidence chain cannot support, so the served number is renamed to
-- effect_utility_score -- the model's own internal utility, useful for ordering candidates and
-- for nothing else -- and its bounds lose the word "confidence", which they never earned.
--
-- Three lineage columns are added so a served tile can cite what produced it rather than
-- arriving anonymous: label_release_key, source_count, label_review_tier. Each is read from a
-- foreign-key-reachable row or is NULL. Where the chain cannot answer, the tier says so in
-- words instead of guessing -- the whole point of this migration is that an unavailable fact
-- must not be filled in.
--
-- Scope note: this migration reads the `agri` schema and writes only `geo`. It declares,
-- creates and alters nothing under `agri`, which is owned end to end by the agri-data-service
-- Alembic tree (see the header of src/lib/server/db/schema.ts for the time that rule was
-- broken and had to be reverted).

DROP MATERIALIZED VIEW IF EXISTS geo.mv_strategy_recommendations_coarse;
--> statement-breakpoint

DROP MATERIALIZED VIEW IF EXISTS geo.mv_strategy_recommendations_regional;
--> statement-breakpoint

DROP MATERIALIZED VIEW IF EXISTS geo.mv_strategy_recommendations_detail;
--> statement-breakpoint

DROP VIEW IF EXISTS geo.v_strategy_recommendation_cells;
--> statement-breakpoint

-- One located, governed candidate row per (candidate, real spatial cell it occupies).
--
-- Factored out as a plain view rather than pasted into all three matviews because the join
-- chain below IS the correctness fix: three hand-maintained copies of it is how one of them
-- drifts back toward a shortcut. The three matviews differ only in how they bucket and
-- generalise what this view returns.
--
-- Every join here is a declared foreign key except the two spatial ones, which are real
-- intersections of real geometry:
--
--   candidate -> selection receipt          fk_strategy_selection_candidate_selection_receipt_id_st_d3e8
--   receipt   -> analysis subject           fk_strategy_selection_receipt_analysis_subject_id_analy_120b
--   receipt   -> forecast training run      fk_strategy_selection_receipt_training_run_id_forecast__dc00
--   run       -> strategy label release     fk_forecast_training_run_strategy_label_release_id_stra_4f7c
--
-- The label release is LEFT JOINed on purpose: forecast_training_run.strategy_label_release_id
-- is nullable, so a run that was never bound to a release must produce a row that says it has
-- no release rather than no row at all. An absent citation is a fact about the tile; a missing
-- tile would just look like empty country.
CREATE VIEW geo.v_strategy_recommendation_cells AS
WITH subject_grid AS (
  -- One grid per analysis subject, so a subject tiled by several grids at once cannot emit the
  -- same candidate two or three times under overlapping cells. Finest resolution wins because
  -- it is the most specific real answer available; grid_name breaks ties so the choice is
  -- deterministic across refreshes rather than whatever the planner returned first.
  SELECT DISTINCT ON (subj.id)
    subj.id AS analysis_subject_id,
    cell.grid_name,
    cell.resolution_m
  FROM agri.analysis_subject subj
  JOIN agri.spatial_cell cell
    ON cell.geometry && subj.geometry
   AND ST_Intersects(cell.geometry, subj.geometry)
  ORDER BY subj.id, cell.resolution_m, cell.grid_name
)
SELECT
  candidate.strategy_id,
  receipt.id AS selection_receipt_id,
  cell.cell_key,
  cell.grid_name,
  cell.geometry AS cell_geom,
  cell.centroid AS cell_centroid,
  candidate.conservative_score,
  candidate.expected_effect,
  candidate.effect_lower_bound,
  candidate.effect_upper_bound,
  label_release.release_key AS label_release_key,
  label_release.status AS label_release_status
FROM agri.strategy_selection_candidate candidate
JOIN agri.strategy_selection_receipt receipt
  ON receipt.id = candidate.selection_receipt_id
JOIN agri.analysis_subject subject
  ON subject.id = receipt.analysis_subject_id
JOIN subject_grid
  ON subject_grid.analysis_subject_id = subject.id
JOIN agri.spatial_cell cell
  ON cell.grid_name = subject_grid.grid_name
 AND cell.resolution_m = subject_grid.resolution_m
 AND cell.geometry && subject.geometry
 AND ST_Intersects(cell.geometry, subject.geometry)
LEFT JOIN agri.forecast_training_run training_run
  ON training_run.id = receipt.training_run_id
LEFT JOIN agri.strategy_label_release label_release
  ON label_release.id = training_run.strategy_label_release_id
-- Four gates the 0027 definition had none of, each one a row that must not reach a map:
--   'excluded' / 'insufficient_evidence' / 'no_effect' candidates carry an exclusion_reason and
--   are conclusions AGAINST a strategy, not recommendations of it;
--   a 'staging' receipt has no receipt_checksum and may still be rewritten;
--   an 'abstained' receipt is the model declining to rank, and its candidates rank nothing;
--   a receipt flagged 'cutoff_violation' read data published after its own data cutoff, which
--   is the one audit state that invalidates the result outright.
WHERE candidate.eligibility_state = 'eligible'
  AND receipt.status = 'finalized'
  AND receipt.decision_state = 'ranked'
  AND receipt.audit_state = 'clear';
--> statement-breakpoint

-- Detail tier (z >= 12): one row per real agri.spatial_cell, drawn at its published boundary.
--
-- cell_id is the cell's own globally unique cell_key, prefixed with the tier so a feature
-- inspected in the browser says which tier drew it. No generalisation is applied: at this zoom
-- the real cell is what the viewport can show.
CREATE MATERIALIZED VIEW geo.mv_strategy_recommendations_detail AS
SELECT
  strategy.id AS strategy_id,
  strategy.slug AS strategy_slug,
  strategy.name AS strategy_name,
  strategy.category AS strategy_category,
  bucket.cell_id,
  bucket.suitability_score,
  bucket.effect_utility_score,
  bucket.effect_utility_lower,
  bucket.effect_utility_upper,
  bucket.label_release_key,
  bucket.source_count,
  bucket.label_review_tier,
  bucket.geom
FROM (
  SELECT
    located.strategy_id,
    'detail:' || located.cell_key AS cell_id,
    AVG(located.conservative_score)::double precision AS suitability_score,
    AVG(located.expected_effect)::double precision AS effect_utility_score,
    MIN(located.effect_lower_bound)::double precision AS effect_utility_lower,
    MAX(located.effect_upper_bound)::double precision AS effect_utility_upper,
    COUNT(DISTINCT located.selection_receipt_id)::integer AS source_count,
    CASE
      WHEN COUNT(DISTINCT located.label_release_key) = 1
       AND COUNT(*) FILTER (WHERE located.label_release_key IS NULL) = 0
      THEN MIN(located.label_release_key)
    END AS label_release_key,
    CASE
      WHEN COUNT(located.label_release_key) = 0 THEN 'no_label_release_bound'
      WHEN COUNT(DISTINCT located.label_release_key) > 1
        OR COUNT(*) FILTER (WHERE located.label_release_key IS NULL) > 0
        THEN 'mixed_label_releases'
      WHEN MIN(located.label_release_status) = 'validated'
        THEN 'label_release_validated_owner_signature_unverified'
      ELSE 'label_release_' || MIN(located.label_release_status)
    END AS label_review_tier,
    ST_CollectionExtract(ST_MakeValid(ST_Union(located.cell_geom)), 3) AS geom
  FROM geo.v_strategy_recommendation_cells located
  GROUP BY located.strategy_id, located.cell_key
) bucket
JOIN agri.strategies strategy ON strategy.id = bucket.strategy_id
WHERE bucket.geom IS NOT NULL AND NOT ST_IsEmpty(bucket.geom);
--> statement-breakpoint

-- Regional tier (z 7-11): real cells unioned into 0.25-degree buckets.
--
-- The bucket index is floor(real centroid / 0.25) -- a derived grouping of measured points, the
-- same kind of operation 0023 performs when it groups HUC12 basins by code prefix. It does not
-- move a cell and it cannot invent one: a bucket with no real cells in it produces no row, and
-- the polygon drawn for a bucket is exactly ST_Union of the real cells that landed in it.
-- grid_name is part of the key so two different tilings of the same ground stay two features
-- rather than being silently merged into one that belongs to neither.
CREATE MATERIALIZED VIEW geo.mv_strategy_recommendations_regional AS
SELECT
  strategy.id AS strategy_id,
  strategy.slug AS strategy_slug,
  strategy.name AS strategy_name,
  strategy.category AS strategy_category,
  bucket.cell_id,
  bucket.suitability_score,
  bucket.effect_utility_score,
  bucket.effect_utility_lower,
  bucket.effect_utility_upper,
  bucket.label_release_key,
  bucket.source_count,
  bucket.label_review_tier,
  bucket.geom
FROM (
  SELECT
    located.strategy_id,
    'regional:'
      || located.grid_name
      || ':'
      || floor(ST_X(located.cell_centroid) / 0.25)::bigint
      || ':'
      || floor(ST_Y(located.cell_centroid) / 0.25)::bigint AS cell_id,
    AVG(located.conservative_score)::double precision AS suitability_score,
    AVG(located.expected_effect)::double precision AS effect_utility_score,
    MIN(located.effect_lower_bound)::double precision AS effect_utility_lower,
    MAX(located.effect_upper_bound)::double precision AS effect_utility_upper,
    COUNT(DISTINCT located.selection_receipt_id)::integer AS source_count,
    CASE
      WHEN COUNT(DISTINCT located.label_release_key) = 1
       AND COUNT(*) FILTER (WHERE located.label_release_key IS NULL) = 0
      THEN MIN(located.label_release_key)
    END AS label_release_key,
    CASE
      WHEN COUNT(located.label_release_key) = 0 THEN 'no_label_release_bound'
      WHEN COUNT(DISTINCT located.label_release_key) > 1
        OR COUNT(*) FILTER (WHERE located.label_release_key IS NULL) > 0
        THEN 'mixed_label_releases'
      WHEN MIN(located.label_release_status) = 'validated'
        THEN 'label_release_validated_owner_signature_unverified'
      ELSE 'label_release_' || MIN(located.label_release_status)
    END AS label_review_tier,
    ST_CollectionExtract(
      ST_MakeValid(ST_SimplifyPreserveTopology(ST_Union(located.cell_geom), 0.002)),
      3
    ) AS geom
  FROM geo.v_strategy_recommendation_cells located
  GROUP BY
    located.strategy_id,
    located.grid_name,
    floor(ST_X(located.cell_centroid) / 0.25)::bigint,
    floor(ST_Y(located.cell_centroid) / 0.25)::bigint
) bucket
JOIN agri.strategies strategy ON strategy.id = bucket.strategy_id
WHERE bucket.geom IS NOT NULL AND NOT ST_IsEmpty(bucket.geom);
--> statement-breakpoint

-- Coarse tier (z <= 6): the same real cells unioned into 1-degree buckets and simplified harder.
-- At this zoom a 1-degree footprint is a few pixels, so the vertex budget goes to coverage
-- rather than outline fidelity.
CREATE MATERIALIZED VIEW geo.mv_strategy_recommendations_coarse AS
SELECT
  strategy.id AS strategy_id,
  strategy.slug AS strategy_slug,
  strategy.name AS strategy_name,
  strategy.category AS strategy_category,
  bucket.cell_id,
  bucket.suitability_score,
  bucket.effect_utility_score,
  bucket.effect_utility_lower,
  bucket.effect_utility_upper,
  bucket.label_release_key,
  bucket.source_count,
  bucket.label_review_tier,
  bucket.geom
FROM (
  SELECT
    located.strategy_id,
    'coarse:'
      || located.grid_name
      || ':'
      || floor(ST_X(located.cell_centroid))::bigint
      || ':'
      || floor(ST_Y(located.cell_centroid))::bigint AS cell_id,
    AVG(located.conservative_score)::double precision AS suitability_score,
    AVG(located.expected_effect)::double precision AS effect_utility_score,
    MIN(located.effect_lower_bound)::double precision AS effect_utility_lower,
    MAX(located.effect_upper_bound)::double precision AS effect_utility_upper,
    COUNT(DISTINCT located.selection_receipt_id)::integer AS source_count,
    CASE
      WHEN COUNT(DISTINCT located.label_release_key) = 1
       AND COUNT(*) FILTER (WHERE located.label_release_key IS NULL) = 0
      THEN MIN(located.label_release_key)
    END AS label_release_key,
    CASE
      WHEN COUNT(located.label_release_key) = 0 THEN 'no_label_release_bound'
      WHEN COUNT(DISTINCT located.label_release_key) > 1
        OR COUNT(*) FILTER (WHERE located.label_release_key IS NULL) > 0
        THEN 'mixed_label_releases'
      WHEN MIN(located.label_release_status) = 'validated'
        THEN 'label_release_validated_owner_signature_unverified'
      ELSE 'label_release_' || MIN(located.label_release_status)
    END AS label_review_tier,
    ST_CollectionExtract(
      ST_MakeValid(ST_SimplifyPreserveTopology(ST_Union(located.cell_geom), 0.01)),
      3
    ) AS geom
  FROM geo.v_strategy_recommendation_cells located
  GROUP BY
    located.strategy_id,
    located.grid_name,
    floor(ST_X(located.cell_centroid))::bigint,
    floor(ST_Y(located.cell_centroid))::bigint
) bucket
JOIN agri.strategies strategy ON strategy.id = bucket.strategy_id
WHERE bucket.geom IS NOT NULL AND NOT ST_IsEmpty(bucket.geom);
--> statement-breakpoint

-- The unique indexes are not optional decoration: agri's strategy_mv_refresh job refreshes
-- these CONCURRENTLY, and REFRESH MATERIALIZED VIEW CONCURRENTLY refuses to run without one.
-- Same names and same (strategy_id, cell_id) key as 0027 used.
CREATE UNIQUE INDEX uq_mv_strategy_recommendations_detail
  ON geo.mv_strategy_recommendations_detail (strategy_id, cell_id);
--> statement-breakpoint

CREATE INDEX idx_mv_strategy_recommendations_detail_geom
  ON geo.mv_strategy_recommendations_detail USING GIST (geom);
--> statement-breakpoint

CREATE UNIQUE INDEX uq_mv_strategy_recommendations_regional
  ON geo.mv_strategy_recommendations_regional (strategy_id, cell_id);
--> statement-breakpoint

CREATE INDEX idx_mv_strategy_recommendations_regional_geom
  ON geo.mv_strategy_recommendations_regional USING GIST (geom);
--> statement-breakpoint

CREATE UNIQUE INDEX uq_mv_strategy_recommendations_coarse
  ON geo.mv_strategy_recommendations_coarse (strategy_id, cell_id);
--> statement-breakpoint

CREATE INDEX idx_mv_strategy_recommendations_coarse_geom
  ON geo.mv_strategy_recommendations_coarse USING GIST (geom);
--> statement-breakpoint

-- Same MVT tag ('strategy-recommendations') and the same zoom thresholds as 0027, because
-- src/lib/map/layers/strategy-layer.ts declares that exact source-layer string and reads
-- `suitability_score` out of it; a renamed tag or a moved threshold renders nothing while
-- reporting no error. `suitability_score` therefore keeps both its name and its meaning.
--
-- causal_benefit_tau leaves the wire. Nothing in the client ever read it, so removing it breaks
-- no style, and shipping a per-feature attribute named for a causal effect straight into the
-- browser was the part of 0027 that could most easily have become a user-facing claim.
-- effect_utility_score replaces it and must never be labelled a benefit, a yield change or an
-- expected outcome in any UI: it is a model-internal ordering quantity over an evaluation-only
-- plane whose labels no owner has signed.
--
-- The three lineage attributes travel with every feature so that a tile can answer "where did
-- this come from" without a second query: `label_release_key` (NULL when the chain cannot cite
-- exactly one release), `source_count` (how many distinct finalized selection receipts were
-- averaged into this feature) and `label_review_tier`, which states in words what the release
-- behind it is worth -- including 'no_label_release_bound' and 'mixed_label_releases', the two
-- cases where there is nothing honest to cite.
CREATE OR REPLACE FUNCTION geo.strategy_recommendations_tiles(z integer, x integer, y integer)
RETURNS bytea
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
SET search_path = public, pg_catalog
AS $$
DECLARE
  bounds_3857 geometry;
  bounds_4326 geometry;
  mvt bytea;
BEGIN
  bounds_3857 := ST_TileEnvelope(z, x, y);
  bounds_4326 := ST_Transform(bounds_3857, 4326);

  IF z <= 6 THEN
    SELECT COALESCE(ST_AsMVT(tile, 'strategy-recommendations', 4096, 'geom'), ''::bytea) INTO mvt
    FROM (
      SELECT
        cell_id,
        strategy_id,
        strategy_slug,
        strategy_name,
        strategy_category,
        suitability_score,
        effect_utility_score,
        label_release_key,
        source_count,
        label_review_tier,
        ST_AsMVTGeom(ST_Transform(geom, 3857), bounds_3857, 4096, 64, true) AS geom
      FROM geo.mv_strategy_recommendations_coarse
      WHERE geom && bounds_4326 AND ST_Intersects(geom, bounds_4326)
    ) AS tile;
  ELSIF z <= 11 THEN
    SELECT COALESCE(ST_AsMVT(tile, 'strategy-recommendations', 4096, 'geom'), ''::bytea) INTO mvt
    FROM (
      SELECT
        cell_id,
        strategy_id,
        strategy_slug,
        strategy_name,
        strategy_category,
        suitability_score,
        effect_utility_score,
        label_release_key,
        source_count,
        label_review_tier,
        ST_AsMVTGeom(ST_Transform(geom, 3857), bounds_3857, 4096, 64, true) AS geom
      FROM geo.mv_strategy_recommendations_regional
      WHERE geom && bounds_4326 AND ST_Intersects(geom, bounds_4326)
    ) AS tile;
  ELSE
    SELECT COALESCE(ST_AsMVT(tile, 'strategy-recommendations', 4096, 'geom'), ''::bytea) INTO mvt
    FROM (
      SELECT
        cell_id,
        strategy_id,
        strategy_slug,
        strategy_name,
        strategy_category,
        suitability_score,
        effect_utility_score,
        label_release_key,
        source_count,
        label_review_tier,
        ST_AsMVTGeom(ST_Transform(geom, 3857), bounds_3857, 4096, 64, true) AS geom
      FROM geo.mv_strategy_recommendations_detail
      WHERE geom && bounds_4326 AND ST_Intersects(geom, bounds_4326)
    ) AS tile;
  END IF;

  RETURN mvt;
END;
$$;
