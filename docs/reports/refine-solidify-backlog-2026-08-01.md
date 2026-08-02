# Refine-and-solidify backlog — dual Claude/Codex review, 2026-08-01

Three independent read-only lanes: Codex CLI (31 findings, whole repo), Claude R1
(25, frontend+infra), Claude R2 (25, agri-data-service). Items found by 2+ lanes
are marked ✔✔ (cross-confirmed). Singleton CRITICALs went to an adversarial
Codex verification pass; that run died without output (stdin hang). Instead:
R2#2 (vacuous coverage gate) and R2#5 (inverted cutoff) were empirically
confirmed 2026-08-01 by migration-0014 tests that fail on the old behavior;
R2#1, R1#2, R1#7 are confirmed-or-refuted by the wave that fixes each.

## P0 — gate production cutover on these

1. ✔✔ **Deploy self-deadlock**: `/api/ready` pins exact drizzle hash
   (`migration-contract.ts`) while no automation ever runs migrations
   (deploy.yml only `railway up`). Fix: floor-check readiness + migrate step in
   deploy job. Same brittleness in agri `health.py:18` (`EXPECTED_ALEMBIC_REVISION`
   hardcoded; test compares literal to literal).
2. ✔✔ **Hindcast finalize is time-dependent** (`finalize_forecast_hindcast_run.sql`
   uses `clock_timestamp()` for actuals/knowledge horizon): re-verification of a
   valid receipt can fail later; audits non-reproducible; Codex frames it as
   look-ahead leakage into backtests. Fix: stored `actual_knowledge_as_of`
   column, pin all reads to it (migration 0014).
