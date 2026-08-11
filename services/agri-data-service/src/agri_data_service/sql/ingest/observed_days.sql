-- observed_days
-- Purpose: one row per calendar day a feature layer actually holds published, geometry-linked,
--          publisher-dated observations for, with how many rows that day carries. THE canonical
--          day census. Two callers bind it -- the completeness report over every layer, and lane
--          reconciliation over one layer -- and they must never disagree about which rows count.
-- Loaded by: agri_data_service.ingest.validation.queries (which formats it once per scope and hands
--          the one-layer form to agri_data_service.ingest.reconcile)
-- Params: published_status (text) -- the one `geo.features.status` value the map serves,
--         row_limit (int) -- a hard cap on returned rows; both callers REFUSE a result that reached
--         the cap rather than reasoning about a truncated day set,
--         layer_id (text holding a UUID) -- supplied ONLY by the one-layer form; the all-layers form
--         carries no such parameter at all. See the scope slot note below.
--
-- The first line above is a dispatch marker, not documentation. The unit tests answer
-- AsyncSession.execute from a recording stub and route each statement by the first single-word comment
-- line in it, so that line must stay first and stay spelled exactly as it is. No other comment line in
-- this file may be a bare `--` followed by one word, or it would become a rival marker. See "Marker
-- protocol" in sql/AGENTS.md.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md -- SQLAlchemy scans comments for colon-prefixed words too and would mint a bind
-- parameter nobody supplies. That rule is stricter than usual in this file, because the scope slot
-- below means the parameter list is not even the same for both callers.
--
-- WHY THIS FILE EXISTS AT ALL, AND WHAT IT REPLACES. There used to be two files -- one for the
-- report, one for reconciliation -- carrying the same three filters at two scopes. They agreed by
-- inspection and by a comment asking the next reader to keep them agreeing, which is not a mechanism.
-- The two answers are load-bearing against each other: the report decides a day is MISSING and the
-- reconciler decides a planned window has LANDED, so a filter that drifted between them would let a
-- lane settle a window over days the report is simultaneously reporting as absent. One statement
-- cannot drift from itself.
--
-- THE SCOPE SLOT. The one clause the two callers genuinely need to differ on is written here as a
-- Python `.format()` slot, filled at import time from a closed set of two constants in
-- validation/queries.py and NEVER from request input -- the same load-time slot pattern
-- ingest/store_drought_area.sql already uses for its replace predicate. It is not a bound parameter
-- for a measured reason: the alternative spelling, one statement with an "either the parameter is
-- null or the column matches it" predicate, cannot use the index whose leading column is layer_id
-- (features_layer_external_id_unique), because a prepared statement's generic plan has to be correct
-- for a null parameter too and so degrades to a filter over every row of geo.features. The all-layers
-- caller scans everything by definition and loses nothing; the one-layer caller would lose the only
-- index that makes its scan bounded.
--
-- What this returns: one row per (layer, day) pair, ordered by layer then day, each row carrying the
-- layer's name, the day, and the number of published features observed on it. The one-layer form
-- returns the same shape with exactly one distinct layer name in it.
--
-- WHY THE THREE FILTERS ARE EXACTLY THESE THREE: this mirrors the `observed` step of the TypeScript
-- read model's readObservationWindows -- published rows only, geometry-linked rows only, and only a
-- publisher-named day that `geo.feature_observation_day` could actually parse. Anything looser would
-- report days back to the caller that the slider will never offer, and the report would call a layer
-- complete over a span the user cannot scrub through. For reconciliation the same three filters carry
-- a second consequence: a row missing either of the first two is drawn on the map but invisible to the
-- time axis, so counting its day as coverage would mark a window succeeded whose days the slider still
-- cannot reach.
--
-- WHY COVERAGE IS READ FROM THE DATA AND NEVER FROM A CURSOR FILE: a cursor recorded where a walk had
-- REACHED, not what had LANDED. On the first full pass those differed by 169 of 298 windows. A day is
-- covered here because rows for it exist, or it is not covered; there is no third answer and no file
-- to be wrong. The module docstring in ingest/reconcile.py tells the whole story.
--
-- How this query works, clause by clause:
--
--   FROM geo.layers AS layers JOIN geo.features AS features ON features.layer_id = layers.id
--     A join stitches two tables together row by row on a matching condition -- here, each feature row
--     is paired with the layer row it belongs to, so the layer's human-readable name is available
--     beside the feature. A plain JOIN (an "inner" join) keeps only pairs that match, which is what is
--     wanted: a feature with no layer is not a thing this schema can hold, and an empty layer has no
--     observed days to report by definition.
--
--   geo.feature_observation_day(features.properties)
--     A database function this service owns. It digs the publisher's own observation timestamp out of
--     the feature's JSON payload and reduces it to a UTC calendar day, answering NULL when the payload
--     carries no parseable one. Calling it rather than reading a column is the point: the same rule
--     that dates a feature for the map dates it for this census, so "covered" here means "visible
--     there".
--
--   WHERE features.status = the published-status parameter
--     Only rows the map actually serves. Draft or superseded rows are real rows in the table but
--     nobody can see them, so counting them would overstate coverage.
--
--   AND features.geometry_id IS NOT NULL
--     Only rows linked into the geometry dimension. An unlinked row has no shape to draw and the
--     serving path skips it. `IS NOT NULL` rather than `<> NULL` because in SQL a comparison against
--     NULL is itself NULL -- neither true nor false -- so `IS` is the only way to test for it.
--
--   AND geo.feature_observation_day(features.properties) IS NOT NULL
--     Only rows whose day could be parsed. An undated row is counted elsewhere in the report as a
--     validity failure; it must not silently land on some default day here, and it cannot vouch for
--     any backfill window either.
--
--   the scope slot, immediately below that filter
--     Either nothing at all (the report, which wants every layer) or one extra equality on the
--     feature's layer id, whose bound value is cast to uuid so PostgreSQL knows which type to resolve
--     the text parameter to. A cast like that changes no value; it only pins a type that would
--     otherwise be ambiguous.
--
--   GROUP BY layers.name, geo.feature_observation_day(features.properties)
--     GROUP BY collapses many rows into one row per distinct combination of the listed expressions --
--     here, one row per layer per day. Once rows are collapsed there is no longer a single "the
--     feature" to select, so every other selected column must be an aggregate that summarises the
--     group. That is what `count(*)` is: the number of rows that fell into this group. The whole
--     expression is repeated rather than the output name reused, because standard SQL evaluates GROUP
--     BY before the SELECT list has produced its names.
--
--   ORDER BY layers.name, geo.feature_observation_day(features.properties)
--     A stable, total order. It also makes the LIMIT below deterministic -- without an order, a capped
--     result would be an arbitrary sample rather than the earliest days.
--
--   LIMIT row_limit
--     The cap. It exists so a runaway layer cannot pull an unbounded result into memory; both callers
--     treat hitting it as an error, not as an answer, because a truncated day set silently invents
--     absent days -- and an absent day at the tail is what turns a covered window into a partial one
--     and leaves it queued forever.
SELECT layers.name                                            AS stream,
       geo.feature_observation_day(features.properties)       AS observed_day,
       count(*)                                               AS observation_count
  FROM geo.layers AS layers
  JOIN geo.features AS features
    ON features.layer_id = layers.id
 WHERE features.status = :published_status
   AND features.geometry_id IS NOT NULL
   AND geo.feature_observation_day(features.properties) IS NOT NULL
   {layer_scope}
 GROUP BY layers.name, geo.feature_observation_day(features.properties)
 ORDER BY layers.name, geo.feature_observation_day(features.properties)
 LIMIT :row_limit
