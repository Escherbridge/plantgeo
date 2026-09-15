---
type: runbook
status: active
updated_on: 2026-09-15
---

# Current operating runbook

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
| Weather forecast | [Forecast lane](tracks/weather_forecast_parquet_lane_20260911/plan.md) and [forecast experience](tracks/weather_forecast_experience_20260911/plan.md) | Admit Open-Meteo run-time/valid-time data to Parquet, serve fields and location forecasts, and render continuous weather, wind, hourly, and daily information beyond the observation horizon. |
| Botanical profiles | [Species profile lookup](tracks/botanical_species_profile_lookup_20260911/plan.md) | Inspect the authorized Railway lookup, admit provenance-bound growth and plant-composition sources, publish immutable profiles, and validate agent/API/MCP use. |
| Herbaria specimens | [PNW Herbaria admission](tracks/pnw_herbaria_source_admission_20260911/plan.md) | Owner risk decision 2026-09-13 cleared pre-acquisition gates; UBC v16.43 acquired, safety-inspected, and PUBLISHED to production Parquet (generation `956c0be7...`, superseding `0c0f3cb8...` after the name-join fix below); route and agent tools MOUNTED and live at `plantgeo-parquet-api`. See "Handoff — botanical occurrences" below for full state, what is verified vs. assumed, and the concrete next steps. WTU acquisition still deferred (one-transfer-at-a-time budget). Field-map reconciliation and the v16.42/v16.43 native-ID comparison remain open; `admitted_releases` (the governance JSON) still empty even though data is live in Parquet -- those are different ledgers, see below. |
| Production release | [Production acceptance](tracks/parquet_production_acceptance_20260901/plan.md) | Cross-layer browser, freshness, schedule burn-in, conservation, rollback, and release verdict after upstream gates pass. |
| Intervention drawing & draft/proposed overlay | [Intervention drawing visibility](tracks/intervention_drawing_visibility_20260912/plan.md) | Draft/proposed overlay only; "published interventions become visible" is a separate bug gated on the publish-path fix in [Community engagement completion](tracks/community_engagement_completion_20260805/), not on this track. |

## Operating sequence

### Current progress and incremental release policy — September 15, 2026

The [auditable checklist rollup](tracks/platform_experience_qa_20260911/evidence/progress-rollup-20260915.md)
counts 33/218 current directly owned checklist items complete (15.14%). Including the declared
workspace, requests, unified-intervention and land-context scope gives 54/333 (16.22%), with
three in progress and 276 open. Explicitly historical phases are excluded. These are literal
plan-status counts, not percentages of total effort or final acceptance: some obligations remain
in prose and some implemented work still has open verification. The 220-case formal QA matrix
has no whole-case signoffs; bounded execution variants retain their separate evidence.

The user has authorized incremental pushes and live-site QA, reporting no active users. Verified
code checkpoints may now follow the existing `main` → Railway build → migration readiness →
traffic path. Source admission, governed publication and real-human acceptance are not waived.
Each checkpoint must record its commit/deployment identity, build result and live QA evidence;
the runbook remains open while later batches continue.

Candidate 17's complete frontend release sweep passes all 193 files / 2,542 tests and 12 tooling
tests. Type and data-boundary checks pass; its scoped test-file lint and Candidate 16's full lint
are recorded distinctly in Session 11. Real local D26 publication-date and stale-moderation browser
regressions pass independent review, as does unobstructed desktop temperature/VPD rendering.
The mobile visual continuation remains failed at the local QA bridge before its first field
capture; no product-renderer conclusion is drawn from that failure.

The first [incremental release checkpoint](tracks/platform_experience_qa_20260911/evidence/release-checkpoint-20260915.md)
was pushed to `main` as `10cbd69581e979f08fd915418232df37a0730d8e`. Martin and the frontend
deployed successfully; the frontend passed its full build and readiness gates. The data API and job executor correctly refused a stale checked-in
Python quality receipt (880 old inputs versus 885 current inputs). A fresh receipt must be
produced by the required Linux check workflow and shipped before those deployments can pass.
The original failed deployments remain evidence; the earlier local Linux pass does not make
the stale production receipt valid. The new full Linux workflow has now generated that receipt:
4,494 passes, 150 skips and one expected failure, with all four quality gates and the receipt
verifier passing. The exact 885-input digest matches the rejected build's current tree.

Four bounded live desktop/mobile smoke cases now pass against the deployed frontend: health,
readiness, anonymous social pages, real weather Latest and September 6 selection/missingness,
and opening the unsent proposal/AI workspace. The first four failures are retained as a harness
analytics-guard issue; the second run explicitly excludes that exact injected analytics script.
These passes do not certify all rendered frames, authenticated workflows, model calls, or the
220-case matrix. The feed disclosure wording and early blank-frame evidence are tracked in the
[defect ledger](tracks/platform_experience_qa_20260911/evidence/defects.md).

### Active long-horizon run — September 14, 2026

The current coordinating session is `/root`, starting from `0f16e40dae3cce1d3b6d4ac00138254a968d974f`
with pre-existing working changes retained. The [session ledger](tracks/platform_experience_qa_20260911/evidence/runbook-session-20260914.md)
records bounded sessions, ownership, candidate evidence and outstanding gates. This run covers
the declared runbook, all 31 registry layers and four separate land-context groups, their time
controls, intervention/social journeys and the AI intervention workspace. Existing historical
receipts retain their original scope; implementation presence does not establish acceptance.

