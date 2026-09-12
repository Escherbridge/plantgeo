---
type: acceptance-checklist
track: parquet_production_acceptance_20260901
date: 2026-09-12
status: blocked
verdict: RED
source_commit: 843b4b313e03447594b23a67f75c3062b2b1a024
---

# Production evidence fan-in checklist

Every unchecked requirement is **RED**, including unrun reviews. This checklist
consumes future owner packets under the [matrix](acceptance-matrix-20260912.md);
it does not authorize their collection or any infrastructure action. A file named
below is an **expected future artifact**, not a claim that it exists. Actual
retained artifacts are listed only in [source custody](source-custody-20260912.json).

## Custody and exact candidate — G0

- [ ] `candidate/service-matrix.json`: owner/coordinator/independent verifier task
  IDs, review base, full application/data-service/executor commits and trees,
  changed paths and dirty-diff hash, service/environment/deployment IDs, effective
  commands/definition/configuration hashes, build/start/ready times, pinned
  source/support/data releases, capture UTC timestamps, exact rollback revisions.
  Bind all relevant serving services; no secret values belong in the receipt.
- [ ] `candidate/products.json`: current registered versus activated/required
  product/lane census; every UI/private/agent mapping, reader/route, nature,
  floor, ceiling, lag, cadence, depth/statistic, authoritative required rungs,
  current source acquisition owner and live capture/reviewer task. Reconcile
  older generic exporters and retired database-era assignments explicitly.
- [ ] `candidate/cases.json`: fully expanded stable case IDs from product and QA
  matrices; literal day/release/window/bbox/camera/zoom/cache/input variants,
  expected outcomes, predeclared metrics and scope rationale for any proposed
  reduction. Blank, omitted, blocked and unrun cases cannot become GREEN.
- [ ] `candidate/intake.json`: every file path, media type, byte length, SHA-256,
  originating owner/task, source candidate/release and UTC capture time; verify
  hashes locally before fan-in. Keep raw traces/captures with their original
  identities and mark superseded evidence; no circular commit self-hashes.

## Source, absence, support and publication — G1

- [ ] `publication/<lane>/source-inventory.json`: provider/version/rights,
  requested horizon and satisfied/unresolved intervals, source floor and
  receipt-derived ceiling/lag, inventory root, immutable source receipts and
  acquisition owner for each outstanding interval. A recent poll is not a
  complete history census; exhausted or unavailable source is explicit.
- [ ] `publication/<lane>/terminal-ledger.json`: every owed `(day,rung)` is
  immutable published data with source/data/completion/terminal receipts, or
  immutable governed absence with source receipt and reason. Retain exact
  partitions, row counts, content hashes, statuses and missing intervals.
  Derived-empty markers are valid only with their completion receipt; unmarked
  legacy rungs require owner repair. No partial scan justifies source absence.
- [ ] `publication/<lane>/availability-bindings.json`: pointer key and raw-byte
  SHA-256, ETag/VersionId when present, generation key/SHA/length, schema and
  coverage wire version, row population/limits, lane/product/nature, ordered
  authoritative required-rung set, source ceiling and recorded edge; immutable
  bootstrap receipt key/SHA, manifest/checkpoint keys/SHAs and source inventory
  root. Verify pointer, generation metadata/rows, bootstrap and source/terminal
  references agree. A rollup also binds canonical pointer and generation hashes.
- [ ] `publication/<lane>/publication-order.json`: durable source/data parts and
  completion markers precede verified generation; pointer conditional advance
  occurs last. Retain previous generation, conflict/correction lineage and
  publication barrier/lock evidence; never treat a candidate manifest as published.
- [ ] `publication/<lane>/manifest-bounds.json`: immutable release/manifests and
  parts with bytes/rows, spatial extent, coordinate system, source support IDs,
  cell origin/centre/width/height, coverage fractions, aggregation and contributor
  counts, request bbox and clipping/selection contract, response limits and
  `truncated=false` for accepted cases. Geometry may cross a viewport edge under
  its declared intersection contract; do not invent vertex containment rules.
- [ ] `publication/support.json`: bucket-pinned climate/soil/vegetation support
  IDs, coordinates, ordering, coverage fractions and grid validation. For static
  soil separately require all twelve COG/PMTiles full-object identities,
  transforms, source/licence/release, property/depth/statistic, scale/units and
  palette. SSURGO needs native/detail and generalized low-zoom release evidence.

## Reader and request metrics — G3

