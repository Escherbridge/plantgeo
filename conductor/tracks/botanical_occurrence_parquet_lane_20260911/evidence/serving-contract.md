---
type: track-evidence
track: botanical_occurrence_parquet_lane_20260911
status: implemented-pending-review
---

# `botanical-occurrences` serving contract (as implemented, 2026-09-12)

Product `botanical-occurrences`, temporal kind `static_lookup`. Implemented in
`src/agri_data_service/planes/botanical_occurrences.py`; transport in
`src/agri_data_service/interface/http/botanical_occurrences.py` (`GET /botanical-occurrences/query`,
not yet mounted in `app.py`).

## Pinning

Every request requires `release_set_id`. There is NO default and `current` is refused by name with
`release_not_pinned`. A reader that followed the pointer would answer two identical requests from two
different generations and report the same thing both times.

## Inputs

| Parameter | Required | Meaning |
| --- | --- | --- |
| `release_set_id` | yes | Exact published generation. `current` refused. |
| `bbox` | yes | `minLon,minLat,maxLon,maxLat`; min strictly below max. |
| `zoom` | yes | ≥ `detail_zoom_floor` (11) → detail points; below it → aggregate. |
| `taxon_concept_id` | no | Exact concept id. No name filter exists. |
| `family` | no | Exact string; a family name carries no homonym problem. |
| `collection_key` | no | Exact collection. |
| `event_start` / `event_end` | no | ISO days; OVERLAP semantics against each record's interval. |
| `spatial_quality` | no | `confirmed` (default) \| `possible` \| `all`. |
| `limit` | no | Default 500, max 2000; above → `limit_exceeded`. |
| `cursor` | no | Opaque base64 of `{offset}`; only one this plane issued. |

Zoom → rung: `z < 7` → `grid-0.25`; `7 ≤ z < 11` → `grid-0.05`; `z ≥ 11` → detail.
Bbox ceilings (square degrees): detail 4.0, `grid-0.05` 100.0, `grid-0.25` 1600.0. Over → refused
`bbox_too_large_for_zoom`, checked BEFORE the generation is opened so a refusal cannot leak what is
published. `scientific_name`, `taxon_name` and `species_name` are refused with
`name_only_taxon_filter`.

## States

`state ∈ detail | aggregate | refused | unavailable`. Exactly one, never two.

**detail** → `features[]` each with `occurrence_id`, `collection_key`, `source_record_key`,
`taxon_concept_id`, `resolution_state`, `scientific_name`, `family`,
`event_interval{start,end,precision}`, `longitude`, `latitude`, `coordinate_uncertainty_m`,
`spatial_class`, `membership`, `catalog_number`, `recorded_by`, `basis_of_record`, `rights_uri`,
`attribution_text`; plus `release_set_id`, `taxonomy_recipe_version`, `qc_policy_version`,
`support_id` (null), `truncated`, `next_cursor`,
`counts{returned, matched, withheld, nonspatial, excluded_by_qc}`.

`spatial_quality=confirmed` returns only `spatial_class == exact`; `possible` returns only
`generalized`; `all` returns both. Withheld and nonspatial records are never returned as points and
are counted at the generation level under the same non-spatial filters (they match no bbox, so a
bbox-scoped count of them would always be zero and would hide the population).

**aggregate** → `support_id` and `cells[]` of `{cell_id, geometry (GeoJSON Polygon), evaluation,
documented_taxa, record_count, event_estimate, collection_count, excluded_by_qc,
possible_only_records}`, with the same `release_set_id`/recipe fields, `truncated`, `next_cursor`.
`evaluation ∈ documented | evaluated_zero | withheld_or_generalized_only | outside_coverage |
not_evaluated`. `documented_taxa` is a count of DISTINCT concepts among confirmed records at that
rung, recomputed from the exact set and never summed from a finer rung.

**refused** → `reason ∈ release_not_pinned | release_unknown | release_incomplete |
bbox_too_large_for_zoom | name_only_taxon_filter | historical_publication_unsupported |
limit_exceeded`, plus `detail` and a note stating that nothing was read.

**unavailable** → `reason` string. No completed generation could be opened; says nothing about
content.

HTTP status mapping: caller refusals (`release_not_pinned`, `bbox_too_large_for_zoom`,
`name_only_taxon_filter`, `limit_exceeded`, `invalid_request`) → 400; publication-state refusals
(`release_incomplete`) → 409; `unavailable` → 503; otherwise 200. `Cache-Control: no-store` always.

## Agent neighbour semantics

Three tools in `src/agri_data_service/agent/botanical_occurrences.py`. Every payload carries an
`exact: {state: found|empty, count}` block AND, separately, `substitutes[]` whose every entry carries
`substitute: true`. A neighbour is returned NEXT TO the exact result, never in place of it.

- `botanical_occurrences_in_region` — bbox read, exact only.
- `botanical_occurrence_spatial_neighbours` — `distance_m` (haversine to the record point, or to the
  reported point for a generalized record; `distance_semantics` says which), the record's own
  uncertainty, and the represented support. Radius capped at 50 km.
- `botanical_occurrence_temporal_neighbours` — the requested interval, each record's OWN event
  interval, `signed_days` (negative before, positive after), `abs_days`, `overlap`. Overlap is
  reported as overlap, never as an invented exact-day match. A record with an unknown date is
  neither exact nor a neighbour.

Every payload restates `refused_claims`: abundance, cover, current occupancy, habitat suitability,
surveyed absence, historical publication state. Tool docstrings state the same refusals.

## Deviations from the briefed contract

1. `excluded_by_qc` in the detail counts is generation-scoped, not bbox-scoped, for the reason above.
2. `historical_publication_unsupported` is declared in `REFUSAL_REASONS` but is not reachable yet:
   nothing in the request surface accepts a publication date to refuse, so declaring it is the
   forward-compatible half and no code path fabricates it.
3. `release_unknown` is likewise declared but unreached — an unknown id currently answers
   `unavailable`, because from the reader's position "never published" and "not published here" are
   the same observation and only `unavailable` is honest about that.
4. Aggregate answers do not materialise `outside_coverage` cells; cells outside the declared
   envelope are simply absent from the published table and a caller querying them receives no cell.
   Reader-side synthesis of `outside_coverage` is deferred and noted.
