---
type: runbook
status: active
updated_on: 2026-09-18
---

# Current operating runbook

Pruned 2026-09-18 for context cost: deployed-session narratives and closed handoffs live in git
history (`git log -- conductor/RUNBOOK.md`, last full copy at `5c8c34c4`) and in the evidence files
linked below. Only open state, standing rules and pointers stay here.

## Directive

Environmental payloads write directly to governed Parquet and read only from the Parquet serving plane. PostgreSQL environmental observation tables, materialized views, archive readers, writers, migrations, and fallback options are retired. Missing or incomplete Parquet coverage must produce an explicit unavailable or governed-absence response and a bounded source-direct repair task.

PostgreSQL remains for transactional application data, community interventions, operational job state, and approved small reference lookups such as species profiles.

## Outstanding work

| Area | Owning track | Next proof |
| --- | --- | --- |
| Repository and schema boundary | [Environmental Parquet serving](tracks/environmental_parquet_serving_20260912/plan.md) | Zero environmental PostgreSQL readers, writers, schema objects, migrations, compatibility commands, or fallback flags; clean bootstrap and relation census. |
| Historical and forward coverage | [Gapless publication](tracks/gapless_parquet_publication_20260901/plan.md) | Every owed day is immutable data or a governed absence; repair owners and three consecutive scheduled advances are recorded. |
| Slider, API, and agent reads | [Environmental Parquet serving](tracks/environmental_parquet_serving_20260912/spec.md) | Cold and warm traces for selected day, viewport, supported zoom, spatial neighbours, temporal neighbours, missingness, and source ceilings. |
| Multiscale rendering | [Multiscale surfaces](tracks/multiscale_polygon_surface_20260901/plan.md) | Live conservation, continuity, readability, hover, performance, and mobile evidence at every required rung. |
| Weather observations | [Platform QA](tracks/platform_experience_qa_20260911/plan.md) | Reconcile the incomplete selected day against availability, then capture populated desktop and mobile behavior. |
| Weather forecast | [Forecast lane](tracks/weather_forecast_parquet_lane_20260911/plan.md) and [forecast experience](tracks/weather_forecast_experience_20260911/plan.md) | Admit Open-Meteo run-time/valid-time data to Parquet, serve fields and location forecasts, and render continuous weather, wind, hourly, and daily information beyond the observation horizon. Plan W8-E (2026-09-18): distinct slug `weather-forecast`, `kind=observed`, nature `release_series`, issue date as the partition day; `kind=forecast` stays reserved for the ML service. |
| Botanical profiles | [Species profile lookup](tracks/botanical_species_profile_lookup_20260911/plan.md) | Inspect the authorized Railway lookup, admit provenance-bound growth and plant-composition sources, publish immutable profiles, and validate agent/API/MCP use. |
| Herbaria specimens | [PNW Herbaria admission](tracks/pnw_herbaria_source_admission_20260911/plan.md) | UBC v16.43 is live (generation `956c0be7…`); see "Botanical occurrences — open items" below. WTU deferred (one transfer at a time). |
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
green until **all four services** (plantgeo-main, plantgeo-parquet-api, plantgeo-job-executor,
plantgeo-martin) report the same commit; the Python `QUALITY_RECEIPT.json` is digested from the
git index and refused by the image build when stale (archive-verify before every push). Every web
sweep runs `check:data-boundary`; it rejects any bare URL in `src/**`, comments included. Source
admission, governed publication and real-human acceptance are not waived. Production mutations
(pointer advances, lane activations, breaker releases) wait for an explicit owner go.

**Standing lesson (Sessions 20–22):** every adversarial review of a pushed range returned
CHANGES-REQUIRED, always with the same shape — a rule true in prose and broken by a mechanism a few
files away — and none surfaced in a green sweep. Authoring, verification and review stay three
separate lanes; review every pushed range, fix in the next wave.

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
`no_days_promoted`); the `pipeline.direct.vegetation.forward` import is gone. Forecast lane W8-E:
ownership agreed with the ML session (distinct slug `weather-forecast`, `kind=observed`,
`release_series`, `PartitionKind` stays two-valued, one rung ladder, `forecast_module=None`); S1
froze the 19-column schema (grain `cell_id, valid_time, variable`; `missing_reason` enum); S2 added
`ingest/weather_forecast/` (single-run endpoint, 64 MiB / 120 s bounds, 429 raises with no retry,
`u=-speed*sin(dir), v=-speed*cos(dir)`, precipitation `valid_time` shifted one hour earlier to name the
accumulation window). **Two S2 assumptions are unverified against a live probe:** the multi-location
response shape and the precipitation window direction — S3 must probe before building on them. S3–S5
(direct lane + registry entry, plane + agent tool, web reader + slider variant) are next; `lane_registry.py`
is also on the ML session's touch list, so rebase before S3. **Re-activation of the NDVI lane waits for
this push and a fresh owner go.**

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

Open, owner: `admission-decisions.json` `admitted_releases` is still `[]` while data is live — a
different ledger from Parquet; the `admission_reconciliation_note` needs an explicit owner decision
(`owner-risk-decision-20260913.md` authorized acquisition, not admission). Open, engineering:
field-map reconciliation against the raw `occurrence.txt` row count; v16.42 vs v16.43 native-ID
comparison (plan A2); unwired `limit`/`cursor` pagination; publish-time rights-URI guard. Local
quarantine of the raw archive: `C:/Users/atooz/plantgeo-quarantine/botanical_occurrences/ubc-vascular-v16.43.zip`.
`railway run --service <name> -- <cmd>` executes locally with env injected, so `*.railway.internal`
never resolves through it.

## Recovery

- Disable the affected current schedule and preserve the last valid immutable generation and pointer.
- Re-run a bounded, idempotent source-direct request. Never restore a PostgreSQL environmental reader or writer.
- Do not advance availability until every required object and marker verifies.
- If a reader is incomplete, return an explicit Parquet unavailable response while the owning track repairs it.
- For release failures, keep the previous verified generation active and record the exact revision, request, source identity, and failed gate.

## Validation

Apply the complete change batch before the final integrated check. Run the data-boundary check, type check, lint, affected frontend and Python tests, migration/bootstrap verification, and relation census appropriate to the change. Production acceptance additionally requires cold and warm request traces, browser evidence, schedule burn-in, and an independent release verdict.
