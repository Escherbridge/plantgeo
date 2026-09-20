---
type: runbook
status: active
updated_on: 2026-09-20
---

# Current operating runbook

Pruned 2026-09-18 for context cost: deployed-session narratives and closed handoffs live in git
history (`git log -- conductor/RUNBOOK.md`, last full copy at `5c8c34c4`) and in the evidence files
linked below. Only open state, standing rules and pointers stay here.

## Directive

Environmental payloads write directly to governed Parquet and read only from the Parquet serving plane. PostgreSQL environmental observation tables, materialized views, archive readers, writers, migrations, and fallback options are retired. Missing or incomplete Parquet coverage must produce an explicit unavailable or governed-absence response and a bounded source-direct repair task.

PostgreSQL remains for transactional application data, community interventions, operational job state, and approved small reference lookups such as species profiles.

## Priority (owner, 2026-09-19) — data completeness is #1

> "We need to get our data right. That should be our #1 priority: solid data layers and layer
> serving, ingestion to date every day. That should be perfect and everything else will naturally
> fall into place."

Every other track below is subordinate to this until a lane census shows no unexplained holes.
Acceptance evidence, mobile, multiscale and release verdicts wait; they are downstream of a corpus
that is complete and honest about itself.

**Four owner decisions taken 2026-09-19, all binding:**

1. **"All to date, don't worry about ML yet."** A lane is current when it holds *every day the
   provider has actually published*, ingested up to the provider's edge. Do NOT block lane currency
   on the ML forecast lane, and do NOT fabricate days to reach literal today —
   `scripts/check-fabricated-observations.mjs` exists to prevent exactly that. Provider edges as
   measured: climate ~5 d, soil ~9 d, vegetation ~7 d.
2. **Full backfill to each lane's earliest claimed day.** The holes are real and large —
   `water-gauges` 11,594 gap days against 1,545 published (1990-09-30 returns 2 rows; 2005 and 2018
   sample days return `day_not_written`), `weather-observations` 1,709, `burn-severity` 2,079 with
   only 7 published days. Re-ingest from source rather than narrowing the claimed range. Where the
   provider archive genuinely cannot serve a day, write an explicit governed absence — a lane must
   never claim depth it does not have.
3. **Arm the NDVI promotion lane, with pre-checks first.** Confirm `agri.spatial_cell` holds the
   `sentinel2-ndvi-0p25deg` cells after the 2026-09-09 rebuild-from-empty, settle the stale
   `2026-09-19T02:25:00+00:00` work item (retry_wait, attempt 2/5), then arm and watch one turn. This
   lane has failed activation twice in production on exactly this class of missing precondition; both
   rolled back cleanly.
4. **Fold the wave-12 review debt into the data work** as its first commit rather than running
   another full fix-wave cycle.

### The measured corpus, 2026-09-19

Full census at `.omc/ultrapilot-20260918/ML-DATA-READINESS-20260919.md` (gitignored). Warehouse holds
1,252,681 objects / 26.46 GB. Twenty-three lanes carry multi-year history — `climate-field-dew-point`
to **1984** (15,598 days), `fire-detections` to **2000** (8,383 days), climate/soil families to
2022 (~1,600 days each). **The old "only vegetation has depth, everything else starts 2026-08-02"
claim is false and retired.**

Three structural facts that shape any backfill or feature work:

- **Day counts overstate the corpus.** `water-gauges` deep history is largely fictional (above);
  `fire-detections` early years are 4 rows (2000) / 6 (2010) / 8 (2020) vs 148 (2026); NDVI is
  irregularly sampled by cloud masking (six sampled days gave 169/146/36/539/136/73 cells).
- **Only two distinct native resolutions were measured in the sampled dense families.** The
  2026-09-19 availability census reported identical day counts at all four rungs, but that is not
  proof of physical rung equality: the 2026-09-20 audit found 25 sensor days with z13 only and
  legacy count-only markers. Climate is 1.0° (397 cells) and soil 0.25° (1,470 cells); a cross-lane
  matrix still needs an explicit regridding step.
- **The HTTP window route truncates silently.** `MAX_WINDOW_DAYS = 31`, `WINDOW_ROW_BUDGET = 120_000`,
  and **no cursor or offset on any route** — a measured `water-gauges` window returned exactly 120,000
  rows with 18 of 31 days dropped, indistinguishable from a complete read. Bulk work reads the object
  store with DuckDB, filtered to availability-blessed days. `release_series` lanes (drought,
  burn-severity) return `day_not_written` from `/day`; `/release` is their reader.

Two lanes publish nothing: `soil-survey` (never published) and `climate-field-shortwave-radiation`
(withheld `availability_stale` on the NASA POWER provider regression, `ALLSKY_SFC_SW_DWN = -999`
from 2026-07-01 — probe POWER before touching it).