First engineering batch applied: GBIF feedback, workspace lifecycle corrections, removal of the
feed's unsupported telemetry claim, botanical metadata transport handling and QA census reconciliation.
The [check receipt](tracks/platform_experience_qa_20260911/evidence/check-receipt-20260914.json)
records passing frontend tests, final type checking, zero-error lint, boundary checks and Python
format/lint/full-selected-pytest plus the final targeted mypy correction. Source remains local and
uncommitted; this checkpoint does not certify a release or close the runbook.
The [independent review](tracks/platform_experience_qa_20260911/evidence/independent-review-20260914.md)
approves the bounded authored code and reconciled checks; overall platform QA remains RED.
At that first checkpoint the computer-use inventory had no available browser. Subsequent sessions
use the local Playwright runner; its bounded results below do not close overall browser acceptance.
Production data certification, scheduled advances, source admission and real-human contribution
acceptance retain their separate gates.

The [second session](tracks/platform_experience_qa_20260911/evidence/runbook-session2-20260914.md)
adds explicit proposal-to-AI consent and records a passing 190-file / 2,502-test frontend sweep.
It verifies ten environmental availability generations and audits forty populated physical rung samples
(27 full part-metadata matches, nine availability-digest matches, four counts-only legacy samples),
while confirming missing weather September 6 and shortwave June 1 publication. Sensor refresh
remains operator-held. Historical freshness documents and three checksum-verified JSON artifacts
were recovered; original coverage JSON/CSV and launch notes remain missing. All local fixes remain
uncommitted and undeployed; browser and production acceptance remain open.

The [third session](tracks/platform_experience_qa_20260911/evidence/runbook-session3-20260914.md)
adds workspace geometry recovery after navigation, expands physical sampling to thirty streams
across Sessions 2/3, and records 37 selected-day API cases queried twice. It identifies the original
sensor absent-state conflict and its artifact-gated correction path. Legacy indexed absences
remain source-unproven; incomplete SSURGO parts remain withheld. Dormant request voting requires
an owning implementation contract, while defined request/like/comment journeys remain required.

The [fourth session](tracks/platform_experience_qa_20260911/evidence/runbook-session4-20260914.md)
corrects public-request disclosure and the forecast tool's unavailable-capability description.
Sixteen read-only agent probes preserve selected days, grouped depths, static release dates and
typed refusals within their stated bounds. Four existing Chromium fixture tests passed, with one
routing skip; the local Next server required manual teardown. These supplementary fixture results
do not close the 220-case live, authenticated, slider, drawing, accessibility or release matrix.
The full frontend sweep found one stale assertion, corrected and confirmed by its eleven-test
file; Python's full selected checks passed. Exact candidate distinctions and retained failed
attempts are in the [Session 4 receipt](tracks/platform_experience_qa_20260911/evidence/check-receipt-session4-20260914.json).

The [fifth session](tracks/platform_experience_qa_20260911/evidence/runbook-session5-20260914.md)
is exercising isolated authenticated social and workspace journeys. Fresh database bootstrap
exposed Alembic and Drizzle search-path failures; independently reviewed fixes now pass the real
local bootstrap and deployment migration no-op without changing historical migration hashes.
Candidate 13 passes all 192 frontend files / 2,512 tests and 12 tooling tests; type checking,
data-boundary checking and lint exit zero. Lint retains 9,887 warnings and the explicit `.omc/**`
CLI exclusion for inaccessible historical scratch directories. Candidate 11's Python sweep remains
FAILED: one failure, 4,497 passes, 146 skips and one expected failure. Desktop browser attempt 4
passes identity resume and both real database-backed proposal/social journeys, 3/3 tests in 50.2
seconds, with runner exit zero and verified closed-port cleanup. Separate mobile attempt 5 passes
the identity dependency and both 390×844 touch journeys, 3/3 tests in 35.2 seconds, also with clean
cleanup. Earlier failures remain preserved. These bounded results do not close whole cases or
release acceptance.

The [sixth session](tracks/platform_experience_qa_20260911/evidence/runbook-session6-20260914.md)
compares the current Python service on an isolated Linux filesystem. After correcting two omitted
capture fixtures, its final offline format/lint/mypy/pytest gates pass: 4,494 passed, 150 skipped
and one expected failure, with complete per-case skip evidence. The first failed attempt remains
preserved. Database integration, pending registration, wheel packaging and the other skipped
surfaces are not certified; the Windows rename failure remains unresolved.

The [seventh session](tracks/platform_experience_qa_20260911/evidence/runbook-session7-20260914.md)
fixes a real drawing teardown crash exposed by About navigation. Candidate 14 passes the integrated
frontend gates, 12 tooling tests and 2,514 Vitest tests. Actual desktop and mobile browser journeys
now restore unsent Point/Polygon drawings after client navigation and confirm discard, with no
analysis or submission mutation and verified process cleanup. The bounded desktop environmental
scenario now passes real weather/moisture date changes, three moisture depths and static/withheld
controls; its first three failed attempts remain retained. Independent review found a new defect:
the date summary assigns never-published SSURGO a selected date as though data were drawn.
That fix, physical-device, accessibility, model-stream, all-layer slider and whole-platform
acceptance remain open.

The [eighth session](tracks/platform_experience_qa_20260911/evidence/runbook-session8-20260914.md)
adds two targeted Windows passes: the existing frozen-input hash test and the existing wheel SQL
resource test using verified local build dependencies. These supplement the Linux skip ledger
without rewriting it.

