---
type: runbook
status: active
updated_on: 2026-09-13
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