Live refresh on 2026-09-20: dense climate/soil families advanced one day and remain gapless. Open
non-ML debt is RH 56 historical days; vegetation `2026-09-01..05`; sensors 27 days; water 11,594;
weather observations 1,709; burn severity 2,079; four static lanes without immutable availability;
and the two withheld/unpublished lanes above. Direct physical audits and the repair order are pinned
in `tracks/gapless_parquet_publication_20260901/evidence/lane-gap-census-20260920.md`.

### Manual per-lane checkpoint (owner, 2026-09-20)

The broad cross-lane repair wave is stopped. Further completeness work is manually driven one lane
at a time through physical census, bounded repair, availability publication, serving proof and a
lane-specific runbook update. The last fully checked and live revision before this checkpoint is
`8eb89e77`: main, Parquet API, executor and Martin all reached Railway `SUCCESS`; their public
readiness checks passed. The next repository checkpoint is intentionally pushed without a new
format/lint/type/test sweep or quality-receipt refresh at the owner's direction, so it is source
handoff evidence, **not** a release verdict or proof that the Docker quality gate will admit it.

Facts preserved for the lane sessions:

- `signal` physically holds 1,344 selectable observed days from `2022-04-30..2026-08-31`, but the
  public `2026-08-06` read refuses with `availability_unpublished`. Its dry-run bootstrap found
  5,376 rung rows and excluded 241 days: 222 partial ladders (`2025-12-28..2026-08-06`) plus 19 days
  with absence markers on only a subset of rungs (`2026-08-07..25`). No signal pointer was advanced.
- Sensors `2026-07-30..2026-08-23` has 25 repairable z13-only days (75,113 base rows); vegetation
  `2026-09-01` and water-gauges `2026-09-06` each need only a legacy z13 receipt upgrade. These were
  dry-run proofs only; no production ladder or availability write was applied.
- Vegetation `2026-09-02..05` and sensors `2026-09-10..11` have no z13 source parts and therefore
  require source-level ingestion or an honestly evidenced governed absence. Sensors `2026-07-29`
  already holds four-rung absence markers and requires a source/governance decision, not ladder
  derivation.
- The checkpoint introduces a distinct complete-history-floor contract and permits the observed
  `signal` lane to enter the agri availability bootstrap while continuing to exclude foreign
  forecast publishers. Each lane session must re-read and validate that contract before using it;
  the interrupted cross-lane pass did not establish a release verdict.

## Outstanding work

| Area | Owning track | Next proof |
| --- | --- | --- |
| Repository and schema boundary | [Environmental Parquet serving](tracks/environmental_parquet_serving_20260912/plan.md) | Zero environmental PostgreSQL readers, writers, schema objects, migrations, compatibility commands, or fallback flags; clean bootstrap and relation census. |
| Historical and forward coverage | [Gapless publication](tracks/gapless_parquet_publication_20260901/plan.md) | Every owed day is immutable data or a governed absence; repair owners and three consecutive scheduled advances are recorded. |
| Slider, API, and agent reads | [Environmental Parquet serving](tracks/environmental_parquet_serving_20260912/spec.md) | Cold and warm traces for selected day, viewport, supported zoom, spatial neighbours, temporal neighbours, missingness, and source ceilings. |
| Multiscale rendering | [Multiscale surfaces](tracks/multiscale_polygon_surface_20260901/plan.md) | Live conservation, continuity, readability, hover, performance, and mobile evidence at every required rung. |
| Weather observations | [Platform QA](tracks/platform_experience_qa_20260911/plan.md) | Reconcile the incomplete selected day against availability, then capture populated desktop and mobile behavior. |
| Weather forecast | **Moved to the ML service 2026-09-19** ([PlantGeo ML service](tracks/plantgeo_ml_service_20260918/plan.md)) | Owner decision: projections leave agri. The `weather-forecast` slug, the partition-kind question and the NWP product are the ML service's; agri lanes stay observed-only and admit no provider projection. Design survives at `.omc/ultrapilot-20260918/W8-E-PLAN.md` and the probe captures at `.omc/research/forecast-s3-probe-20260919/`; the deleted code is in the tree at `c922509d` (landed as `e66dbc36`, `c9c5256c`). The two forecast tracks here are stale and belong to that session to retire or re-point. |
| Botanical profiles | [Species profile lookup](tracks/botanical_species_profile_lookup_20260911/plan.md) | Inspect the authorized Railway lookup, admit provenance-bound growth and plant-composition sources, publish immutable profiles, and validate agent/API/MCP use. |
| Herbaria specimens | [PNW Herbaria admission](tracks/pnw_herbaria_source_admission_20260911/plan.md) | UBC v16.43 live as generation `956c0be7…` and **admitted** by owner decision 2026-09-19. Remaining: field-map reconciliation against the raw `occurrence.txt` row count, and the v16.42/v16.43 native-ID comparison. WTU deferred (one transfer at a time). |
| Production release | [Production acceptance](tracks/parquet_production_acceptance_20260901/plan.md) | Cross-layer browser, freshness, schedule burn-in, conservation, rollback, and release verdict after upstream gates pass. |
| ML and Monte Carlo runtime | [PlantGeo ML service](tracks/plantgeo_ml_service_20260918/plan.md) | Phase 1 push: `services/plantgeo-ml-service/` skeleton answers `/ready` on Railway and agri-data-service builds green with no `method/ml`, `method/monte_carlo`, or ML execution lane. ML work is owned by that track and its own `services/plantgeo-ml-service/RUNBOOK.md`; nothing ML-related is recorded here. |
| Intervention drawing & draft/proposed overlay | [Intervention drawing visibility](tracks/intervention_drawing_visibility_20260912/plan.md) | Draft/proposed overlay only; "published interventions become visible" is a separate bug gated on the publish-path fix in [Community engagement completion](tracks/community_engagement_completion_20260805/), not on this track. The contribution queue already calls `publishContribution` (sets `status: published`), so revalidate end to end before treating it as unimplemented. |

