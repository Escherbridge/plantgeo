---
type: agents
scope: src/lib/server/services (soil data plane web side)
design: .omc/soil-data-plane-20260927/DESIGN.md (rev 2) §2.6, §5
contract: .omc/soil-data-plane-20260927/CONTRACT.md C2, C3, C5, C6, C7
review: .omc/soil-data-plane-20260927/REVIEW-1.md (M1-M9, m-series applied)
---

# Soil data plane: web services

This directory holds only this document. The code lives one level up (`soilgrids.ts`,
`site-brief.ts`, `site-brief-readers.ts`, and the soil paths of `regional-context.ts`,
`regional-map-evidence.ts`, `ai-prompt.ts`, `remediation-report.ts` and
`regional-measurement-facts.ts`). It sits in its own directory so the peer-owned
`services/AGENTS.md` needs no edit (PEER-REQUEST §2).

## The one rule
Every SoilGrids number is a **model estimate**. It is labelled "SoilGrids v2.0 250 m model estimate,
<depth>" everywhere: payload, brief, tool results, provenance, the map card and the report. It is
never called measured or observed, in code, prompts, labels, tests or docs (CONTRACT C9).

## §flags (CONTRACT C2; review M7)
Two independent kill switches, both off by default, parsed by one rule
(`site-brief.ts::readsFlagEnabled`): only the exact value `true`, whitespace trimmed, case-sensitive.
agri parses them identically (`agent/site_brief.py::flag_enabled`); the shared fixture
`services/agri-data-service/tests/fixtures/site_brief_reader_constants.json` pins the cases.

| SOIL_PROPERTIES_READS_ENABLED | SITE_BRIEF_ENABLED | what a regional-intelligence request does |
|---|---|---|
| off | off | **wave 2 byte for byte**: no soil read, no brief read, no `siteBrief` key, no `site_facts_provenance`, no `site_brief_query`, the wave-2 prompt text |
| on | off | one soil read (LRU-cached) on every request; `soilProperties` in the payload; its 0-5 cm pH/SOC reach `site_facts` labelled `model_estimate`; labelled prompt text; no brief read |
| off | on | the brief on base runs, its soil section `reads_disabled` and its five other sections read; a follow-up is served the cached brief, else nothing |
| on | on | the full design: one soil read shared by payload and brief; brief on base runs; follow-ups get the cached brief or the one soil read |

The soil flag also gates every other soil reader (`rasterEvidence`, the `SoilDetails` card, agri's
`soil_properties_at_point`). The agri catalogue declares that tool whatever the flags say; with the soil
flag off it answers `reads_disabled` (DESIGN §7 P-push).

## §soilgrids-reader (`soilgrids.ts::getSoilProperties`)
- **Kill switch first.** `SOIL_PROPERTIES_READS_ENABLED` off returns `reads_disabled` before the cache or
  any read, so unsetting the variable takes effect at once (P5 rollback) even with a warm cache.
- **Coverage (C5.1).** `outsideSoilReleaseCoverage` is agri's `outside_release_coverage`: outside when the
  point lies more than `r` beyond the envelope (lon margin `r / (111320 · max(cos lat, 0.01))`).
- **Read.** `GET /api/v1/parquet/release?layer=soil-properties&kind=observed&zoom=13&bbox=…&as_of=<server today>`
  through `parquet-plane-client.ts::getParquetLatestRelease`. `as_of` is always the server date.
- **Selection (C2).** The bbox half-widths are `r/110574 + 0.005` deg by `r/(111320·max(cos lat, 0.01)) + 0.005`
  deg. Keys are SW origins; the distance goes to the centre (`origin + 0.0025`), by haversine on a
  6,371,008.8 m sphere. The minimum wins, ties go to the lower latitude then the lower longitude, and
  the cell is accepted only within `r`. The distance is reported as `floor(d + 0.5)` whole metres.
  Beyond `r` the answer is `no_cell_within_radius` with `radiusM`; it is never widened silently.