- [ ] `routes/<case-id>/private.json` and `public.json`: exact request route,
  parameters and status, response body/hash, trace/correlation IDs, bytes/rows,
  bounds/limit/truncation, temporal state, rung/support/provenance, requested and
  served day, window order or pinned release identity. Bind private
  `/api/v1/parquet/{coverage,day,window,release}` and actual public tRPC/agent routes
  from the frozen tree; refuse unsupported combinations explicitly.
- [ ] `routes/<case-id>/availability-io.json`: effective policy and measured
  cache state; pointer/generation/rollup/bootstrap-marker GET counts and keys,
  bytes and durations; historical prefix LIST count and historical data-part
  read count. Both latter counts must be zero for admitted time-bearing
  availability. Separate static/census traffic and resolve literal gate-9 count
  differences; a green HTTP response or imported test is insufficient.
- [ ] `routes/<case-id>/metrics.json`: catalogue start/end duration, day-row
  TTFB, request-start-to-correct-settled-paint duration, request/error/byte and
  feature counts, cache observation/reset method and each cache layer's state,
  capture clock/iterations/statistics, declared thresholds and actual outcome.
  Time-to-response and time-to-paint are separate clocks. Synthetic one-second
  waits and historical timings are not measurements of the current candidate.
- [ ] `routes/<case-id>/temporal-refusal.json`: catalogue, exact response, frame,
  legend, selected feature/details, tool and final answer reconcile. Include
  newest terminal/populated/absence/missing/outside/future, carried release,
  window holes, checksum/stale/bootstrap/rung/depth/statistic failures, contract
  version mismatch, transport/timeout and invalid/over-limit input distinctions.
  No silent fallback, zero-for-missing, unlabelled cap or adjacent-day substitution.
- [ ] `routes/<case-id>/interactions.json`: pan, scrub, zoom boundaries, pin/step,
  rapid changes/reordered replies and cancellation trace, requested/served/painted
  dates and cache identity. Prove no production fire request reaches `/api/fires`.
  Preserve source cancellation/batching limits; record actual downstream work.
- [ ] `routes/coverage-caption-review.md`: reader owner's S1 implementation and
  accessible visible authority/source-ceiling text, recorded-versus-carried-day
  rationale and exact candidate review. Existing missing caption blocks this item.

## Canvas, geometry and renderer — G4

- [ ] `spatial/<case-id>/capture.json` plus original screenshots/canvas captures:
  browser/version/device/input/viewport/orientation, camera and bbox, layer,
  selected/served/painted day, data/release/support/manifest hashes, zoom/rung,
  cache state, style/terrain/opacity, capture timing and file SHA-256.
- [ ] `spatial/<case-id>/pixels.json`: reproducible region, baseline image/hash,
  sampled pixels or mask, comparison method, tolerance and measured result for
  adjoining cells, source-part seams, zoom transitions and no-data boundaries.
  Retain dense basemap, empty/error and opacity/style-reload variants. Direct
  WebGL `readPixels` is unreliable in the retained synthetic packet; only its
  documented screenshot-derived probes support that narrow historical claim.
- [ ] `spatial/<lane>/conservation.json`: detail-to-derived counts/sums and
  contributor IDs reconcile under declared aggregation; means are checked with
  their count/weight semantics, not summed as observations. Stable support,
  bit-identical shared edges, domain masks and exactly one painted physical rung.
  Detection density never becomes a perimeter; native polygons retain topology;
  vegetation stays at actual 0.25-degree support and station detail stays points.
- [ ] `spatial/renderer-verdict.md`: all render classes and four rungs reviewed;
  M0 MTBS/fire/air-temperature/soil-moisture baselines, SSURGO deviation disposition,
  populated scalar zero/tiny/missing/avg/isoband semantics, dense-basemap label
  readability, hover/touch equivalent support selection, no cracks/nested blocks,
  feature/byte/frame/GPU/request-to-paint budget results on desktop and mobile.
  Shared climate/soil hover remains a separate owner prerequisite.

## Effective ownership, recovery and burn-in — G2

- [ ] `operations/ownership-cutoff.json`: current service/revision/configuration,
  exact command/definition hash and active/required lanes, old/new owner mapping,
  checkpoint/cursor, process/run/work-item/attempt/lease/fencing identities,
  before/after effective cutoff, no in-flight legacy writer and no overlapping
  publication. `configured_pending_deployment` or a later commit alone is not proof.
- [ ] `operations/<lane>/recovery.json`: three separately observed scenarios:
  transient failure -> retry -> terminal publication; restart retains cursor and
  definition with bounded catch-up; expired lease reclaim fences stale worker and
  successor completes. Preserve attempt/failure/output receipt and availability
  lineage, locks/idempotency, dead-letter/breaker disposition and no duplicate
  publication. Implementation tests alone cannot close runtime recovery.