## Operating sequence

1. Choose one layer and freeze its source, day horizon, resolutions, current publication generation, and owning schedule.
2. Read the physical Parquet objects, completion markers, and availability entry independently. Do not infer one from another.
3. If coverage is missing, create bounded repair work against the original source. Preserve source identity, request bounds, and checksums.
4. Publish all required rungs and completion receipts before advancing availability.
5. Verify the public selected-day reader, map rendering, and agent tool against the same generation. Exercise populated, absent, unavailable, and source-ceiling responses.
6. Record the evidence in the owning active track and update its metadata and this runbook only when the outstanding state changes.

### Release policy (owner, 2026-09-15)

Incremental pushes and live-site QA are authorized; there are no active users. Verified checkpoints
follow `main` → Railway build → migration readiness → traffic. Each checkpoint records its
commit/deployment identity, build result and live QA evidence in
`tracks/platform_experience_qa_20260911/evidence/release-checkpoint-<date>-<sha>.md`. A push is not
green until every changed platform service reports `SUCCESS`; an unchanged service may report
`SKIPPED` only when the checkpoint records its prior serving revision and proves that revision is
compatible with the change. Record the status of all four platform services (plantgeo-main,
plantgeo-parquet-api, plantgeo-job-executor, plantgeo-martin). A fifth service,
**plantgeo-ml** (the Railway service name; the directory is `services/plantgeo-ml-service/`), exists
in the same project as of 2026-09-19. It is owned by the ML session, its watch pattern covers only
its own directory — the wave-9 push SKIPPED it rather than rebuilding it — and its result is not
this runbook's gate. the Python `QUALITY_RECEIPT.json` is digested from the
git index and refused by the image build when stale (archive-verify before every push). Every web
sweep runs `check:data-boundary`; it rejects any bare URL in `src/**`, comments included. Source
admission, governed publication and real-human acceptance are not waived. Production mutations
(pointer advances, lane activations, breaker releases) wait for an explicit owner go.

**Standing lesson (Sessions 20–22):** every adversarial review of a pushed range returned
CHANGES-REQUIRED, always with the same shape — a rule true in prose and broken by a mechanism a few
files away — and none surfaced in a green sweep. Authoring, verification and review stay three
separate lanes; review every pushed range, fix in the next wave.

**Session 23 deployment recovery (2026-09-20).** Wave-12 review debt, availability reconciliation,
locked ladder repair, isolated snapshot release gates and the verified Python receipt landed in
`1ed49d3a`, `df0abc3c`, `f3402768`, `316a62ed` and `e20e044f`. A later broad checkpoint
`c02f7e7b` accidentally restored the retired agri forecast/PostgreSQL surface and failed the main,
Parquet API and executor builds. `fae95a94` removed that restoration, repaired the region test mock,
and reached `SUCCESS` on all four non-ML services; live readiness probes passed. Full evidence:
`tracks/platform_experience_qa_20260911/evidence/release-checkpoint-20260920-fae95a94.md`.

## Deployed state and carried follow-ups (Sessions 1–21, 2026-09-14 → 2026-09-18)

Evidence: `tracks/platform_experience_qa_20260911/evidence/runbook-session{,2..21}-2026091{4,5,8}.md`,
`check-receipt-*.json`, `defects.md`, `task-ledger.md`,
[progress rollup](tracks/platform_experience_qa_20260911/evidence/progress-rollup-20260915.md)
(33/218 owned checklist items, 54/333 with declared scope; literal plan-status counts). The
220-case QA matrix has **no whole-case signoffs**; every session below promoted no case.