The [ninth session](tracks/platform_experience_qa_20260911/evidence/runbook-session9-20260914.md)
reproduces a stale moderation decision overwriting an earlier publication, while passing comment
ownership and administrator deletion checks. Its first likes case hit the normal login limit before
reaching a like action. A separate reviewed likes-only run now passes two-client state, count and
reload checks with normal authentication and clean process shutdown. The initial automatic login
snapshot exposed a synthetic credential; that artifact was sanitized, and the revised capture
guard passed a dummy-field failure probe and a clean exact-known-credential scan of the real run.
The moderation and publication-date fixes are approved for the next complete local source batch;
their integrated checks and fresh browser regressions remain required.

The [tenth session](tracks/platform_experience_qa_20260911/evidence/runbook-session10-20260914.md)
passes corrected desktop and mobile temperature/VPD controls and governed reads, including four
temperature depths, September 4/5 selections, return to latest, 5–100 percent strength and separate
visibility controls. The original unsupported zero-strength expectation remains a failed harness
attempt. Desktop numeric temperature rendering is independently reviewed. Mobile's open manager
obscures the canvas, and isolated VPD numeric-label rendering remains unproved; a separate bounded
follow-up targets those visual gaps. Different viewports and rungs do not establish conservation.

Fresh read-only botanical evidence is in the [September 14 receipt](tracks/platform_experience_qa_20260911/evidence/botanical-read-20260914.json).
The current pointer still selects `956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4`.
The wide zoom-8 request still returns `bbox_too_large_for_zoom`; the grid-policy decision is pending.
The identification object is a valid zero-row Parquet file matching manifest count zero. Its source
receipt reports `extension_row_counts: {}` and no unread-extension reasons. This closes the
receipt-inspection step in historical Finding 3; it does not prove an independent source-archive
field-map reconciliation or close admission gates.

The contribution queue currently calls `publishContribution`, and that procedure sets
`status: published`. The outstanding intervention publication issue in the table above therefore
requires end-to-end revalidation before being treated as a missing implementation.

1. Choose one layer and freeze its source, day horizon, resolutions, current publication generation, and owning schedule.
2. Read the physical Parquet objects, completion markers, and availability entry independently. Do not infer one from another.
3. If coverage is missing, create bounded repair work against the original source. Preserve source identity, request bounds, and checksums.
4. Publish all required rungs and completion receipts before advancing availability.
5. Verify the public selected-day reader, map rendering, and agent tool against the same generation. Exercise populated, absent, unavailable, and source-ceiling responses.
6. Record the evidence in the owning active track and update its metadata and this runbook only when the outstanding state changes.

## Recovery

- Disable the affected current schedule and preserve the last valid immutable generation and pointer.
- Re-run a bounded, idempotent source-direct request. Never restore a PostgreSQL environmental reader or writer.
- Do not advance availability until every required object and marker verifies.
- If a reader is incomplete, return an explicit Parquet unavailable response while the owning track repairs it.
- For release failures, keep the previous verified generation active and record the exact revision, request, source identity, and failed gate.

## Validation

Apply the complete change batch before the final integrated check. Run the data-boundary check, type check, lint, affected frontend and Python tests, migration/bootstrap verification, and relation census appropriate to the change. Production acceptance additionally requires cold and warm request traces, browser evidence, schedule burn-in, and an independent release verdict.

## Handoff — four rendering gaps diagnosed, none fixed yet (2026-09-14)

Follows the GBIF-occurrences-layer session below. This session added browser-side caching, raised
the cell budget, ran a code review, built the GBIF layer as a separate source via a 5-lane
AgentGraph mission, then investigated a fresh user report: botanical/GBIF layers not rendering at
a wide regional zoom, and land-context layers (parcels/utility/BLM/state-lands) toggled on but
drawing nothing. Two parallel investigation agents plus live curl verification produced four
findings below. **None are fixed** — this is diagnosis only.

### Finding 1 — botanical/GBIF aggregate layer: a real, CONFIRMED serving defect

`services/agri-data-service/src/agri_data_service/planes/botanical_occurrences.py:47-60` bounds
each support rung's answerable bbox area:

```python
MAX_BBOX_SQUARE_DEGREES = {"detail": 4.0, "grid-0.05": 100.0, "grid-0.25": 1600.0}
```

Zooms 7-10 select the `grid-0.05` fine rung, whose ceiling is only 100 square degrees — one eighth
of the coarse rung's 1600. Confirmed live against production
(`https://plantgeo-parquet-api-production.up.railway.app/api/v1/botanical-occurrences/query`,
release `956c0be7...`):

| bbox | area (sq deg) | zoom | result |
| --- | --- | --- | --- |
| `-125,42,-111,49` | 98 | 5, 8, 9 | real cells returned, no refusal |
| `-130,40,-110,50` | 140 | 8 | **refused**: `bbox_too_large_for_zoom`, `"a grid-0.05 answer is bounded at 100.0 square degrees"` |
| `-125,41,-108,50` | ~150 | 8 | refused, same reason |

