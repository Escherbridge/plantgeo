-- Purpose: one row per plan-driven historical walk for the /ops/backfill dashboard -- how
--          many lattice cells the walk has landed, out of how many the grid holds, how many
--          chunks it has written, HOW MANY OBSERVED VALUES those chunks actually carried,
--          when it started, and how long ago its newest chunk landed.
-- Loaded by: agri_data_service.routes.ops
-- Params: throughput_window_hours (int) -- how many trailing hours the "covered recently"
--         and "landed recently" counters look back over. The route divides those counters
--         by this same number to get a per-hour rate, so the two must agree.
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param trap" in
-- sql/AGENTS.md: SQLAlchemy scans comments for colon-prefixed words too, so a colon glued
-- to a word anywhere in this file -- comment or statement -- becomes a bind parameter that
-- nobody supplies and the statement fails before it reaches the database. That is why the
-- example version strings below write a dot where the real value writes a colon.
--
-- WHY THIS STATEMENT EXISTS
--
-- The lanes table on the same page reads the agri.job_* durable ledger. Only two loads
-- enqueue ledger work items; the NASA POWER and Open-Meteo ERA5-Land historical walks are
-- driven by a reviewed plan file instead and never write a job row, so the ledger cannot
-- see them at all. What they DO write, once per chunk, is an agri.source_release row. This
-- statement reconstructs each walk's progress from those rows alone, except for one column:
-- observed_value_count, which is how many rows in agri.signal_observation those releases
-- actually carry.
--
-- WHY THE VALUE COUNT IS WORTH ITS COST
--
-- Chunks landed and cells done are both counted from release rows, and a release row is
-- written whether or not the chunk it describes returned any data. Measured on production
-- 2026-08-09, the Open-Meteo radiation lane's pre-fix run published 8 chunks over 397 cells
-- under the wrong model key with every series coming back no_data: 397 of 397 cells, 8
-- chunks, ZERO observed values. Beside it sits the fixed run, same walk label, same 397 of
-- 397, same 8 chunks, and 580,414 real values. Without this column those two rows read
-- identically on the page, and the release set is immutable provenance that must not be
-- deleted -- so counting what each walk landed is the only way to tell a complete walk from a
-- complete-but-empty one.
--
-- The cost is real, and it is why the route caches this statement instead of re-running it on
-- every five-second tick: the release-grain rollup below is an index-only scan of the whole
-- (source_release_id, observed_at) index over ~46M rows, which takes this statement from
-- 0.15 s to 2.0 s against production. Every other column here still reads only the few
-- thousand rows of agri.source_release.
--
-- WHAT A "WALK" IS
--
-- One walk is one campaign over one lattice for one window with one parameter set, and it
-- is identified by tearing the per-chunk part off the release's source_version. The two
-- producers compose that string differently, and both shapes are handled here.
--
--   Open-Meteo writes  schema-version . window . grid-name . chunk-ordinal
--     e.g. 'open-meteo-era5-land-archive-daily-v1' then '20220802-20260802' then
--     'sentinel2-ndvi-0p25deg' then 'cells-0007', joined by colons. The trailing
--     'cells-NNNN' is the chunk ordinal, and everything before it names the walk. Which
--     lattice cells that chunk covered is recorded in the release's query_parameters under
--     the key cell_keys, and the daily parameters it asked for under the key parameters.
--
--   NASA POWER writes  schema-version . window . cell-key
--     e.g. 'nasa-power-daily-v1' then '20220430-20260430' then
--     'na-sample.1deg.p043.00.m116.00', joined by colons -- one release per single cell,
--     with that cell's key as the tail. Neither the schema version nor the window contains
--     a colon, so the first two segments name the walk and everything after the second
--     colon is the cell key. This producer records no cell_keys or parameters key; the
--     upstream parameters are only visible inside the request_url it stored, so they are
--     pulled back out of that URL below.
--
-- The parameter set is part of a walk's identity, not decoration. Three Open-Meteo walks
-- share one window and one lattice and differ only in whether they asked for soil
-- temperature, soil moisture, or vapour-pressure deficit; grouping without the parameters
-- would fuse them into one meaningless row.
--
-- A walk that is re-run covers cells it already covered, under brand new release rows.
-- Cells are therefore counted DISTINCT and never summed -- a continuation must not push a
-- 397-cell walk to 405 of 397 done.
--
-- How this query works, clause by clause:
--
--   WITH historical_release AS (...)
--     A CTE ("common table expression") -- a named subquery written up front and then
--     referenced below like a table. This one narrows agri.source_release to the rows that
--     are shaped like a walk chunk and tags each with which of the two shapes it is.
--
--   release.source_version ~ '[:]cells-[0-9]+$'
--     The ~ operator is "matches this regular expression". A regular expression is a
--     pattern language: [0-9] means one digit, + means one or more of them, and $ anchors
--     the pattern to the end of the string. So this asks "does the version end in a colon
--     and a chunk ordinal", which is exactly what distinguishes the Open-Meteo shape from
--     the NASA one. Used once as a WHERE filter and once as a selected boolean column.
--     The leading colon is wrapped in [ ] -- a character class matching that one character
--     -- purely so that no colon in this file is ever glued directly to a letter. Written
--     the obvious way, SQLAlchemy would read the pattern inside these quotes as a bind
--     parameter named cells and every execution would fail on a missing bind. The trap
--     described in the header applies to the statement, not only to its comments.
--
--   OR release.source_version ~ '^[^:]+:[0-9]{8}-[0-9]{8}:.'
--     The NASA shape. ^ anchors to the start, [^X] means "any character except X", and {8}
--     means exactly eight of the previous item -- so "a first segment with no colon in it, a
--     colon, an eight-digit start day, a dash, an eight-digit end day, a colon, then at
--     least one more character". The colons survive in this comment where the pattern above
--     could not keep its own, because the trap only springs on a colon glued to a letter or
--     a digit and every colon here is followed by a bracket or a dot. Any release matching
--     neither shape is not a walk chunk and drops out here -- the lone Sentinel-2 NDVI
--     release, whose version is a single segment with no window in it, is excluded by this
--     filter rather than by being named and blacklisted.
--
--   WITH walk_release AS (...)
--     Still one row per release, but with the three things the grouping needs computed --
--     the walk label, the parameter set, and the cell keys this one chunk covered.
--
--   regexp_replace(source_version, '[:]cells-[0-9]+$', '')
--     regexp_replace finds the pattern and substitutes the third argument for it; an empty
--     third argument means "delete it". Here it strips the trailing chunk ordinal, turning
--     every chunk of one Open-Meteo walk into the same label.
--
--   regexp_replace(source_version, '^([^:]+:[0-9]{8}-[0-9]{8}):.*$', '\1')
--     The NASA case, and a different use of the same function. Parentheses in the pattern
--     capture the part they surround; \1 in the replacement means "put capture number 1
--     back". Since the pattern consumes the whole string, the effect is "keep the first two
--     segments, discard the cell key". If a version ever fails to match, regexp_replace
--     returns it unchanged, so an unrecognized release becomes its own one-chunk walk
--     rather than silently joining somebody else's.
--
--   (SELECT array_agg(x ORDER BY x) FROM jsonb_array_elements_text(...) AS x)
--     jsonb_array_elements_text expands a JSON array stored in a column into one row per
--     element, as text. array_agg then collapses those rows back into a SQL array, and the
--     ORDER BY inside it fixes the order so the same parameter set always produces the same
--     array and therefore always groups together. Wrapping the pair in a parenthesized
--     SELECT makes it a scalar subquery -- one value, usable in a column list. When the key
--     is absent the expansion yields no rows and array_agg returns NULL, which is the
--     honest answer for "this producer did not record its parameters here".
--
--   substring(request_url FROM 'parameters=([^&]*)')
--     The NASA parameter set, recovered from the stored request URL. substring with a
--     regular expression returns just the captured group, i.e. the value of the URL's
--     parameters argument up to the next & separator. That value is URL-encoded, so the
--     comma between names arrives as the escape %2C; replace() puts the commas back and
--     string_to_array splits on them. unnest turns that array into rows so the same
--     array_agg ... ORDER BY sorting can be applied as above.
--
--   ARRAY[regexp_replace(source_version, '^[^:]+:[0-9]{8}-[0-9]{8}:', '')]
--     The NASA cell list, which is always exactly one cell. ARRAY[...] builds a one-element
--     array so both producers hand the next CTE the same column type. The pattern deletes
--     the schema version and window from the front, leaving the cell key -- which is a
--     literal agri.spatial_cell key, as the join below relies on.
--
--   WITH walk_cell AS (...)
--     One row per (walk, lattice cell), no matter how many releases covered that cell.
--
--   CROSS JOIN LATERAL unnest(covered_cell_keys) AS covered(cell_key)
--     unnest turns one array into one row per element. LATERAL is what lets the function on
--     the right see the row on the left -- an ordinary subquery cannot reference the row it
--     is being joined to. CROSS means every produced row is kept. So a 50-cell Open-Meteo
--     chunk becomes 50 rows here and a NASA release becomes 1. A release whose cell list is
--     NULL produces no rows and simply contributes no cells, which is correct.
--
--   max(created_at) grouped down to (walk, cell)
--     The GROUP BY is what makes the cell count distinct. The max is the last time any
--     release covered this cell, which is what the trailing-window counter below tests.
--
--   LEFT JOIN agri.spatial_cell AS cell ON cell.cell_key = walk_cell.cell_key
--     Both producers' cell keys are literal agri.spatial_cell keys, so the lattice a walk is
--     working on can be read off the cells themselves instead of being guessed from the
--     version string. LEFT keeps the cell even when no such row exists, so a cell key that
--     resolves to nothing is visible as unresolved rather than vanishing from the count.
--
--   count(*) FILTER (WHERE last_covered_at >= now() - make_interval(hours => CAST(...)))
--     FILTER restricts one aggregate to the rows matching its condition while the other
--     aggregates in the same SELECT still see every row -- that is how "cells covered ever"
--     and "cells covered in the trailing window" come out of one pass. now() is the current
--     transaction timestamp and make_interval(hours => N) builds a span of N hours;
--     subtracting gives the start of the window. make_interval is used instead of the quoted
--     interval literal because a bound parameter cannot appear inside a quoted literal, and
--     the CAST pins the parameter to integer so the database never has to guess its type.
--
--   count(DISTINCT cell.grid_name) AS distinct_grid_count
--     DISTINCT inside an aggregate counts each different value once. A healthy walk works
--     one lattice and reports 1 here; 0 means none of its cells resolved and anything above
--     1 means the cell keys straddle two grids. Only the 1 case is trusted below.
--
--   WITH release_volume AS (... GROUP BY observation.source_release_id)
--     How many observed values each release carries, at release grain. Every row of
--     agri.signal_observation names the release it came from, and the whole table is grouped
--     down to one row per release -- about 1,800 of them -- in a single pass over the
--     (source_release_id, observed_at) index. Grouping the whole table is not laziness: every
--     observation in this warehouse belongs to a walk release, so there is no narrower set to
--     scan, and asking per release instead (a LATERAL count for each of the 1,800) measured
--     11 s against 2 s for this shape.
--
--     Restricting it up front -- WHERE observation.source_release_id IN (SELECT source_release_id
--     FROM walk_release) -- looks like the obvious third option and is WORSE, measured against
--     production 2026-08-09 with EXPLAIN (ANALYZE, BUFFERS): 12.0 s against 2.7 s. The semi-join
--     against the CTE takes the index-only grouped scan away and leaves a sequential scan of all
--     46,146,568 rows. It removes nothing either, for the reason above -- the releases it would
--     filter to are already every release the observations name. Do not re-litigate this without
--     a fresh plan; the shape below is the fast one.
--
--   WITH walk_release_rollup AS (...)
--     The chunk-grain totals, kept separate from the cell-grain totals on purpose. Folding
--     them together is not possible without corrupting both -- one release covers up to 50
--     cells, so a joined aggregate would multiply the chunk count by the cells per chunk and
--     the cell count by the releases per cell. Aggregating each grain on its own and joining
--     one-row-per-walk to one-row-per-walk keeps both exact. This is the same reason the
--     lane query keeps its item, attempt and checkpoint rollups apart.
--
--   WITH grid_size AS (...)
--     How many cells each lattice holds, which is the walk's denominator. It is counted from
--     agri.spatial_cell rather than read from a plan file, because the database is the only
--     thing this page is allowed to believe.
--
--   LEFT JOIN walk_cell_rollup ON ... IS NOT DISTINCT FROM ...
--     The two rollups are matched on the three columns that identify a walk. The parameter
--     set is compared with IS NOT DISTINCT FROM rather than = because it can be NULL, and
--     NULL = NULL is not true in SQL -- it is unknown, so a plain = would drop exactly the
--     walks whose parameters were never recorded. IS NOT DISTINCT FROM is equality that
--     treats two NULLs as equal.
--
--   LEFT JOIN grid_size ON grid_size.grid_name = ... AND distinct_grid_count = 1
--     The denominator is attached only when the walk's cells all sit on one identifiable
--     lattice. When they do not, this join finds nothing, grid_name and target_cells both
--     come back NULL, and the dashboard prints an em dash and no percentage. An honest
--     absence is required here; inventing a denominator would turn an unknown into a
--     confident wrong number.
--
--     Known limitation: the full lattice is the only denominator this statement can derive,
--     so a plan that deliberately targets a strict subset of it -- a regional probe rather
--     than the whole grid -- still reads against the full count and shows low, slow-moving
--     completion until either the plan is widened to the lattice or a producer starts
--     recording its own intended cell count in query_parameters. Nothing here can tell "still
--     walking toward the full grid" apart from "finished a smaller grid on purpose" without
--     that signal, and that includes a probe a later, wider plan has since superseded: the two
--     4-cell soil-wetness pilots on production sit permanently at 4 of 397 beside the 397-of-397
--     plan that replaced them, and both readings are literally true. Deciding they are the same
--     campaign would take a walk-family rule over overlapping cell coverage, which is a great
--     deal of machinery to build on a guess about intent. What observed_value_count DOES settle
--     is the question that matters most beside them -- verified 2026-08-09, each pilot carries
--     17,544 values, exactly 4 cells x 3 parameters x 1,462 days, so it reads as small and
--     complete-for-its-scope rather than as the chunks-complete-and-empty release set two rows
--     below it.
--
--   COALESCE(sum(release_volume.observed_value_count), 0) AS observed_value_count
--     The walk's total landed values. It is summed, not counted DISTINCT like the cells,
--     because the join beneath it is one release to one volume row -- walk_release already
--     holds exactly one row per release, so nothing fans out and no value is counted twice. A
--     re-run that re-covers a cell writes a NEW release with its own values, and those values
--     are genuinely in the warehouse, so summing them is the honest total. COALESCE turns the
--     NULL that a walk with no matching observations produces into the zero it means, which is
--     precisely the case this column exists to expose.
--
--   count(*) FILTER (WHERE cell.id IS NOT NULL) AS cells_done
--     Counted against resolved cells only, matching the denominator: grid_size counts real
--     agri.spatial_cell rows, so a walk cell that never resolved to one must not inflate the
--     numerator past what the denominator can ever reach. unresolved_cells (below) is where
--     that count goes instead, so it stays visible rather than silently dropped.
--
--   ORDER BY data_source_key, walk_label, parameters
--     A stable, total order so the dashboard's rows do not shuffle between refreshes.
WITH historical_release AS (
    SELECT
        source.key AS data_source_key,
        release.id AS source_release_id,
        release.source_version,
        release.query_parameters,
        release.created_at,
        release.observed_from,
        release.observed_to,
        release.source_version ~ '[:]cells-[0-9]+$' AS chunked_walk
    FROM agri.source_release AS release
    JOIN agri.data_source AS source ON source.id = release.data_source_id
    WHERE release.source_version ~ '[:]cells-[0-9]+$'
       OR release.source_version ~ '^[^:]+:[0-9]{8}-[0-9]{8}:.'
),
walk_release AS (
    SELECT
        historical_release.data_source_key,
        historical_release.source_release_id,
        historical_release.created_at,
        historical_release.observed_from,
        historical_release.observed_to,
        CASE
            WHEN historical_release.chunked_walk
                THEN regexp_replace(historical_release.source_version, '[:]cells-[0-9]+$', '')
            ELSE regexp_replace(historical_release.source_version, '^([^:]+:[0-9]{8}-[0-9]{8}):.*$', '\1')
        END AS walk_label,
        CASE
            WHEN historical_release.chunked_walk
                THEN (
                    SELECT array_agg(parameter_name ORDER BY parameter_name)
                    FROM jsonb_array_elements_text(
                        historical_release.query_parameters -> 'parameters'
                    ) AS parameter_name
                )
            ELSE (
                SELECT array_agg(parameter_name ORDER BY parameter_name)
                FROM unnest(
                    string_to_array(
                        replace(
                            substring(
                                historical_release.query_parameters ->> 'request_url'
                                FROM 'parameters=([^&]*)'
                            ),
                            '%2C',
                            ','
                        ),
                        ','
                    )
                ) AS parameter_name
            )
        END AS walk_parameters,
        CASE
            WHEN historical_release.chunked_walk
                THEN (
                    SELECT array_agg(cell_key)
                    FROM jsonb_array_elements_text(
                        historical_release.query_parameters -> 'cell_keys'
                    ) AS cell_key
                )
            ELSE ARRAY[regexp_replace(historical_release.source_version, '^[^:]+:[0-9]{8}-[0-9]{8}:', '')]
        END AS covered_cell_keys
    FROM historical_release
),
walk_cell AS (
    SELECT
        walk_release.data_source_key,
        walk_release.walk_label,
        walk_release.walk_parameters,
        covered.cell_key,
        max(walk_release.created_at) AS last_covered_at
    FROM walk_release
    CROSS JOIN LATERAL unnest(walk_release.covered_cell_keys) AS covered(cell_key)
    GROUP BY
        walk_release.data_source_key,
        walk_release.walk_label,
        walk_release.walk_parameters,
        covered.cell_key
),
walk_cell_rollup AS (
    SELECT
        walk_cell.data_source_key,
        walk_cell.walk_label,
        walk_cell.walk_parameters,
        count(*) FILTER (WHERE cell.id IS NOT NULL) AS cells_done,
        count(*) FILTER (
            WHERE cell.id IS NOT NULL
              AND walk_cell.last_covered_at
                  >= now() - make_interval(hours => CAST(:throughput_window_hours AS integer))
        ) AS cells_in_window,
        count(*) FILTER (WHERE cell.id IS NULL) AS unresolved_cells,
        count(DISTINCT cell.grid_name) AS distinct_grid_count,
        min(cell.grid_name) AS grid_name
    FROM walk_cell
    LEFT JOIN agri.spatial_cell AS cell ON cell.cell_key = walk_cell.cell_key
    GROUP BY
        walk_cell.data_source_key,
        walk_cell.walk_label,
        walk_cell.walk_parameters
),
release_volume AS (
    SELECT
        observation.source_release_id,
        count(*) AS observed_value_count
    FROM agri.signal_observation AS observation
    GROUP BY observation.source_release_id
),
walk_release_rollup AS (
    SELECT
        walk_release.data_source_key,
        walk_release.walk_label,
        walk_release.walk_parameters,
        count(*) AS chunks_landed,
        count(*) FILTER (
            WHERE walk_release.created_at
                  >= now() - make_interval(hours => CAST(:throughput_window_hours AS integer))
        ) AS chunks_in_window,
        min(walk_release.created_at) AS started_at,
        max(walk_release.created_at) AS last_chunk_at,
        min(walk_release.observed_from) AS observed_from,
        max(walk_release.observed_to) AS observed_to,
        COALESCE(sum(release_volume.observed_value_count), 0) AS observed_value_count
    FROM walk_release
    LEFT JOIN release_volume ON release_volume.source_release_id = walk_release.source_release_id
    GROUP BY
        walk_release.data_source_key,
        walk_release.walk_label,
        walk_release.walk_parameters
),
grid_size AS (
    SELECT
        spatial_cell.grid_name,
        count(*) AS target_cells
    FROM agri.spatial_cell AS spatial_cell
    GROUP BY spatial_cell.grid_name
)
SELECT
    walk_release_rollup.data_source_key,
    walk_release_rollup.walk_label,
    walk_release_rollup.walk_parameters AS parameters,
    walk_release_rollup.observed_from,
    walk_release_rollup.observed_to,
    walk_release_rollup.chunks_landed,
    walk_release_rollup.chunks_in_window,
    walk_release_rollup.observed_value_count,
    walk_release_rollup.started_at,
    walk_release_rollup.last_chunk_at,
    COALESCE(walk_cell_rollup.cells_done, 0) AS cells_done,
    COALESCE(walk_cell_rollup.cells_in_window, 0) AS cells_in_window,
    COALESCE(walk_cell_rollup.unresolved_cells, 0) AS unresolved_cells,
    grid_size.grid_name,
    grid_size.target_cells
FROM walk_release_rollup
LEFT JOIN walk_cell_rollup
       ON walk_cell_rollup.data_source_key = walk_release_rollup.data_source_key
      AND walk_cell_rollup.walk_label = walk_release_rollup.walk_label
      AND walk_cell_rollup.walk_parameters IS NOT DISTINCT FROM walk_release_rollup.walk_parameters
LEFT JOIN grid_size
       ON grid_size.grid_name = walk_cell_rollup.grid_name
      AND walk_cell_rollup.distinct_grid_count = 1
ORDER BY
    walk_release_rollup.data_source_key,
    walk_release_rollup.walk_label,
    walk_release_rollup.walk_parameters