Landed and deployed: workspace/social/drawing lifecycle fixes; renderer admission against a parsed
style with a once-per-map `style.load` listener for every environmental renderer (`SoilSurveyLayer`
excluded, sole consumer of the shared hook), live-verified on `a112a754`; sensors, weather-observations
and water-gauges reconcile a disproven absence and exit 1 only when no day wrote; shortwave lag 75→6
with a 429 pause series; fire-perimeters repairs invalid geometry and is on the allow-list (51
perimeters repaired, stall closed 2026-09-18); bounded source-direct gap repair authored every six
hours; child stdout/stderr into the job ledger; About-page claims traced or removed; `landfire.ts`
deleted; land-context enterable by a bare canvas click with server-side WKB decode; climate walk
steps past one `source_unsettled` frontier day (`CLIMATE_UNSETTLED_FRONTIER_SKIPS = 1`); vegetation-type
p1a (`GridAggregation.key_columns_by_tier`) and p1b (latitude-band folding with exact integer band
membership — `round(lat / base) // cells_per_z5`, never IEEE division) deployed as `d98a2686`.

Carried, still open:

- **Shortwave withheld `availability_stale`** — NASA POWER regressed provider-side 2026-09-18
  (`ALLSKY_SFC_SW_DWN = -999` from 2026-07-01 on); no lane change owed, probe POWER before touching
  it (memory `plantgeo-power-solar-regressed-2026-09-18`). The repair path refuses to author for a
  withheld lane — recorded design gap. Frontier jitter deeper than lag+1 is a cross-turn follow-up.
- **Daily lanes sit at provider edges** (climate 5 d, soil 9 d, vegetation 7 d); reaching today
  needs the forecast lane, not ingest changes.
- **Process-start breaker release ships disabled**; the sensors breaker supersession was applied by
  CLI against run `def58693`.
- **`ClimateFieldLayer` admission cleanup** reads ids from the props ref after React updated it;
  inert only because the parent keys `ClimateSignalLayer` on `signal`, and nothing asserts that key.
  SoilField's owned-id list is stated in two places; `hasParsedStyle`'s catch can turn a failure into
  never admitting. Listener registration order after a basemap swap is proven live for the occurrence
  renderers only.
- **Herbaria cannot be the vegetation-type path** (92 % of UBC has no coordinates); LANDFIRE EVT lane
  plan at `.omc/research/runbook-20260915-vegetation-type/PLAN.md` §8 (owner grain/row-cap/charter
  decisions).
- **Sensor refresh is operator-held**; legacy indexed absences remain source-unproven; incomplete
  SSURGO parts withheld; the date summary once assigned never-published SSURGO a selected date
  (fix owed). Dormant request voting needs an owning contract.
- Mobile visual continuation, accessibility, model-stream, all-layer slider, authenticated whole-case
  and physical-device acceptance all remain open.
- The repository's `playwright.config.ts` boots `npm run dev` and must never be used by a browser
  lane; browser lanes use a standalone config pinned to the deployed site.

## Session 22 — styleguide hardening, readability swarm, federation base, ultrapilot run (2026-09-18)

Pushes, all four services SUCCESS with checkpoint files in
`tracks/platform_experience_qa_20260911/evidence/release-checkpoint-20260918-<sha>.md` (and
`…-20260919-5c8c34c4.md`): `f90b3044` styleguides · `4592e868`/`64a586e1` readability swarm
(plantgeo-main build failed `check:data-boundary` on a comment URL, ~16 min of version skew) ·
`8451ebcf` hotfix (also carried eight staged dead-module deletions) · `7f2d8f69` region manifest ·
`5325e600` botanical pointer + proxy · `9f5f936a` wave 2 · `2271e394` wave 3 · `37963657` wave 4 ·
`0320a745` wave 5 · `bf25cd16` wave 6 · `5c8c34c4` wave 7. Run artefacts (gitignored):
`.omc/ultrapilot-20260918/{BACKLOG.md (N1–N40), W*-A.md, VERIFIER-W*.md, STYLE-REVIEW-W1..6.md}`;
swarm logs `.omc/swarm-readability-20260918/`. Owner rulings in memory
`plantgeo-federation-styleguide-decisions`, `plantgeo-owner-decisions-2026-09-18`.

**What is live now.**

- Styleguides: `conductor/code_styleguides/federation.md` (one typed region manifest is the only home
  for footprint literals; regional sources bind behind a per-layer source protocol with declared
  `coverage`; module size is soft guidance), pillar 5 portability in `engineering-principles.md`,
  readability/portability sections in the language guides, lane contract §1b.
