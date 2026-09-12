---
type: track-evidence
slug: parquet_reader_cutover_acceptance_20260901
artifact: local-availability-contract
date: 2026-09-12
status: authored
---

# Local availability contract reconciliation

This read-only source audit freezes the current reader contract for R0. It does not
certify a deployment, a production policy, published days, request counts, or a test
execution. Audited base commit: `8c14ea0117ba14d5584f737b793f989566102514`;
base tree: `de840fb558a110e0aded8f2baf44a39b9717f0d7`.
Paths and line numbers below refer to that base. No Railway, object store, PostgreSQL,
`pgt`, writer or load was contacted or controlled. Only this receipt, the owning plan
and its dated metadata reconciliation are authored by this slice.

## Wire and source ceiling

- The current coverage wire is **version 3**, agreed by
  `services/agri-data-service/src/agri_data_service/parquet_ops/wire.py:47`,
  `services/agri-data-service/tests/contract/wire_contract.py:48`, and
  `src/lib/server/services/parquet-plane-client.ts:463`. Version 2 is the September 2
  historical freeze; version 3 additionally requires `latest_recorded_day`.
- `wireCoverageSchema` at `parquet-plane-client.ts:465` requires the authority,
  generation SHA-256, pointer key, source ceiling, required rungs and withholding
  reason fields, with explicit nullable values where declared. `decodeCoverage`
  at `:635` rejects malformed bodies and any other version; it preserves those facts
  on the TypeScript result at `:664`. An omitted field is not a healthy index.
- `parquet-slider-capabilities.ts:927` checks availability withholding before census
  currency. Its ceiling gate at `:841` compares `latestRecordedDay` to
  `sourceCeilingDay`. A carried release may answer after its publication ceiling;
  a genuinely recorded future partition is withheld. `latestDay` and the recorded
  publication edge cannot be substituted for each other.
- The capability publishes `coverageAuthority` and `sourceCeilingDay` at `:744`.
  No component currently consumes either field. `LayerTimeSlider.tsx:376` derives a
  selected-day coverage note and `:424` joins its captions without these facts.
  The R2 caption item therefore stays open. The field comment in
  `src/types/time-slider.ts:256` also still describes the ceiling as an axis limit;
  the implementation's bounded-carry distinction is the current behavior to preserve
  when that future slice updates wording.

## Availability integrity and refusal

The following Python paths are under
`services/agri-data-service/src/agri_data_service/`.

- `pipeline/parquet/availability_index.py:1475`, `read_latest_availability`, loads
  the required pointer and generation, then enforces expected lane/product/nature,
  rung contract and minimum source ceiling (`:1535`). The full generation verifier
  at `:1665` checks byte SHA-256, its content-addressed key, row limits, Arrow schema,
  metadata, population, semantic receipt and pointer/Parquet agreement (`:1728`).
- `parquet_ops/availability_coverage.py:640`, `_read_lane`, classifies checksum,
  malformed, missing and stale availability refusals. `withheld_lane_coverage`
  at `:581` emits null day bounds and empty published/gap/absence ranges; it does
  not turn an index failure into a claim that a source published no observations.
  Unclassified transport faults escape the full lane read instead of becoming a
  content withholding. The public TypeScript boundary at
  `src/lib/server/services/parquet-slider-capabilities.ts:904` withholds all
  Parquet capabilities on recognized coverage boundary faults.
- `required_source_ceiling` at `availability_coverage.py:108` uses the lane's allowed
  ceiling minus cadence, publication lag and three grace days. This source-relative
  tolerance is separate from response-cache age.

## Requested day and served day

- `requestedDay` is the caller's named day; `servedDay` is the partition or
  release that supplied the answer. Exact day and window resolution set the two
  equal (`parquet_ops/serving.py:96`, `:128`, `:310`), while release resolution
  keeps the as-of day as `requestedDay` and reports the newest eligible release
  or governed-absence marker at its own day as `servedDay` (`:183`, `:195`,
  `:431`). A carried weekly release must therefore remain visibly older than the
  day it answers.
- The wire renders both dates for published and governed-absence envelopes
  (`parquet_ops/wire.py:133`, `:174`). `parquet-envelope.ts:82` freezes the same
  distinction and `parquet-plane-client.ts:498` preserves it into the TypeScript
  union. Missing-day states carry only `requestedDay`, because no partition was
  served. `parquet-trpc-readers.ts:997` preserves the pair in ready and governed
  absence results.
- The current client validates the shape of each returned day, and validates the
  requested-day sequence for a window (`parquet-plane-client.ts:538`, `:619`),
  but `getParquetLayerDay` and `getParquetLatestRelease` do not independently
  compare the echoed `requestedDay` with the outgoing single-day/as-of argument
  (`:743`, `:784`). The serving implementation and inspected contract tests
  establish the intended source behavior; this local audit does not turn that
  into an observed production echo check or a universal client refusal claim.
- Response-specific panels already name both dates where a carried answer is a
  product behavior (`ClimateDetails.tsx:143`, `:239`; `SoilDetails.tsx:137`,
  `:239`; `WaterDetails.tsx:137`). That does not close the separate slider-row
  provenance gap: `LayerTimeSlider.tsx:376` still derives its note only from day
  coverage and does not caption `coverageAuthority` or `sourceCeilingDay`.

## Policy boundary: no universal zero-LIST claim

`config.py:197` defaults to `census_until_bootstrap`; no deployed setting was read.
Under that transitional policy, `_no_pointer` at `availability_coverage.py:712`
may return a census lane only when neither a pointer nor a bootstrap marker proves
prior publication. A malformed index/marker or a missing pointer after bootstrap is
withheld. Under `availability`, an unpublished time-bearing lane is also withheld.
Positive bootstrap-marker results are held for the process life; negative results
are re-read (`:262`). The missing-pointer path can therefore add a marker GET.