A normal wide-PNW viewport at zoom 7-10 sits right on or over this ceiling. The refusal does raise
a client banner (`src/components/map/LayerManager.tsx:889-895`), so whether the user actually saw
it is open — but the underlying defect is real: the fine rung's budget is too tight for a viewport
that legitimately wants regional coverage in that zoom band, while the coarse rung right below it
(zoom <7) comfortably covers the same area at 16x the budget. **Not decided**: raise the `grid-0.05`
ceiling, have the frontend clamp/downsample its requested bbox before crossing into that zoom band,
or pick the rung from combined zoom+bbox-size rather than zoom alone. This is a server-load-vs-UX
tradeoff, not a one-line constant bump — needs a decision before a fix lands.

### Finding 2 — GBIF layer: correctly empty, but indistinguishable from broken (UX gap)

No real GBIF acquisition has run yet — by design, this session's own GBIF commit built the
pipeline/schema/layer without running one. `src/components/map/layers/GbifOccurrencesLayer.tsx`
handles the empty-geojson case correctly (early return, no throw), but produces **no banner or
empty-state message at all**, unlike the UBC layer's zoom-floor banner. A user toggling it on sees
nothing — identical to what a broken layer looks like. The ingestion pipeline itself
(`pipeline/direct/botanical_occurrences/`) was swept for TODO/stub/`NotImplementedError` markers
and came back clean; the UBC lane is fully implemented, only genuinely never-run for GBIF. Fix is a
UX addition (an explicit "no data ingested for this source yet" indicator), not a data fix.

### Finding 3 — the 2KB `identifications/part-0000.parquet`: probably not a bug, one check short of closed

Initially looked suspicious next to the collection's real scale (confirmed live: 177,728
`excluded_by_qc`/`nonspatial` records exist in this release for even a small Vancouver-area
bbox query). But `identifications` is populated only from Darwin Core *extension* rows
(`forward.py:330-336`) — a different table from `occurrences`/`raw_occurrences`, where the real
specimen volume lives (`forward.py:461-464`). UBC's export may simply carry no Identification
extension, or one `parse_meta_descriptor` didn't recognize (tracked in `descriptor.unread_row_types`,
`archive_descriptor.py:176-182`). **Not yet verified**: whether this generation's publish receipt
(`forward.py:338-349`, fields `extension_row_counts` and reasons prefixed
`unread_extension_row_type:`) shows the extension was seen-and-skipped rather than never processed.
Pull that receipt before concluding anything further.

### Finding 4 — land-context layers: three independent, fully diagnosed blockers (NOT this session's feature)

Belongs to a different, concurrent session's in-flight work
(`conductor/tracks/environmental_parquet_serving_20260912/**` was seen actively modified throughout
this session) — diagnosed here for the handoff, not claimed or touched.