- Region manifest in both trees: `foundation/region/pnw.json` (+ `manifest.py`, `load_region()` lazy,
  `PLANTGEO_REGION` per call — never a module-level snapshot, memory
  `plantgeo-manifest-moves-must-be-lazy`) and `src/lib/region/pnw.ts` (`getRegion()`), parity-tested;
  required `platform_layers` vocabulary (14 slugs incl. `land-context`); `default_camera_envelope`,
  `sub_envelopes`, `PNW_ADMIN_CODES as const`. Guards: `tests/test_region_literal_contract.py`,
  `src/__tests__/region/footprint-literals.test.ts` (`KNOWN_OFFENDERS`: `OfflinePanel.tsx:52` — a
  different, product-owned box; non-PNW `NAMED_COVERAGE_REGIONS` rows).
- Per-layer source `Protocol`s for drought/burn-severity/soil-survey (`pipeline/direct/*/source_protocol.py`,
  read-only members, typed payloads `DroughtReleasePayload.drought_intensity_class` /
  `BurnSeverityRecordPayload`), sources at `drought/usdm.py` and `burn_severity/mtbs.py` (old `source.py`
  = shims in `DEPRECATED_ALIASES.md`, delete next release), typed per-layer `SourceRegistry` in
  `pipeline/source_bindings.py` (duplicate-slug refusal, "found under another layer" error), and
  `assert_region_bindings_are_servable` as the first statement of `create_app`. `soil-survey` has no
  producer; `pipeline/direct/soil/` is the ERA5 soil *field* writer.
- `/api/v1/parquet/coverage` carries additive `layer_bindings` (14 rows; schema version stays 3 —
  additive optional keys both sides tolerate ship without a bump); ONE web rule
  `layerBindingInRegion(capabilities, slug) → bound | unbound | not_federated` (payload row wins,
  else compiled manifest); `useLayerVisibility` disables unbound toggles; agent tools refuse with
  `_region_absence`; land-context has **no Parquet lane** and answers the typed
  `source_unbound_for_region` on all five reader paths, with an automatic viewport read
  (`useLandContextViewportBoundaries`, refuses above 1 sq degree) that issues no request in PNW.
- Botanical: `selectFinestAdmittingRungResult` (rung by zoom band + bbox area; `servingRung` is a
  **required** response field — an old route answering a new bundle is `contract_mismatch`), Next.js
  proxy `botanical-occurrences-client.ts`, `LayerManager` mounts the lanes; served through the
  **legacy `current.json` bridge** (`pointer_kind = legacy_current_json`, generation `956c0be7…`)
  until the `_LATEST.json` pointer advance lands.
- NDVI governed-plane promotion: `execution/vegetation_partition_promotion.py` keyed per day-partition
  content SHA, consults the availability index first, `PartitionNotWrittenError` vs
  `ConcurrentPrunePartitionError`, statuses `completed` (exit 0) / `waiting_for_writer` (exit 0, logs
  once) / `all_days_absent` (non-zero); lane `vegetation-ndvi-governed-plane-promotion` at `25 * * * *`,
  **shadow** until activated.
- Splits (every original re-exports its public names): `parquet-trpc-readers/` barrel, `routers/teams/`,
  `availability_*`, `snapshot_*`, `gap_fill_*`, `job_executor_service.py` → `lane_specs`/`lane_scheduling`/
  `turn_reports`, `LayerManager.tsx` → `layer-manager/*` (1,727 → 1,334 lines). Real bug fixed:
  botanical `support.py` binned cells with IEEE division (memory `polars-division-is-frame-length-dependent`).