`_lane_plan` at `:765` returns census for lanes without a time axis under either
policy. `resolve_availability_lanes` does not itself list objects, but
`interface/http/parquet_routes.py:306` explicitly invokes `open_listing()` when its
result contains census lanes. Thus the tree does not prove universal fail-closed
behavior or zero listings across the whole catalogue. Removing the transitional
policy remains conditional on per-lane bootstrap evidence owned by publication and
production acceptance; this local receipt supplies no such evidence.

## Cache and request classes

- `BotoAvailabilityStorage.read` at `pipeline/parquet/availability_index.py:763`
  issues an unconditional `get_object(Bucket, Key)`. It retains ETag and optional
  VersionId and checks declared/actual byte length. **There is no conditional GET
  or `If-None-Match` reader revalidation.** Conditional headers at `:824`, `:860`
  and `:862` belong to immutable writes and pointer CAS, not reader caching.
- `AvailabilityCoverageReader.read_lane_root` at
  `parquet_ops/availability_coverage.py:174` reuses a successful full index for
  60 seconds (`:71`, `:286`). Its storage wrapper at `:790` retains immutable
  generation objects by key, with an 8 MiB per-entry limit and 32-entry capacity.
  Larger admitted generations are served without retention. The wrapper rejects
  publication methods; it is a reader.
- `interface/http/parquet_routes.py:287` reads the optional coverage rollup first.
  `_rollup_rows` at `availability_coverage.py:674` probes an **uncached** pointer;
  `pipeline/parquet/coverage_rollup.py:129` requires both the canonical pointer
  digest and generation digest to match. A matching entry avoids a generation GET.
  The pointer-only reader (`availability_index.py:1498`) does not re-derive the
  generation checksum, row population or semantic receipt; it trusts publisher
  facts already bound into that rollup entry and the current pointer document.
  A missing, unreadable, stale or mismatched rollup falls back to the full index
  path, not directly to historical data. A failed/mismatched probe may therefore
  precede a second pointer read; a warm full-index memo may instead be reused.
- A full cold lane read has a pointer/generation pair when no applicable caches
  satisfy it. A matching rollup requires no generation fetch; the whole build also
  reads the rollup object. Missing pointers may require the bootstrap-marker GET.
  These are source-derived request classes, **not measured production counts**.
  The original gate-9 literal pair per lane needs these classes identified in its
  production trace; this receipt does not mark that gate passed or rewrite its spec.
- There are outer caches: `_CoveragePayloadCache` at `parquet_routes.py:98` holds
  successful complete payloads for 120 seconds and shields one shared build.
  `getParquetWarehouseCoverage` at `src/lib/server/services/parquet-plane-client.ts:805`
  reuses its memo for 300 seconds and may return it while refreshing only if
  `src/lib/environmental/slider-policy.ts:7` permits reuse: same UTC evaluated day,
  finite nonfuture generation timestamp and generation age below ten minutes.
  Consequently, 60 seconds is the inner pointer TTL, not an end-to-end freshness
  guarantee. A deployed cold/warm packet must identify which caches were warm.

## Existing verification subjects and remaining gates

The named tests were inspected as source, **not run in this slice**:

| Subject | Existing local tests |
| --- | --- |
| Frozen payload, refusal, policy, no listing inside resolver, bootstrap loss, TTL and immutable cache | `services/agri-data-service/tests/parquet_ops/test_availability_coverage.py:306`, `:459`, `:496`, `:532`, `:589`, `:658`, `:681`, `:702` |
| Full-read integrity and ETag/length adapter contract | `services/agri-data-service/tests/parquet/test_availability_index.py:234`, `:262`, `:405`, `:431`, `:448` |
| Rollup/full equivalence, skipped generation GET, stale pointer binding and fallback | `services/agri-data-service/tests/parquet_ops/test_coverage_rollup.py:244`, `:265`, `:293`, `:328`, `:360`, `:452` |
| Wire version, withholding and bounded cache reuse | `src/__tests__/services/parquet-plane-client.test.ts:420`, `:545`, `:583`, `:592`, `:986` |
| Exact-day versus carried-release request/serve semantics | `services/agri-data-service/tests/parquet_ops/test_parquet_envelopes.py:152`, `services/agri-data-service/tests/contract/test_wire_contract.py:172`, `src/__tests__/services/parquet-plane-client.test.ts:304`, `:868`, `:884` |
| Withholding precedence, recorded ceiling, carried release, surfaced provenance | `src/__tests__/services/parquet-slider-capabilities.test.ts:1040`, `:1064`, `:1104`, `:1134`, `:1179`, `:1202` |

R0's availability wire/cache/refusal **definition** can close on this source
reconciliation. The existing root
[integrated verification record](../../platform_experience_qa_20260911/evidence/root-integrated-checks-20260912.md)
records earlier validation and environment limits; it does not certify this new
documentation candidate. This worktree lacks the frontend dependency tree.
This continuation's final handoff owns its independent documentation review,
documentation validation and exact candidate commit/tree. Ownership is tracked in the root
[task ledger](../../platform_experience_qa_20260911/evidence/task-ledger.md).
No prior receipt is promoted to a current test pass.
The UI caption, all-reader checks, browser day/bbox/zoom and cancellation evidence,
production bootstrap/policy proof, observed GET/LIST counts, cold/warm timings and
exact deployment/rollback handoff stay open with their existing owners.
