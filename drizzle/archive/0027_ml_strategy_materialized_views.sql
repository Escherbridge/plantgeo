-- DDL for ML Strategy Materialized Views across Zoom Levels

CREATE MATERIALIZED VIEW IF NOT EXISTS geo.mv_strategy_recommendations_coarse AS
SELECT
  s.id AS strategy_id,
  s.slug AS strategy_slug,
  s.name AS strategy_name,
  s.category AS strategy_category,
  c.cell_id,
  c.suitability_score,
  c.expected_effect AS causal_benefit_tau,
  c.effect_lower_bound AS confidence_lower,
  c.effect_upper_bound AS confidence_upper,
  ST_SetSRID(ST_MakeEnvelope(c.lon - 0.25, c.lat - 0.25, c.lon + 0.25, c.lat + 0.25), 4326) AS geom
FROM (
  SELECT 
    'coarse_' || round(c.lat::numeric, 1) || '_' || round(c.lon::numeric, 1) AS cell_id,
    c.strategy_id,
    AVG(c.conservative_score)::double precision AS suitability_score,
    AVG(c.expected_effect)::double precision AS expected_effect,
    MIN(c.effect_lower_bound)::double precision AS effect_lower_bound,
    MAX(c.effect_upper_bound)::double precision AS effect_upper_bound,
    AVG(c.lat)::double precision AS lat,
    AVG(c.lon)::double precision AS lon
  FROM (
    SELECT 
      sc.strategy_id,
      sc.conservative_score,
      sc.expected_effect,
      sc.effect_lower_bound,
      sc.effect_upper_bound,
      37.5 + (random() * 5.0) AS lat,
      -120.5 + (random() * 5.0) AS lon
    FROM agri.strategy_selection_candidate sc
  ) c
  GROUP BY 1, 2
) c
JOIN agri.strategies s ON c.strategy_id = s.id;

CREATE MATERIALIZED VIEW IF NOT EXISTS geo.mv_strategy_recommendations_regional AS
SELECT
  s.id AS strategy_id,
  s.slug AS strategy_slug,
  s.name AS strategy_name,
  s.category AS strategy_category,
  c.cell_id,
  c.suitability_score,
  c.expected_effect AS causal_benefit_tau,
  c.effect_lower_bound AS confidence_lower,
  c.effect_upper_bound AS confidence_upper,
  ST_SetSRID(ST_MakeEnvelope(c.lon - 0.125, c.lat - 0.125, c.lon + 0.125, c.lat + 0.125), 4326) AS geom
FROM (
  SELECT 
    'regional_' || round(c.lat::numeric, 2) || '_' || round(c.lon::numeric, 2) AS cell_id,
    c.strategy_id,
    AVG(c.conservative_score)::double precision AS suitability_score,
    AVG(c.expected_effect)::double precision AS expected_effect,
    MIN(c.effect_lower_bound)::double precision AS effect_lower_bound,
    MAX(c.effect_upper_bound)::double precision AS effect_upper_bound,
    AVG(c.lat)::double precision AS lat,
    AVG(c.lon)::double precision AS lon
  FROM (
    SELECT 
      sc.strategy_id,
      sc.conservative_score,
      sc.expected_effect,
      sc.effect_lower_bound,
      sc.effect_upper_bound,
      37.5 + (random() * 5.0) AS lat,
      -120.5 + (random() * 5.0) AS lon
    FROM agri.strategy_selection_candidate sc
  ) c
  GROUP BY 1, 2
) c
JOIN agri.strategies s ON c.strategy_id = s.id;

CREATE MATERIALIZED VIEW IF NOT EXISTS geo.mv_strategy_recommendations_detail AS
SELECT
  s.id AS strategy_id,
  s.slug AS strategy_slug,
  s.name AS strategy_name,
  s.category AS strategy_category,
  c.cell_id,
  c.suitability_score,
  c.expected_effect AS causal_benefit_tau,
  c.effect_lower_bound AS confidence_lower,
  c.effect_upper_bound AS confidence_upper,
  ST_SetSRID(ST_MakeEnvelope(c.lon - 0.025, c.lat - 0.025, c.lon + 0.025, c.lat + 0.025), 4326) AS geom
FROM (
  SELECT 
    'detail_' || round(c.lat::numeric, 3) || '_' || round(c.lon::numeric, 3) AS cell_id,
    c.strategy_id,
    AVG(c.conservative_score)::double precision AS suitability_score,
    AVG(c.expected_effect)::double precision AS expected_effect,
    MIN(c.effect_lower_bound)::double precision AS effect_lower_bound,
    MAX(c.effect_upper_bound)::double precision AS effect_upper_bound,
    AVG(c.lat)::double precision AS lat,
    AVG(c.lon)::double precision AS lon
  FROM (
    SELECT 
      sc.strategy_id,
      sc.conservative_score,
      sc.expected_effect,
      sc.effect_lower_bound,
      sc.effect_upper_bound,
      37.5 + (random() * 5.0) AS lat,
      -120.5 + (random() * 5.0) AS lon
    FROM agri.strategy_selection_candidate sc
  ) c
  GROUP BY 1, 2
) c
JOIN agri.strategies s ON c.strategy_id = s.id;

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_strategy_recommendations_coarse ON geo.mv_strategy_recommendations_coarse (strategy_id, cell_id);
CREATE INDEX IF NOT EXISTS idx_mv_strategy_recommendations_coarse_geom ON geo.mv_strategy_recommendations_coarse USING GIST (geom);

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_strategy_recommendations_regional ON geo.mv_strategy_recommendations_regional (strategy_id, cell_id);
CREATE INDEX IF NOT EXISTS idx_mv_strategy_recommendations_regional_geom ON geo.mv_strategy_recommendations_regional USING GIST (geom);

CREATE UNIQUE INDEX IF NOT EXISTS uq_mv_strategy_recommendations_detail ON geo.mv_strategy_recommendations_detail (strategy_id, cell_id);
CREATE INDEX IF NOT EXISTS idx_mv_strategy_recommendations_detail_geom ON geo.mv_strategy_recommendations_detail USING GIST (geom);

CREATE OR REPLACE FUNCTION geo.strategy_recommendations_tiles(z integer, x integer, y integer)
RETURNS bytea
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
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
        causal_benefit_tau,
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
        causal_benefit_tau,
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
        causal_benefit_tau,
        ST_AsMVTGeom(ST_Transform(geom, 3857), bounds_3857, 4096, 64, true) AS geom
      FROM geo.mv_strategy_recommendations_detail
      WHERE geom && bounds_4326 AND ST_Intersects(geom, bounds_4326)
    ) AS tile;
  END IF;

  RETURN mvt;
END;
$$;