- **§nearest-estimate (owner 2026-09-28, "Name the nearest estimate").** SoilGrids masks urban pixels,
  so a city click often has no centre within 1,000 m. `soilSearchRadius` makes `r` the search radius:
  the DEFAULT (omitted, or exactly 1,000 m after clamping; value equality, so a model that fills in
  the default is not strict) searches 2,000 m; any other radius is strict. It is ONE read at `r`, then
  nearest selection, so the cache key and the answer never depend on the fallback; only the labels
  do. `site-brief.ts::soilEstimateIsNearestFallback` classifies on the integer `distance_m` (> 1,000 m),
  and then every soil string names the distance with `soilDistancePhrase` ("nearest cell centre 1,340 m
  away (none within 1,000 m)"): the reader's 0-5 cm and topsoil labels, the brief section and topsoil
  labels, both soil descriptors, the C3 provenance, map evidence, and the `describeSiteBrief` soil line
  ("soil (nearest SoilGrids cell, NOT this point)"). The value is never presented as being at the
  point. The two soil descriptor seeds are empty, so a nearby cell's texture never reaches
  `literature_seed`/`site_brief_query` as this site's soil. Beyond 2,000 m: `no_cell_within_radius`
  with `radiusM: 2000` (SoilDetails says "within 2 km"). Agri mirrors it (`soil_properties.search_radius`,
  `site_brief.soil_distance_phrase`); pinned by the reader-constant fixture's `search_radius_cases`
  (including .5 rounding rows) and `distance_phrase_cases` (the 1000/1001 boundary), and the golden
  cases `urban_nearest_cell_beyond_default_radius` and `soil_boundary_1001_is_the_nearest_fallback`.
- **Integer contract (C1).** Every z13 value must be integral (`|v − round(v)| < 1e-9`) and
  non-negative. Anything else is `read_failed`: the lane is corrupt, and a value is never rounded quietly.
- **Reasons.** `lane_never_written` passes through as is. `day_not_written` and governed absence map to
  `not_published`. Agri's 409 `serving_at_capacity` maps to `serving_at_capacity`; `read_timed_out`, the
  transport timeout, our own `timeoutMs` and the caller's `signal` (the brief deadline) map to `timeout`.
  Everything else is `read_failed`. `getSoilProperties` never throws.
- **§soil-cache (review M4).** An LRU of 1,024 entries for 6 h, keyed by the 0.005 cell containing the
  QUERY point plus the search radius `r`. A neighbour cell is right for one point only, so an entry is either:
  - `own_cell`: the chosen cell IS the query cell. It is the nearest centre for every point inside it,
    so a hit re-measures the distance and serves it;
  - `candidates`: the query cell is masked (the chosen cell is a neighbour, or none lies within `r`).
    The entry keeps the read's candidate rows and bbox, and a hit re-runs `selectNearestCell` for the new
    point, exactly as a fresh read would, but only when the cached bbox holds every origin the new point
    could need (`candidatesCover`: the spherical cap `lat ± d`, `lon ± asin(sin d / cos lat)`, shifted half
    a cell south-west). Otherwise it re-reads.
  Refusals, faults and a corrupt row are never cached.
- **Compatibility.** `SoilProperties.ph/organicCarbon/nitrogen/bulkDensity/cec/ocd` stay the 0-5 cm
  physical values that `payloadSoilObservations` (peer-owned), `soil-ai-evidence.ts` and `SoilDetails`
  read. The added fields are optional on the interface only so that pre-lane literals still type. The
  reader returns `SoilGridsEstimate = Required<SoilProperties>`.

## §site-brief (`site-brief.ts`)
- **Pure builder, parity by construction (C5).** `buildSiteBrief(inputs)` takes only the integer
  `SiteBriefInputs`. Every derived number uses round half up by integer division, `(2N + D) // (2D)`,
  and is emitted as `k / 10^p`. Descriptor text renders from the integer (`58` gives "5.8"), never
  from a float. The Python twin is agri `agent/site_brief.py`. Parity is checked against
  `services/agri-data-service/tests/fixtures/site_brief_golden.json` after `canonicalJson`.
- **Literature seed (C5.4).** Descriptor seeds are joined with "; " and cut at a word boundary at 600
  characters. Weather contributes no seed.

## §site-brief-readers (`site-brief-readers.ts`)
- **One reader semantics on both paths (C5.1; review M9).** Both `assembleRegionalContext` paths build the
  brief through `siteBriefForRequest` and the same point reads, and those reads match agri's
  `agent/graph.py` readers. The constants are pinned in `site_brief_reader_constants.json`, which both
  test suites assert:
  - soil: the request's one soil read;
  - fire: the newest WFIGS perimeter or MTBS burn that CONTAINS the point (0.01 deg box, point in polygon),
    dated on or before today; the severity is the MTBS class (codes 2/3/4 or names) of that same day's
    burn, only when exactly one is recorded, never a WFIGS `severity`; plus FIRMS detections within a
    10 km circle of each detection cell's coordinate over the 30 UTC days ending today. An unpublished
    day adds nothing; a truncated day is `read_failed` (an undercount is not a count);
  - drought: the highest USDM class of the newest release's polygons containing the point, or `none`
    (`getParquetDrought`, which takes the signal);
  - weather: the nearest station within 50 km on today's day if published, else yesterday's (UTC),
    ranked by whole metres then newest reading. A reading without both temperature and humidity is
    `read_failed`: the brief never invents a value. Today published with nobody in range is
    `no_observation_within_radius`, never filled from yesterday;
  - land cover: the z13 crop-cover cell containing the point (`getParquetLatestRelease`, which takes the
    signal), dominant CDL class over integer codes with a finite positive area, ties to the lower code.
    A dominant class without a name is `read_failed`, never its numeric code. A region binding no
    crop-cover layer is `not_bound_in_region`.
  All distances use the C2 sphere (6,371,008.8 m); agri measures with DuckDB's spheroid, a sub-0.5%
  difference that can move a boundary reading, not a rule.
- **§scheduler (review M8).** `SiteBriefReadScheduler` holds at most 2 brief reads in flight on the 3-slot
  serving plane and hands each the same AbortSignal, which aborts at one shared 3,000 ms deadline so the
  transport lets go of its slot. A read not started by the deadline never starts; a slot frees when the
  underlying read settles. A read that misses the deadline is `timeout`.
- **§brief-cache (review M5).** An LRU of 256 briefs for **45 minutes**, keyed by the 0.005 cell, the UTC
  day and BOTH flags (the kill switch precedes the cache, C2). A brief is cached only when no section
  refused: every unavailable section must state a stable fact (`no_cell_within_radius`,
  `no_observation_within_radius`, `not_bound_in_region`, `outside_release_coverage`, or
  `reads_disabled`, which cannot go stale because the flags are in the key). A `timeout`,
  `serving_at_capacity`, `read_failed`, `lane_never_written` or `not_published` section is re-read
  next time. The short TTL keeps a nearest-weather reading from freezing for the day.
- **§base-run-and-follow-ups (review M6).** `route.ts` sets `isBaseRun` for the first turn or an empty
  question. Conversation history replays only the saved question and report, NEVER the brief, so a
  follow-up is not left without it: it is served the cached brief with no read, or, on a cache miss, the
  one soil read alone (its 0-5 cm values reach the prompt through `soilProperties` and `site_facts`,
  labelled `model_estimate`). A follow-up never repeats the six section reads.
- **Selection path and time.** The brief is the one deliberate exception to "no initial source read
  as-of-latest". Its sections are read on the server's today, and each carries its own date and label.
  `ai-prompt.ts::buildTemporalSection` says exactly this, but only while a soil flag is on.
- **Fail open.** A brief that cannot be built (an unexpected shape) leaves the payload without one; the
  request still answers.

## §literature-context (`site-brief.ts::withSiteBrief`, applied in `ai-prompt.ts`; review M1)
- The peer-owned `regional-analysis-workflow.ts::buildLiteratureServerContext` is NOT edited.
  `withSiteBrief(context, brief, { observations, sourceByReadId, soilProperties })` wraps its output:
  - soil keys come from `topsoil_0_30cm` (`model_estimate`, 0-30 cm);
  - `land_cover` comes from CDL (`classified_remote_sensing`);
  - fire keys come from the fire section (`measured`; same-fire severity);
  - **every remaining key** gets provenance from the reads that produced it
    (`analysis.siteFactObservations`, the source named from the evidence audit): a value matching the
    payload's 0-5 cm SoilGrids read is `model_estimate`, depth 0-5 cm, even if a warehouse read agrees;
    anything else is `measured` (land cover `classified_remote_sensing`). Electrical conductivity,
    annual precipitation and a warehouse pH therefore survive agri's provenance-aware drop;
  - a key no read produced keeps no entry, and agri drops it as `(no_provenance)` (C3), audited;
  - `site_brief_query` carries the seed.
- With neither a brief nor a soil read the context passes through untouched: the wave-2 legacy shape.
- `user_question` stays the user's words only. When none was typed, agri uses `site_brief_query` as
  `context_query` (C4).

## §raster-evidence (`regional-map-evidence.ts::rasterEvidence`)
- A soil toggle returns the PMTiles metadata and then, on a lane hit, state `model_estimate` with
  `basis: "model_estimate"`, the toggled property at the three depths, `unit`, `label`, `release_id`,
  `distance_m` and `numeric_values_available: true`.
- The state is deliberately NOT `published`: that word is in `regional-measurement-facts.ts::RECORD_STATES`
  and would mint an "Observed data" fact.
- Fallback states: `raster_release_not_published` and `outside_release_coverage` come from the catalogue;
  `no_estimate_within_radius` means no lane cell lies within `r`; `numeric_values_unavailable` covers
  any other refusal, including `reads_disabled`.
- The drawn colour (bilinear PMTiles) can differ from the number (nearest pixel), and the note says so.

## §measurement-facts
`regional-measurement-facts.ts::collectCandidates` skips any envelope or record whose `basis` (or
`properties.basis`) is `model_estimate`, whatever its `state`. That makes two guards: the state name
and the basis.

## §badge-interim-filter and §label-append (`remediation-report.ts`)
- Two peer-owned render paths ignore the source when they badge: `StrategyChips`
  (`ORIGIN_LABELS[item.evidenceOrigin]`) and the markdown `riskSummary` line. Until h7 lands
  (PEER-REQUEST §3), `soilProperties` is removed from the citation enums of remediation items and of
  `riskSummary.evidenceSources`. It remains citable only in `observations[].evidenceSource`. F2 lifts
  this after h7.
- **Label append (review M2).** The live report never cites `soilProperties`: measurement facts are on,
  so soil numbers arrive in `model_inference` statements, rationales, risk factors and the headline. So
  `labelSoilModelEstimates(report, { soilAvailable })` keys on TEXT: whenever soil reached the model
  (payload `soilProperties`, the brief's soil section, or an answered `soil_properties_at_point`), any
  statement, rationale, factor or headline with a digit and a soil property term (pH, SOC, organic
  carbon, clay, sand, silt, loam, texture, bulk density, CEC, coarse fragments, topsoil, SoilGrids) and
  without "model estimate" gets the label. Statements and rationales get `SOIL_MODEL_ESTIMATE_SENTENCE`;
  the headline and factors, too short for it, get `SOIL_MODEL_ESTIMATE_SUFFIX`. Bare "soil" is not a
  term: soil moisture and soil temperature are other sources. An observation citing `soilProperties`
  is labelled whatever the flag. Each field is trimmed to stay within its limit. Deterministic,
  idempotent, never an error.

## §soil-tool (`regional-evidence-tools.ts`)
`soil_properties_at_point` is an agri tool (C6) that reaches the model through the served catalogue.
`loadRegionalEvidenceTools` appends a label rule to its description, so the rule travels with the tool
whatever agri's text says.

## §soil-ai-evidence
`soilAiEvidence` keeps the 0-5 cm units and the legacy `method` string, which a peer test pins. It adds
`basis`, the lane `label`, the cell-centre distance, the release, and a labelled 0-30 cm topsoil summary.