**Wave 8 (2026-09-19, worktrees cut from `5c8c34c4`, one sweep, one push).** Production
mutations under owner go: the botanical `_LATEST.json` pointer was **advanced** to `956c0be7`
(02:45Z; `pointer_kind=latest_v1` on the data API and the site proxy —
`tracks/pnw_herbaria_source_admission_20260911/evidence/pointer-advance-20260919.md`); the NDVI
promotion lane was **activated and rolled back** within six minutes because its first tick raised
`ValueError` from `settled_through`, which still read the frozen Postgres `agri.vegetation` table
(`tracks/gapless_parquet_publication_20260901/evidence/ndvi-promotion-activation-20260919.md`).
Code: W8-A deleted the legacy `current.json` bridge (`latest_v1` is the only pointer kind; a legacy
value is `contract_mismatch`; the publisher's `current.json` write stays until its own tests move).
W8-B closed N8/N10/N11/N12/N40 (`regionLayerSlug` on the layer registry — reusing `warehouseLayerName`
would have shown a false slider badge; both `source.py` shims deleted; `LandContextPanel.partialCoverage`
reads `coverageNotices`). W8-C: a second manifest `kenya-highlands` in both trees, resolvers became
slug registries (`load_region()` / `getRegion()` cache per slug, unknown slug throws), and the proof
boots `create_app()` with drought/burn-severity/soil-survey/land-context `unbound` and every unbound
agent surface refusing — no data, tiles, lanes or schedules for that footprint. W8-D: the proxy lane is
the only detail-band botanical read; GBIF shares it; `botanical-viewport-read` is the one surviving
fault caption at that band and is now gated for GBIF-only viewers. W8-F: `default_promotion_days`
takes the vegetation availability index and returns `()` when nothing is published (turn reports
`no_days_promoted`); the `pipeline.direct.vegetation.forward` import is gone. Forecast lane W8-E: superseded — see wave 9; agri no longer carries projections. 

**Still owed after wave 7:** `LandContextPanel.partialCoverage` permanently false (should read
`coverageNotices`); `servedZoomTier` constant with an unreachable arm; drought/burn records behind the
protocols still `object`-typed; `mtbsSnapshot` contract-versioned rename (layer-lanes §1b);
`ingest/mtbs.py::inline_bbox_value` extraction owed before that module moves; `coverageStatus` answers
`unknown_coverage` from its own tri-state (by design); `OfflinePanel.tsx:52` box (product decision);
the two `source.py` shims; the `layer_bindings` schema-version yes/no from a reviewer; tripwire
`map-view-workspace.test.tsx` (`scrollIntoView` in `RegionalIntelligencePanel.tsx:687`) failed once,
flaky until twice. ~30 stale worktrees from earlier sessions remain under `.claude/worktrees` and `.tmp`.

## Botanical occurrences — open items

Live: UBC v16.43 (Canadensys IPT, not the consortium portal) published as generation `956c0be7…`
after the name-join fix (UBC declares no `dwc:scientificName`; the joined-name fallback never invents
a species); route, proxy, four agent tools and the map mount are live and independently reviewed;
`identifications` is a valid zero-row file (receipt shows `extension_row_counts: {}`), closed.
`plantgeo-parquet-api-production.up.railway.app` is the public data-API domain (originally
generated for verification, now relied on).

Admitted by owner decision 2026-09-19 (`evidence/owner-admission-decision-20260919.md`; the ledger
cites that decision, not the 2026-09-13 acquisition one, and names what is still unchecked). The map
keeps its provisional caveat because that text is still true of the data — removing a user-facing
consent notice is a separate owner call. Open, engineering: field-map reconciliation against the raw
`occurrence.txt` row count; v16.42 vs v16.43 native-ID comparison (plan A2); unwired `limit`/`cursor`
pagination; publish-time rights-URI guard. Local
quarantine of the raw archive: `C:/Users/atooz/plantgeo-quarantine/botanical_occurrences/ubc-vascular-v16.43.zip`.
`railway run --service <name> -- <cmd>` executes locally with env injected, so `*.railway.internal`
never resolves through it.

## Session 22 wave 9 — review fixes, region leak closed, NDVI blocker found (2026-09-19)

Wave 8's adversarial review (`.omc/ultrapilot-20260918/STYLE-REVIEW-W8.md`) returned CHANGES-REQUIRED
with 3 BLOCKER / 7 SHOULD-FIX; this wave fixes them and carries three owner decisions.

**Owner decisions 2026-09-19** (memory `plantgeo-owner-decisions-2026-09-18`, addendum): projections
leave agri entirely — the `weather-forecast` slug, the partition-kind question and the whole NWP
product belong to the ML service, agri lanes stay observed-only, and the two packages agri had
pushed are deleted at the ML session's own request (they lift from the tree at `c922509d`; the code
itself landed in `e66dbc36` and `c9c5256c`). UBC v16.43 is **admitted** on the owner's 2026-09-19
authority, cited as that decision and not inferred from the 2026-09-13 acquisition one, with the
field-map reconciliation and the v16.42/v16.43 native-ID comparison named as still open inside the
entry; the map keeps its provisional caveat because that text is still true of the data, and
removing it is a separate owner call. Stale `.tmp` worktrees are assessed and salvaged, not deleted.

**B1 — land-context served pilot-state semantics in every region.** `layerBindingInRegion` gated one
map hook; four server surfaces walked past it, and a non-PNW area of interest came back
`budgetExceeded("outside_pilot_states")` — a refusal that tells the caller to ask smaller. One server
seam (`src/lib/server/services/land-context/region-binding.ts`) now asks the single binding rule, all
five readers gate on it and answer `source_unbound_for_region`, the tRPC enum became a per-parse
refine against the selected manifest, and the agent tools build their descriptions and enums per
catalogue load and refuse before parsing arguments. Tools stay **registered** in every region on
purpose: a vocabulary that changed per region would make the agent deny a surface the platform has.
Where a PNW-derived type remains the reason is storage, not reachability, and a test fails the moment
any manifest binds a land-context source.

**B3 — the botanical release-set pin came from the disabled lane.** A disabled react-query observer
still serves the previous key's answer (`placeholderData: KEEP_PREVIOUS_WHILE_PANNING`), so at the
detail band the pin could name the aggregate answer a wide viewport had landed. The zoom band is the
discriminator now, not an undefined check; when the band's own lane has no answer the pin is written
`null` rather than left stale.