3. **Vacuous hindcast quality gate** (Claude R2 #2): `computed_coverage` is
   always exactly 1.0 (row-preserving CTE + prior equality check), so
   `min_coverage_fraction` can never fail; `computed_interval_coverage` is
   computed but unused in `computed_pass`. No hindcast can fail its policy.
4. **Inverted as-of gate in strategy selection** (Claude R2 #5):
   `finalize_strategy_selection_receipt.sql:79` gates iteration cutoff with
   `>= data_cutoff` (labels/features use `<=`), so receipts can violate their
   declared cutoff by months while checksumming the false claim.
5. **11 unlocked SECURITY DEFINER functions** (Claude R2 #1): only 0012's guard
   got the locked-owner treatment; `record_forecast_*` etc. still run with
   schema-owner authority reachable from `plantgeo_loader`. Fix: extend 0012
   recipe in a migration.
6. ✔✔ **Checksum GUC gaps**: `finalize_forecast_receipt` pins no GUCs;
   `forecast_hindcast_{value,receipt}_checksum` lack `extra_float_digits` (both
   lanes). Same class as the 0011 interval_coverage bug we already fixed.
7. **Analytics fabricate data** (Claude R1 #2): `db/analytics.ts` reads tables
   with no writers and defaults `streamflowPercentile` to 50 — a constant
   presented as measurement. Demo-gating.
8. ✔✔ **No-timeout upstream fetches** (11 call sites bypass
   `bounded-upstream.ts`; `Promise.all` cron → pool exhaustion → app-wide
   outage). Fix: route all through the bounded helper + ESLint ban on bare
   `fetch(` in `src/lib/server/**`.
9. ✔✔ **Non-prod ingest is unauthenticated** (`ingress.ts` fail-open when
   secret unset outside production). Fix: `requireConfigured: true`.
10. **Redis latch-off** (Claude R1 #7): one transient Redis outage permanently
    disables cache/pubsub (retryStrategy returns null; flag never clears).

## P1 — solidify

- ✔✔ Governance tests never run in CI (8 env-gated skip vars; migration-contract
  tests are string asserts). Consolidate to `AGRI_TEST_DATABASE_URL` + fixture;
  fail sweep on unexpected skips. (R2 #6 — do FIRST so P0 fixes are verifiable.)
- ✔✔ Dual storage paths: `geo.features` vs unused typed tables
  (`geo.fire_detections`, `public.water_gauges`, `drought_data`) — collapse onto
  `geo.features` + read models; point drought at the warehouse plane (D5).
- `geo.features` unbounded growth (~18M rows/yr at new cadence): hypertable +
  retention; keyset pagination on `/api/v1/features`; `ST_X/ST_Y` bbox predicates
  → `ST_Intersects` (index-usable, non-point-safe).
- NULL `p_expected_checksum` bypasses format gate in 4 finalize functions;
  `CASE` digests without `ELSE` (fail-open on future versions).
- concat_ws('|') delimiter injection in 6 checksum preimages → port
  `jsonb_build_array` pattern with digest-version bump.
- Promotion receiver: finalize skips revision re-check; zero logging in 1110
  lines; sequence 0/negative chunk indexing.
- Unqualified `digest()`/`ST_Intersects` in ~15 functions vs hardened
  search_path convention (`public.` qualify + SET search_path; lint in
  split_schema).
- Cron/job hygiene: skipped-runs report success; raw upstream errors (with
  embedded FIRMS key URL) echoed to responses; workflow lacks concurrency
  group; api-auth INCR/EXPIRE non-atomic; second divergent Redis client in
  api-auth (no TLS parsing).
- API keys: no expiry/revocation columns.
- Backfill ops: RuntimeError escapes recovery handler (no blocked checkpoint);
  blocked→validated auto-flip nulls failure reason; `_atomic_write` lacks fsync;
  corrupt cache should be miss-not-fatal.
- drizzle 0000 unqualified CREATE TABLE (role-name/schema collision — hit
  locally); 0003 unique-index vs ON CONFLICT mismatch (tracking); extensions
  created by no migration (readiness requires 4).

## P2 — maintainability

- Extract (R2 list): `execution/http_source.py`, `checkpoint_store.py`,
  `atomic_fs.py`, `config._validate_dsn`, `db/privilege_contract.py`,
  checksum-preimage convention, conftest warehouse fixture, `cli/` 6-module
  split. Frontend: single HTTP client rule, layer registry (replaces 6
  `*_LAYER_ID` vars + hardcoded pubsub channels), viewport bbox real canvas
  dims, structured logging.
- Delete list (verify against in-flight edits first): `useDeckLayers.ts`,
  `DeckOverlay.tsx`+`DeckTooltip.tsx`, `ThreeLayer/ModelLayer/three-utils`,
  `lib/offline/indexed-db.ts`, `preact` dep. Dormant fetchers
  (`drought/hydrosheds/mtbs/usda-soil.ts`) → wire into governed ingestion or
  quarantine (they're the soil/vegetation sources the 2026 plan needs — prefer
  WIRE over delete).
- Repo hygiene (D4, corrected): archive dated conductor tracks; organize
  infra/local-warehouse into templates/pilots/queries; gitignore session logs;
  do NOT delete `infra/local-warehouse/plans/*.json` or `.omc/research/**`
  (checksummed governance/provenance artifacts) — document them instead.

## Leave-alone (load-bearing; do not "clean")

R2's list verbatim applies: ROW(...) IS DISTINCT FROM guard blocks; triple-layer
checksum verification; `_ensure_*`+advisory locks in historical_writer;
`GRANT UPDATE (id)` for FOR UPDATE locking in 0012; the `effect_candidate` hard
block; generated db/agri tree discipline; NotImplementedError downgrades.

## Sequencing

1. Test-infra consolidation (makes everything verifiable) →
2. Semantic decisions — RESOLVED by operator 2026-08-01:
   - R2#2 coverage gate: BOTH — redefine `coverage_fraction` as horizon
     completeness (actuals available ÷ ideal horizon steps) AND add
     `min_interval_coverage_fraction` policy column wired into `computed_pass`;
     existing policy rows need both values backfilled.
   - R2#5 cutoff gate: flip `>=` to `<=` at
     `finalize_strategy_selection_receipt.sql:79`; migration flags existing
     receipts violating the corrected rule as 'cutoff_violation' (no silent
     grandfathering, no deletion — audit trail shows tainted selections).
   - R2#12 quality_passed: HARD GATE — `finalize_strategy_selection_receipt`
     requires a finalized hindcast with `quality_passed = true` for the backing
     model/series. →
3. Migration 0014: hindcast knowledge pinning + GUC pinning + NULL-checksum
   guards + digest qualification →
4. Migration 0015: SECURITY DEFINER owner lockdown →
5. Frontend P0 batch (bounded fetch everywhere, ingress fail-closed, analytics
   repoint, Redis retry, readiness floor+deploy migrate step) →
6. P1/P2 waves per above. All fixes → single sweep → quality-reviewer pass →
   commit (repo convention).