1. **No data plane exists.** `src/lib/server/services/land-context/parquet-reader.ts:1-17` is an
   explicit placeholder ("No real data source is wired in yet... always resolves to an
   empty-with-gap-stated result"). A `TODO(worker-1/integrator)` names the missing reads:
   `boundary_versions` / `organizations_offices` / `public_contact_routes` / `source_releases`.
   Every accessor returns empty-with-gap; the tRPC router answers 200 with zero matched features,
   so nothing surfaces as a visible failure.
2. **Geometry is discarded even where data exists.** `src/components/map/land-context/useLandContextQuery.ts:63`
   hardcodes `geometry: { type: "GeometryCollection", geometries: [] }`. This is now stale — the
   contract carries `geometryWkb: string | null` (`src/lib/environmental/land-context-contract.ts:60`)
   and a decoder already exists (`geometry/boundary-geometry-adapter.ts`) but isn't called here.
   Fixing (1) alone still renders nothing until `toFeature` calls `decodeBoundaryGeometry`.
3. **The query never fires on a plain viewport view.** `useLandContextQuery.ts:87-95` gates both
   queries behind `selection.mode === "point"|"area"`, defaulting to `selection: null`
   (`land-context-store.ts:168`), which short-circuits to empty with no request issued. This is
   click-driven point/parcel lookup by original design, not a viewport overlay — toggling the layer
   on over a wide regional view was never going to draw anything regardless of (1) and (2). The
   frontend rendering itself is real and correctly wired (`LandContextLayer.tsx:105-177`, genuine
   fill+line layers, correctly mounted at `MapView.tsx:492-493`, correctly toggled via the store) —
   the toggle is not a no-op, there's simply nothing feeding it and no auto-fire on pan/zoom.

Recommended unblock order: (1) implement the four `parquet-reader.ts` bodies against a real Parquet
lane, (2) wire `decodeBoundaryGeometry` into `toFeature`, (3) decide — as a product question, not an
obvious fix — whether land-context stays click-driven (current, correct-per-spec) or gains an auto
viewport-bbox query; only that third option would match a user's expectation of seeing overlays on
pan/zoom without clicking.

### Session state

- Botanical envelope fix (data-derived coverage box, not a hardcoded placeholder), the
  `BotanicalOccurrencesLayer` `style.load` race fix, and the full GBIF-occurrences-as-a-new-layer
  feature (5-lane AgentGraph mission: source/schema plan, pipeline field mapping, map layer,
  governance evidence, verify sweep) are committed and pushed to `main` — `f18b3f47`, `0f16e40d`.
- A concurrent session has ongoing uncommitted work in
  `conductor/tracks/environmental_parquet_serving_20260912/**`, `src/app/about/page.tsx`, and
  (now-restored after a stash mishap this session — verified via its own 6/6 test suite, nothing
  lost) `services/agri-data-service/src/agri_data_service/interface/cli/availability.py` and its
  tests. Re-check `git status`/`git log` before touching anything nearby; do not assume stability.

### Continuation plan

1. **Pull the release receipt for `956c0be7...`** and check `extension_row_counts` /
   `unread_extension_row_type:` reasons to close Finding 3 (`forward.py:338-349` names the fields).
   Cheap, do this first — it's a read, not a decision.
2. **Take Finding 1 (bbox ceiling) to the owner as a decision**, not straight to a code change —
   the three fix directions trade off server load against UX differently. Once decided,
   `oh-my-claudecode:executor` can apply it; it is a small, well-localized change once the direction
   is picked.
3. **Add an empty-state indicator to `GbifOccurrencesLayer.tsx`** (Finding 2) — small, no decision
   needed, matches the existing banner pattern already in `LayerManager.tsx`.
4. **Land-context (Finding 4) is not this session's track.** Hand it to whoever owns
   `environmental_parquet_serving_20260912` rather than picking it up independently — re-verify its
   current state first, since that track has been actively moving underneath this session.

```
Resume work on the four rendering-gap findings recorded in this handoff (botanical/GBIF layer
zoom-band bbox ceiling, GBIF empty-state UX, the 2KB identifications file, and land-context's three
blockers). Read this RUNBOOK section first — state, decisions still open, and the continuation plan
above. Verify the botanical bbox-ceiling finding still holds with a fresh curl against
https://plantgeo-parquet-api-production.up.railway.app/api/v1/botanical-occurrences/query before
acting — data may have changed. Start at step 1.

Watch out for: Finding 1's fix direction is NOT decided — do not just raise the constant without
flagging the server-load tradeoff to the owner first. Finding 4 (land-context) belongs to a
different, concurrent session's track — check its current state before touching it.

Suggested: `oh-my-claudecode:executor` for step 1 (a read) and step 3 (a small, undecided-free UX
add) · an owner decision (not an agent) for step 2 · re-scope step 4 against
`conductor/tracks/environmental_parquet_serving_20260912/plan.md`'s current state before acting.
```

---

## Handoff — botanical occurrences: map wiring complete, independently reviewed, one governance question open for the owner (2026-09-13)

### Update — this session

Completed the continuation plan from the prior handoff (below), steps 1-5:

1. **Governance ledger reconciliation** — attempted, then CORRECTED after independent review caught a real contradiction (see "Critical finding" below). `admission-decisions.json` no longer claims UBC v16.43 is "admitted" — it is accurately described as "serving but not admitted," matching what `owner-risk-decision-20260913.md` actually authorized.
2. **Current-pointer resolution** — added `GET /api/v1/botanical-occurrences/current`, resolving `current.json` server-side and answering the pinned `release_set_id` (or `unavailable`). Plane logic in `read_current_botanical_release()` (planes/botanical_occurrences.py), thin HTTP adapter in interface/http/botanical_occurrences.py. 21 tests green.
3. **Next.js server proxy** — new sibling file `src/lib/server/services/botanical-occurrences-client.ts` (deliberately NOT added to the frozen `parquet-plane-client.ts` WIRE contract — separate plane, no existing contract pairing). Exports `getCurrentBotanicalReleaseSetId()` and `getBotanicalOccurrences()`. 8 tests green, typecheck clean.
4. **Map wiring** — tRPC procedure `getBotanicalOccurrences` in `src/lib/server/trpc/routers/environmental.ts`, new hook `useBotanicalOccurrencesQuery` in `src/hooks/useViewportProxiedLayers.ts`, and all three previously-unmounted layer components (`BotanicalOccurrencesLayer`, `BotanicalRichnessLayer`, `BotanicalCollectionEffortLayer`) plus `BotanicalOccurrenceDetails`/`BotanicalFilters` mounted into `LayerManager.tsx` / `DockDetails.tsx` under the existing `LAYER_REGISTRY` toggle mechanism (folded into the Vegetation dock section, not a new `PanelId`). New presentation-adapter file `src/lib/environmental/botanical-presentation.ts` bridges the client's camelCase decoding and the layer components' pre-existing snake_case vocabulary. Typecheck clean, 1128+ tests green.
5. **Independent review** — a genuinely separate opus-tier reviewer (not this session) reviewed the full stack end to end, including re-running the test suites itself rather than trusting prior claims. Verdict: engineering sound (no coordinate-governance leak found across four independent layers; licensing/attribution correctly threaded through; `refused`/`unavailable` states correctly surfaced, not silently swallowed), but ONE CRITICAL and TWO HIGH findings required fixing before this could be called independently reviewed. All three are now fixed:
   - **CRITICAL — governance ledger contradicted its own cited authority.** Step 1's first attempt populated `admitted_releases` and marked UBC v16.43 "admitted," but `owner-risk-decision-20260913.md` explicitly authorizes only the two PRE-ACQUISITION gates and states in its own "Scope of this decision" section that post-capture gates (field-map reconciliation, native-ID comparison) "are unchanged and still required before any occurrence release is admitted for serving" and "`admitted_releases` stays empty until those pass." **Fixed by reverting the admission claim** — `admission-decisions.json` now has `admitted_releases: []` again, a new `serving_but_not_admitted` array records the honest state (live in production, not yet admitted), and an `admission_reconciliation_note` documents the correction and leaves the real decision — extend authorization, or require the gates to close first — explicitly to the owner rather than guessing.
   - **HIGH — the withheld-records notice text was wrong on two axes.** It said "withheld or generalized" (generalized records ARE drawn; only withheld ones are not) and "in view" (the count is corpus-wide/generation-level per the plane's own design, not viewport-scoped — panning does not change it). Fixed in `LayerManager.tsx`: now reads "N specimen records in this release have their locality withheld by the publisher and cannot be drawn anywhere."
   - **HIGH — `resolution_state` (ambiguous/unmatched/resolved taxon determination) was carried into the details panel but dropped everywhere else**, discarding a governance decision the ingest pipeline deliberately preserves (an unresolved homonym is never silently collapsed). Fixed: threaded through `botanicalOccurrencesToGeoJSON`'s properties and into the hover tooltip (`hover-fields.ts`), captioned as "Determination: name {state}" whenever not `resolved`.
   - Also fixed a flagged MEDIUM (non-blocking): the Vegetation dock section now gates the botanical filter/detail sub-panels on at least one botanical toggle being on, rather than showing specimen-record UI to every NDVI user unconditionally.
   - Reviewer's other MEDIUM/LOW findings (unwired `limit`/`cursor` pagination inputs, a publish-time rights-URI guard recommendation, an `asDeclared` cast naming nit, hardening the presentation-adapter test to catch new-field drift) are recorded but NOT fixed this session — lower severity, not blocking.

**Still genuinely open, for the owner:** the governance ledger's `admission_reconciliation_note` (`conductor/tracks/pnw_herbaria_source_admission_20260911/evidence/admission-decisions.json`) needs an explicit owner decision on whether UBC v16.43 may continue serving ahead of the still-open post-capture gates (field-map reconciliation, v16.42-vs-v16.43 native-ID stability), or whether those must close first. This is not an engineering question and should not be resolved by an agent.

---

## Prior handoff (superseded by the update above, kept for its "State"/"Continuation plan" detail)

### Goal

Push the herbaria/botanical work forward from "gates open, nothing acquired" to real, correct
specimen data an operator (and eventually the map) can use for intervention modeling. Owner
explicitly authorized skipping the institutional-reply wait (own risk decision) and later said it is
fine for the remaining map-visibility work to be a separate, longer-horizon slice rather than forced
into this session.

### State

**Complete and verified (curled against production, not just tested locally):**
- Owner risk decision recorded: [pnw_herbaria_source_admission_20260911/evidence/owner-risk-decision-20260913.md](tracks/pnw_herbaria_source_admission_20260911/evidence/owner-risk-decision-20260913.md) — accepts per-record `informationWithheld`/`dataGeneralizations` as sufficient coordinate-policy evidence (no institutional reply required), names custody owner/location/retention/withdrawal/permission-authority.
- UBC v16.43 fetched from `data.canadensys.net` into local quarantine, archive-safety inspected (`outcome: release_accepted`, zero reasons) — [ubc-inspection-receipt-20260913.json](tracks/pnw_herbaria_source_admission_20260911/evidence/ubc-inspection-receipt-20260913.json).
- Published to the real production Parquet bucket (`plantgeo-parquet-9ymvp7gv`) twice: first as generation `0c0f3cb8...`, then — after the name-join fix — as `956c0be7...`, which `current.json` now points to. The first generation is superseded but NOT deleted (indefinite retention per the owner decision).
- **Real defect found and fixed, not just theorized**: UBC's `occurrence.txt` declares no `dwc:scientificName` field in `meta.xml` at all — only atomized `genus`/`specificEpithet`/`infraspecificEpithet`/`taxonRank`. Fixed in commit `0c72d3d` (`_joined_scientific_name()` in `normalize.py`, `TAXONOMY_RECIPE_VERSION` bumped to `source-names-v1`). Verified live: a Vancouver-area query now returns real names (`Magnolia x soulangeana`, `Salix lasiandra`, `Helianthus cusickii`, etc.) instead of `null`.
- Backend route (`/api/v1/botanical-occurrences/query`) and the three agent tools (`botanical_occurrences_in_region`, `botanical_occurrence_spatial_neighbours`, `botanical_occurrence_temporal_neighbours`) mounted in `app.py` and deployed — commit `87a817e`. Full quality receipt green both times (`QUALITY_RECEIPT.json` regenerated and verified after each change).
- `published_at` (generation timestamp, for staleness display) added to both detail and aggregate response shapes and to the TypeScript client types, alongside the already-existing per-record `collection_key`/`rights_uri`/`attribution_text` (source display). This was in direct response to the user's ask to support source/staleness tooltips.
- Verified via a **temporary** public domain on `plantgeo-parquet-api` (`plantgeo-parquet-api-production.up.railway.app`) — real curl, real data, HTTP 200. **This domain is still live** — see Environment below.

**In-flight / not done:**
- Map visibility. `BotanicalOccurrencesLayer.tsx` / `BotanicalOccurrenceDetails.tsx` exist but are mounted NOWHERE — not in `LayerManager.tsx`, no data-fetching hook calls the API from the browser. This is real, separately-scoped work belonging to the already-planned `botanical_occurrence_experience_20260911` track (still `status: "planned"`).
- **Blocking discovery for the above**: the HTTP route explicitly refuses `release_set_id=current` ([planes/botanical_occurrences.py:192](../services/agri-data-service/src/agri_data_service/planes/botanical_occurrences.py#L192)) — a caller must pin an exact generation id. Nothing over HTTP currently resolves "what is current" (only `current.json` inside the bucket, read server-side by the Python publish/read code). The browser therefore has no path to learn which generation to query. This needs one deliberate, small addition to the wire contract before any frontend hook can work.
- The Next.js side has NO server proxy for this plane yet. Every other environmental plane goes through `src/lib/server/services/parquet-plane-client.ts`, whose wire format is explicitly frozen and dual-tested (a Python test parses the `WIRE` block out of that TS file and compares it to a pydantic table — editing one side without the other fails both suites by design). A botanical-occurrences equivalent does not exist yet.
- WTU acquisition deferred behind UBC (one-transfer-at-a-time budget in `admission-decisions.json`).
- Field-map reconciliation (matching normalized counts against the raw `occurrence.txt` row count) and the v16.42-vs-v16.43 native-ID stability comparison (plan A2) are still open. They do not block what is already live, but do matter before treating this as "fully admitted" in the governance sense.
- `admission-decisions.json`'s `admitted_releases` array is still `[]`. This is intentionally a *different ledger* from what is live in Parquet — the governance JSON has not been updated to reflect the acquisition in this session's rush to verify the data pipeline. **Worth reconciling**: either update `admitted_releases` to reflect the real state, or document explicitly why it stays empty despite live data.

### Review ledger

No independent/adversarial review has run on any of this session's code changes (self-reviewed only, per the user's explicit "fastest" choice over spawning a separate reviewer agent). The `botanical_occurrence_parquet_lane_20260911` track's own plan calls for an independent governance/archive-safety reviewer in a separate context (partition `a3`) — that has NOT happened. Treat the mounted route/tools as *functionally verified* (real data, correct shape, tests green) but *not independently reviewed*.

### Decisions

- Accepted per-record `informationWithheld`/`dataGeneralizations` over waiting for an institutional reply — owner's explicit call, not inferred.
- Self-reviewed and mounted the previously-held-back route/tools rather than spawning a separate reviewer agent — owner chose "fastest" explicitly when asked.
- Deferred WTU acquisition rather than doing both collections at once — matches the pre-existing one-transfer-at-a-time budget in `admission-decisions.json`, not a new constraint.
- Did NOT attempt a rushed frontend hook/LayerManager mount once the "current" pointer-resolution gap surfaced — chose to stop at a real architectural boundary (frozen wire contract, dual-language test coupling) rather than hack around it. User confirmed this can be a longer-horizon follow-up.

### Assumptions

- The generated temporary Railway domain on `plantgeo-parquet-api` is low-risk to leave up (a governed, read-only, refusal-enforcing endpoint over already-public CC0/CC-BY data) · default taken: left it live · to reverse: one click in Railway dashboard (Settings → Networking → remove domain) or ask a session to do it — no MCP tool currently exists to remove an HTTP service domain (only `delete-tcp-proxy`, for TCP proxies).
- The joined-name fallback (`genus [+ specificEpithet [+ taxonRank + infraspecificEpithet]]`, verbatim, never assigning an invented species) is the correct semantics for a genus-only or family-only determination · default taken: leave exactly as the source asserted it · to reverse: cheap, it's one function (`_joined_scientific_name` in `normalize.py`) with 5 dedicated unit tests.
- `admitted_releases` staying empty despite live Parquet data is fine to leave unreconciled for one more session · default taken: flagged in this handoff rather than fixed · to reverse: low cost, it's a JSON edit plus a decision about what "admitted" should mean now that data-only acquisition has happened ahead of the full governance paperwork.

### Relevant files

- [services/agri-data-service/src/agri_data_service/pipeline/direct/botanical_occurrences/normalize.py](../services/agri-data-service/src/agri_data_service/pipeline/direct/botanical_occurrences/normalize.py) — the name-join fix; `_joined_scientific_name()` is the new function.
- [services/agri-data-service/src/agri_data_service/planes/botanical_occurrences.py:192](../services/agri-data-service/src/agri_data_service/planes/botanical_occurrences.py#L192) — where `current` is refused; the exact line the map-wiring work needs to design around.
- [services/agri-data-service/src/agri_data_service/interface/http/botanical_occurrences.py](../services/agri-data-service/src/agri_data_service/interface/http/botanical_occurrences.py) — the now-mounted route.
- [src/lib/server/services/parquet-plane-client.ts](../src/lib/server/services/parquet-plane-client.ts) — the frozen-wire-contract pattern any new server proxy for this plane must follow (or deliberately diverge from, with reasons).
- [src/components/map/layers/BotanicalOccurrencesLayer.tsx](../src/components/map/layers/BotanicalOccurrencesLayer.tsx), [src/components/panels/BotanicalOccurrenceDetails.tsx](../src/components/panels/BotanicalOccurrenceDetails.tsx) — built, tested, unmounted; the components the map-wiring work will actually place into `LayerManager.tsx`.
- [conductor/tracks/pnw_herbaria_source_admission_20260911/evidence/](tracks/pnw_herbaria_source_admission_20260911/evidence/) — `owner-risk-decision-20260913.md`, `ubc-permission-manifest.json`, `ubc-inspection-receipt-20260913.json`, `ubc-joined-name-recipe-fix-20260913.md` are all this session's new evidence.
- [conductor/tracks/pnw_herbaria_source_admission_20260911/evidence/admission-decisions.json](tracks/pnw_herbaria_source_admission_20260911/evidence/admission-decisions.json) — the governance ledger that needs reconciling against what is actually live.
- `conductor/tracks/botanical_occurrence_experience_20260911/` — the pre-existing, still-`planned` track that owns the map-wiring work.

### Environment

- Branch: `main`, fully pushed. Latest relevant commits: `0c72d3d` (name-join fix), `87a817e` (route/tools mount), `847979294` / commit hash referenced as `8479792` (owner decision + UBC acquisition) — all on `main`, nothing stashed or uncommitted.
- **`plantgeo-parquet-api-production.up.railway.app` is a live, real public domain**, generated this session for verification and never removed. No MCP tool exists to remove it (only `delete-tcp-proxy`, for TCP proxies, not HTTP service domains) — removal needs the Railway dashboard or CLI.
- Local quarantine (not committed, not synced): `C:/Users/atooz/plantgeo-quarantine/botanical_occurrences/ubc-vascular-v16.43.zip` — the raw archive bytes, sha256 `277a46ae...ce847`. The owner decision names `s3://plantgeo-parquet-9ymvp7gv/quarantine/botanical_occurrences/` as the real intended quarantine location; the archive has NOT actually been uploaded there yet, only published data has moved to the bucket.
- Railway project "Aevani" (`6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`), environment `production` (`b7cfa813-8a5c-4fcd-80f2-cab736d840a7`). Object store credentials for direct local publish are in `services/agri-data-service/.env` (`OBJECT_STORE_*` vars) — already configured, nothing new needed.
- `railway run --service <name> -- <cmd>` executes LOCALLY with Railway env vars injected — it does NOT execute inside the container, so `*.railway.internal` hostnames never resolve through it. Learned this the hard way this session; do not repeat the attempt.

### Continuation plan

1. **Reconcile `admission-decisions.json`** ([conductor/tracks/pnw_herbaria_source_admission_20260911/evidence/admission-decisions.json](tracks/pnw_herbaria_source_admission_20260911/evidence/admission-decisions.json)) against the fact that UBC v16.43 is live in Parquet — either update `admitted_releases`/the UBC collection's `decision` field to match reality, or write down explicitly why the governance ledger intentionally lags the data pipeline. Cheap, do this first.
2. **Design and add a minimal "current pointer" resolution** to the botanical-occurrences wire contract — likely a small addition alongside the existing frozen `WIRE` block in `parquet-plane-client.ts` and its Python-side fixture/pydantic counterpart (find via `services/agri-data-service/tests/contract/`). This is the one real blocker before any frontend code can work; do not skip straight to step 3 without it.
3. **Build the Next.js server proxy** for this plane (new file alongside `parquet-plane-client.ts`, following its bounded-fetch/thrown-fault pattern) once step 2 gives it a `release_set_id` to pin.
4. **Wire `BotanicalOccurrencesLayer`/`BotanicalOccurrenceDetails` into `LayerManager.tsx`** — add a fetch hook, a toggle entry, zoom-band switching against `BotanicalRichnessLayer`/`BotanicalCollectionEffortLayer` (also unmounted, same track), and pass `published_at`/`collection_key`/`rights_uri` through to a hover tooltip per the user's explicit ask this session.
5. **Independent review pass** — spawn a genuinely separate reviewer (not self-review) over the mounted route/tools/normalize fix before calling the `botanical_occurrence_parquet_lane_20260911` track's partition `a3` (independent verdict) satisfied. This was explicitly skipped this session per the owner's "fastest" choice and is real technical debt, not paranoia.
6. Optionally remove the temporary `plantgeo-parquet-api-production.up.railway.app` domain via the Railway dashboard, or leave it if the owner wants it for continued manual testing.

### Suggested invocations

- Step 2 (wire-contract design) — inline or a single `oh-my-claudecode:executor` pass; it's one focused file plus its paired fixture, not a fan-out.
- Step 4 (LayerManager wiring) — `oh-my-claudecode:executor-high` (Opus): `LayerManager.tsx` is 1200+ lines with intricate store/toggle/zoom-tier patterns already in place; a shallow pass risks inconsistency with the eight other layers already wired there.
- Step 5 (independent review) — `oh-my-claudecode:code-reviewer` or `/security-review`-style separate-context pass; explicitly must NOT be this same session, since the whole point is a fresh set of eyes with no authoring context.

```
Resume work on wiring live botanical occurrence data (UBC v16.43, real species names, already
published to production Parquet) onto the actual map, and reconciling the herbaria track's
governance ledger with what has already been acquired.

Read `conductor/RUNBOOK.md` first — the "Handoff — botanical occurrences" section has full state,
decisions, assumptions, and a review ledger. Verify its in-flight claims against `git status` and a
live curl before acting (the data pipeline is verified correct; the map wiring is not started).
Start at that section's Continuation plan, step 1.

Watch out for: the HTTP route refuses `release_set_id=current` by design
(services/agri-data-service/src/agri_data_service/planes/botanical_occurrences.py:192) — there is no
way yet for a browser to learn the current generation id. Do not attempt to wire a frontend hook
before step 2 (current-pointer resolution) is done, and do not bypass the frozen wire-contract
pattern in src/lib/server/services/parquet-plane-client.ts without updating its paired Python fixture.

Suggested: `oh-my-claudecode:executor-high` for the LayerManager.tsx mount (1200+ line file with
established per-layer patterns to match) · a genuinely separate reviewer agent for the independent
review step (must not be the authoring session).
```