**B2 dissolved** rather than being fixed: it objected to agri writing projections under
`kind=observed`, and agri no longer writes projections.

**The NDVI promotion lane: two production rollbacks, and the first diagnosis was wrong.** Armed
02:40Z and 03:48Z on 2026-09-19, rolled back both times; nothing was written either time. The failure
was never in day selection — `default_promotion_days` correctly chose 2026-09-12 from an index of
1,219 published days — it was `_corpus_digest` in `execution/vegetation_ndvi_plane.py` reading the
frozen Postgres `agri.vegetation` table, one layer below the code wave 8 had patched. The tell was in
the original log: it named 2026-09-12, never today. Evidence corrected in place at
`tracks/gapless_parquet_publication_20260901/evidence/ndvi-promotion-activation-20260919.md`.

Fixed here: the whole-corpus digest is **deleted** with five SQL files (its sibling verb had no caller
at all), registration works from the partition's own cells, and its checksum is byte-identical to the
promoter's per-day SHA so the release, the receipt and the availability generation key are one value.
The promoter's ceiling now comes from `selectable_days()` — the §4a rung intersection, not the base
rung — with a staleness bound of two publication windows read from the registry, because "latest
equals latest" is not a freshness proof. Five terminal statuses with a stated precedence; a
registration refusal dominates and exits non-zero rather than letting a mixed turn report success.

**Found while joining those two halves: nothing on that path ever committed.** The session context
rolls back on exit and no caller committed, so a turn would have registered the release, written a
durable object-store receipt, rolled Postgres back, and reported `unchanged` forever against an empty
plane. Now commits per promoted day before that day's receipt — per day, not per turn, because the
advisory lock is transaction-scoped.

**Re-activation preconditions (do not arm the lane until both hold):** `agri.spatial_cell` must hold
the `sentinel2-ndvi-0p25deg` cells — production Postgres was rebuilt from empty on 2026-09-09 and
this is unverified; if empty the turn now refuses by name. And the stale work item for shard
`2026-09-19T02:25:00+00:00` sits in `retry_wait` at attempt 2 of 5 and replays the instant the lane is
armed, so its failure would be misread as a fresh one — settle it first. A 2026-09-12 ceiling goes
stale on 2026-09-27, after which only an operator `--day` turn can promote it.

**Salvage from the 2026-09-13 freshness fan-out.** Every branch in it sat on a commit already in
`main`; the work was uncommitted working-tree state, and `freshness-integrated-20260914` was the union
of the other nine. Nine trees are now preserved as WIP commits on their own branches so a stray
`git checkout .` cannot erase them. Ported: drought `--target-day` (the config field existed and the
flag was never wired) and weather-observations source checkpoints written **before** the first write,
since that lane has no archive to re-read. Held back for review: a turn report that counts publication
debt rather than only refused days — it can make a lane with a standing quarantine permanently
incomplete, which is a platform-wide health-signal change. Inventory and disposal list in
`.omc/ultrapilot-20260918/WORKTREE-SALVAGE-20260919.md`; nothing was deleted.

**Also landed:** region identity (`region_slug`, `region_display_name`) on the coverage payload as
additive keys, with a typed `region_identity_mismatch` that withholds Parquet rows when the two trees
disagree — the Python and web region env vars are independent and a split was previously undetectable;
module-scope region reads are now blocked by a guard in each tree, each with a self-test, closing the
trap the previous wave's own comment claimed to have fixed.

## Session 22 wave 10 — review fixes, and a checkpoint that erased what it protected (2026-09-19)

Deployed `3a548034`, all four platform services SUCCESS, gate closed 13:19:10Z; checkpoint
`tracks/platform_experience_qa_20260911/evidence/release-checkpoint-20260919-3a548034.md`. Wave 9's
review (`.omc/ultrapilot-20260918/STYLE-REVIEW-W9.md`) returned CHANGES-REQUIRED with both blockers
inside wave 9's own fixes.

**The promotion staleness bound was measuring the wrong distance.** It compared the ceiling against
*today*, but the registered `publication_lag_days=7` is a MEASURED MEDIAN gap between usable
observation days (cloud screening removes scenes), so a healthy lane already sits a full window behind
before anything is wrong — the two-window bound left one median gap of slack, and a cloudy fortnight
would have refused the turn before evaluating any day. With a one-day default window that day was then
never revisited: the gate manufactured the hole it detects. It now counts **missed publication
opportunities against the lane's provider frontier**: stale means the ceiling is at least three
windows behind the frontier, so a 16-day gap is healthy and 21 days is dead. The verdict is applied
*after* the window, so a stale turn still promotes its ceiling day and re-states a finished report —
a refusal can never consume a day.