- [ ] `operations/<lane>/burn-in.json`: at least **three consecutive scheduled
  advances for every activated lane**, with scheduled/due/start/end UTC times,
  run/work-item/attempt/owner identities, source settlement, output/source/terminal
  receipt hashes, manifests and availability generation before/after each advance.
  Reconcile coverage/source ceiling/all-rung completeness/conservation after
  each interval. A configured duty, manual run or one product's history backfill
  does not establish three later scheduled advances in another product.
- [ ] `operations/rollback.json`: exact supported application/data-service/
  executor rollback revisions and immutable pointer/generation/release identities,
  trigger, responsible operator, compatibility, observability and evidence of
  safe recovery without owner overlap. Preserve outputs/manifests/checkpoints.
  Do not restore retired PostgreSQL readers, old database-writing archive commands,
  Railway cron/one-shot services or schedules. Any future exercise requires its
  own authorization; this checklist performs none.

## Platform fan-in and separate reviews — G5/G6/G7

- [ ] `platform/qa-execution.json`: every required variant in the 34-group
  [crosswalk](qa-crosswalk-20260912.csv), plus remaining platform inventory scope,
  has an exact candidate, observed outcome and original captures. Preserve
  unsupported/future weather, botanical, occurrence and contact gates. Community
  synthetic UI submissions prove mechanics only; C16 still needs a human contributor.
- [ ] `platform/agent-mcp.json`: actual initialize/ping/tools-list/tools-call
  captures reconcile **11 current tools and 24 observation surfaces**; malformed
  methods/tools/schemas, typed unavailability and transport failures stay distinct.
  Same role/place/day/window/filters/units/release/run, values/support/provenance,
  temporal/spatial neighbours and offset distances match UI, tool and final answer.
  Include location consent, role/session/cache changes, cancellation/retry and
  private-context clearing. Botanical reference is not a 25th observation surface.
- [ ] `platform/accessibility.json`: names/roles, tab/focus order and return,
  Escape, announcements, keyboard/touch, persistent text equivalents,
  contrast/non-colour states, reduced motion, touch targets and 200% zoom/reflow;
  portrait/landscape, map-versus-scroll gestures and actual role/server boundaries.
  Separate physical-device results from viewport emulation.
- [ ] `quality/integrated-release-sweep.json`: after all fixes, exact integrated
  source/deployment reconciliation, commands/environment/UTC times/exit codes/log
  hashes for full boundary/type/lint/frontend tests and full Python release checks
  as applicable to the complete candidate. One final sweep under
  [testing policy](../../../../docs/testing.md); a scoped run cannot be labelled
  full-suite or produce a full Python quality receipt. Historical missing
  toolchain/510-error results remain partial, not GREEN.

The review slots are deliberately distinct. Reviewer task identities and all
current-candidate verdicts below are **UNBOUND / RED / not_run**. Independent
documentation review of this local packet fills none of these production slots.

| Review slot / expected artifact | Accountable track | Required independent decision |
| --- | --- | --- |
| RV-data / `reviews/scientific-data.md` | Source owners + gapless + retirement; relevant botanical/forecast/validation owners | Source rights/lineage, source floors/ceilings/absences, units/support/aggregation/missingness, domain/scientific claim limits; no unsupported forecast/ranking/effect claims |
| RV-render / `reviews/renderer.md` | Multiscale + QA | All render classes/rungs, native versus density meaning, conservation, original canvas/pixel proof, selected feature, dense-basemap and measured budgets |
| RV-access / `reviews/accessibility.md` | Platform QA + feature owners | Actual keyboard/touch/assistive-tech, focus/announcements/non-colour/reflow/device and role evidence across applicable cases |
| RV-agent / `reviews/agent-parity.md` | Platform QA + reader/agent owners | Transport discovery/errors and UI/tool/final-answer parity on same evidence, selected day and role; appropriate refusal and neighbour disclosure |
| RV-release / `reviews/release.md` | Production acceptance + independent release verifier | Every required case passed, complete source/service/deployment/release/rollback matrix, quality receipt hashes, all other review verdicts, residual risks and separate authorization status |

- [ ] `verdict/final.json`: no unresolved required failures, blocked/unrun cells,
  missing owners or unaccepted exclusions; exact candidate/tree/product/service/
  rollback identities; review verdicts and residual risks reconcile. Any stop
  condition in the production spec is RED. GREEN is evidence for downstream
  retirement **review**, never a mutation or certification shortcut.