**A window wider than one day was still promoting base-rung-only days.** The §4a servable intersection
gated only the ceiling while every other day was classified by its base-rung verdict. It now binds
every day, checked last so an indexed `governed_absence` keeps its own reason instead of being
relabelled. Also found: `failed` was outside the status vocabulary — a hand-written `return 1` that
the status-partition test excluded, so that test proved a property of a set missing the one status
that mattered. Six statuses now, unknown still fails closed.

**Wave 8's pin blocker was relocated, not closed.** Wave 9 scoped the retained tRPC frame by zoom band,
but a disabled observer still serves its previous answer at the *aggregate* band with both toggles off.
The predicate is now enablement, which subsumes the band, and all nine downstream consumers funnel
through one binding rather than two half-predicates.

**The weather checkpoint was erasing what it protected.** The source checkpoint key is
`(provider, support_sha, day, request_url)` with no instant, and a write refreshes a stable key on a
newer `retrieved_at`. Wiring the archive-less weather-observations lane into it (wave 9 salvage) meant
**every hourly poll overwrote the retained bodies of the bucket the previous poll had failed to
write**. Recovery now runs before retention on every turn and folds recovered readings into the poll's
own tables on the published grain; `--recover-day` repairs a named day with no source request.
Retention failure deliberately does NOT exit 1 — exit 1 is this lane's breaker, and discarding rows
already in memory because their backup failed destroys what the backup protects; the debt reaches the
turn report instead. **Standing trap for any lane that cannot re-fetch its source: put the instant in
the retained-body key, or read before you refresh.** Climate and soil escape it only because they can
re-request an archive.

Drought `--target-day` now forces the republication it is named for (safe: a failed attempt retracts
the completion marker only at `part-0`, so the published day is untouched), and a governed-absent day
no longer makes the turn raise — `_pending_weeks` re-lists a recent absence by design, and the code
read that guaranteed re-listing as an unfilled release. That one was reachable on the ordinary backlog
walk, not just behind the new flag.

**Region identity is now enforced on row reads**, not only on the slider axis: the three Parquet row
readers and the botanical client assert the served region before reading and throw a typed
`region_identity_mismatch` (503). Production shows zero of them, which is the correct outcome for a
correctly configured deployment.

**Residuals, reported not patched:** the served-region identity is *learned* from a census decode
rather than re-read per row (awaiting one would put a ~28 s cold census behind an 8 s timeout), so a
serving side redeployed into another region goes undetected until the next census; a manifest with an
empty `slug` would read as "unstated" rather than as a mismatch; `regional-evidence-tools.ts` reads the
warehouse through `/agent-tools/` with no region guard (not a row read, but the surface is named);
the weather dedup keys on the wire string while the merge refuses on the parsed instant — unreachable
today, reachable if the rendering changes inside the 7-day checkpoint window; and **a stale lane now
exits 1 hourly until its work item dead-letters**, which becomes a new dead-letter source the moment
the NDVI allow-list gate is opened.

**Unproven rather than passed** (both need something a read-only pass may not do): the land-context
agent-tool refusal envelope, whose only dispatch surface is session-gated and returned 401; and the
region-mismatch refusal path itself, which requires the two region environment variables to disagree.

## Open owner items

- **Object-store credential rotation (2026-09-19).** An operations agent printed
  `OBJECT_STORE_ACCESS_KEY_ID` and `OBJECT_STORE_SECRET_ACCESS_KEY` into its own transcript during
  the botanical pointer advance before switching to an environment-only driver. Nothing left the
  machine. The owner chose to rotate later and asked to be reminded; rotate when no lane is
  mid-publish, then delete this bullet.
- **`OfflinePanel.tsx:52` download box** — a product decision, tracked as a known offender in
  `src/__tests__/region/footprint-literals.test.ts`.
- **Stale worktrees under `.tmp`** — eleven from earlier sessions hold uncommitted work. Owner asked
  for assessment and salvage, not deletion; inventory in
  `.omc/ultrapilot-20260918/WORKTREE-SALVAGE-20260919.md`.

## Recovery

- Disable the affected current schedule and preserve the last valid immutable generation and pointer.
- Re-run a bounded, idempotent source-direct request. Never restore a PostgreSQL environmental reader or writer.
- Do not advance availability until every required object and marker verifies.
- If a reader is incomplete, return an explicit Parquet unavailable response while the owning track repairs it.
- For release failures, keep the previous verified generation active and record the exact revision, request, source identity, and failed gate.

## Validation

Apply the complete change batch before the final integrated check. Run the data-boundary check, type check, lint, affected frontend and Python tests, migration/bootstrap verification, and relation census appropriate to the change. Production acceptance additionally requires cold and warm request traces, browser evidence, schedule burn-in, and an independent release verdict.
