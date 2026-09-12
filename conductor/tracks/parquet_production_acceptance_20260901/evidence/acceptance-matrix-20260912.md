---
type: acceptance-matrix
track: parquet_production_acceptance_20260901
date: 2026-09-12
status: blocked
verdict: RED
source_commit: 843b4b313e03447594b23a67f75c3062b2b1a024
source_tree: 9533bb9e5423240630935df0cd012cd8ead15504
---

# Dependency-gated production acceptance matrix

**RED. No current all-product production case was executed or passed in this
lane.** This is a local evidence-intake packet against committed local `main`,
not a deployment, runtime observation or certification. The branch is
`codex/production-acceptance-verdict-20260912`; the enclosing evidence commit and
tree are reported after commit to avoid a circular self-hash.

The author owns this track's evidence only. The requesting coordinator is
`01a09475-01c4-7252-8710-a8a57559c919`. The canonical platform QA owner remains
`01a0904b-756b-7961-b991-cab666123be2` in its
[task ledger](../../platform_experience_qa_20260911/evidence/task-ledger.md).
Read-only subagents `/root/writer_evidence_audit` and `/root/renderer_qa_audit`
supplied source research; neither is an operational owner or release reviewer.
No Railway, database (including `pgt`), object storage, writer, scheduler,
deployment or live service was accessed. Git changes are local; no push occurs.

## Authority and scope

The [production specification](../spec.md), [plan](../plan.md),
[registry](../../../tracks.md), [release policy](../../../release-governance.md),
[platform matrix contract](../../platform_experience_qa_20260911/matrix.md) and
[September 12 remaining matrix](../../platform_experience_qa_20260911/evidence/remaining-acceptance-matrix-20260912.md)
govern the verdict. The [source custody index](source-custody-20260912.json)
binds retained files to this source commit with Git object IDs and SHA-256 hashes.
Hashing a retained receipt proves custody, not its scientific or runtime claims.

The [product matrix](product-matrix-20260912.csv) keeps all 27 PGQA-L surfaces
and two backend-only rows, for **29 product intake rows**. Current source has
23 public capability entries expanding to 30 physical lanes, plus `signal` and
`calendar`, for **32 registered physical lane keys**. The public coverage census
deliberately excludes those two backend keys; their private route/publication
contracts need independent intake rather than invented public capabilities. It is an
intake inventory, not a fresh census of activated production products. Every row
is RED/blocked and names dependencies, missing artifacts, owner and next action.
The [QA crosswalk](qa-crosswalk-20260912.csv) preserves all 34 September 12 audit
groups verbatim with their original requirements and state: **30 blocked, four
not_run, all RED**. These counts are audit-group counts, not executed-case counts
or replacements for the historical 206-case inventory. Other platform families
remain in that inventory; this packet grants no scope exclusions.
The crosswalk's six source columns are verbatim; embedded relative links resolve
from the original remaining-matrix document, not from the CSV's new directory.

Source `kind=forecast` declarations exist for fire detections, sensors, signal,
vegetation and water gauges. They are partition variants of existing keys, not
five additional registered products, and the public census is observed-only.
Require declaration/release/refusal reconciliation for each at G0/G3; no published
forecast or selectable forecast axis is established. The current serving
`SNAPSHOT_PRODUCTS` catalogue is empty; its three frozen temperature provenance
descriptors do not add active keys. Static soil, demand and strategy scopes are
not invented as registered Parquet lanes. Future governed weather forecast,
botanical profile and occurrence products retain B/W/O admission gates in the QA
crosswalk before they can join an executable production matrix.

## Freeze keys

| Key | Bound identity or missing binding | Acceptance consequence |
| --- | --- | --- |
| S0 | Source commit `843b4b313e03447594b23a67f75c3062b2b1a024`, tree `9533bb9e5423240630935df0cd012cd8ead15504`; clean checkout at intake, equal to local `main` then | Local source authority only; no remote freshness claim |
| S1 | Future integrated owner corrections: full base/commit/tree/diff, dependencies and independent review **unbound** | Required for the coverage caption and any returned defect; evidence-only changes do not fill this slot |
| O1 | Exact application/data-service/executor/service/deployment/configuration/release/rollback packet **unbound** | Blocks current route, operational and release verdicts even if S0 is locally committed |
| D1 | Per-product source inventory, dates, support, release/manifest/receipt/pointer/generation/bootstrap hashes **unbound as a current complete set** | Historical subsets stay dated; selectors below cannot become observations |
| U1 | Exact browser/version/camera/viewport/role/input/cache/fixture and predeclared budgets **unbound for current production** | Blocks measured canvas, accessibility and performance acceptance |
| B/W/O | Future botanical-profile, forecast and occurrence candidates/releases **unbound**, respectively | Preserve the original FB/FW/FO dependencies; current historical weather, authoring UUID lookup and metadata admission do not satisfy them |

The prior QA matrix's F0 is `64f4f892bd2b744cc097c7f76a1f239997b80f52` /
`f8697da694a3f9fbbf28e70ff7581bd59546bfd6`. S0 is this audit's later source
freeze. No old execution receipt is relabelled as an S0 or O1 pass. Candidate
changes require owner-supplied impact reconciliation and affected new evidence.

## Dependency gates

These are gates over cases, not permission to run the deferred operations.
`blocked` means required evidence or implementation is missing; `not_run` means
the check has not been executed. Both evaluate to RED. A missing owner task or
required cell is itself RED. A track name routes work; it does not invent a live
operator or reviewer assignment. No `not_applicable` disposition is accepted here.

| Gate | Depends on | Current verdict/state | Missing owner/artifact | Next action by owning track |
| --- | --- | --- | --- | --- |
| G0 inventory/freeze | S0, complete surfaced/private product census | RED / blocked | Production + QA + gapless: O1/D1, live task bindings, exact per-product dates, route applicability and performance limits | Reconcile registered and effective lanes, 27 surfaced rows, private products and future claims; bind every executable variant before capture |
| G1 source/publication | G0; gapless and retirement admission | RED / blocked | Gapless/retirement: required horizons, acquisition owners, source/absence receipts, complete rungs/manifests/bounds and support artifacts | Supply interval-specific evidence; retain unavailable or withheld states until admitted |
| G2 ownership/recovery/burn-in | G1; exact effective executor boundary | RED / blocked | Gapless: current commands/definitions/settings, cutoff/lease/attempt proof, retry/restart/expired-lease outputs, three scheduled advances per activated lane | Submit current no-overlap and recovery/burn-in packet; dated schedules and unit tests are insufficient |
| G3 reader/routes/cache | G0 + G1; S1 caption; reader R0/R2 | RED / blocked | Reader authors + production capture owner: accessible authority/ceiling correction; O1-bound public/private traces, pointer request classes, row bounds, refusal cases | Return code defect to reader owner; accept new immutable candidate, then collect matched route/cache/day traces in a separately authorized task |
| G4 renderer/spatial | G1 + G3; multiscale M0/M3 | RED / blocked | Multiscale: real-data baselines, support/rung conservation, canvas pixels, dense-basemap/picking/mobile and measured budgets | Submit exact renderer packet for all render classes and all required rungs |
| G5 platform/accessibility/parity | G3 + G4; relevant B/W/O and protected-service fixtures | RED / blocked | QA + feature owners: all remaining WX/MS/RD/BT/AP/RA cases, actual 11-tool discovery and 24-surface parity, device/role/accessibility evidence | Execute remaining journeys only after matching prerequisites; obtain separate accessibility and agent verdicts |
| G6 integrated checks/reviews | G2 + G3 + G4 + G5; frozen integrated candidate | RED / blocked | Integration + independent reviewers: full release-quality sweep and separate scientific/data, renderer, accessibility, agent-parity and release reviews | Apply all owner fixes, freeze exact candidate, run one final full release sweep, reconcile receipt hashes and review exact tree |
| G7 release/rollback verdict | G0 through G6; O1 | RED / blocked | Production acceptance + release owner: complete service/commit/product/rollback matrix and accepted residual-risk list | Issue evidence verdict only when every required case passes; separate operator authorization remains necessary |

Gate dependencies control **acceptance**. Authors may prepare contracts and
synthetic fixtures in parallel under their own scope. Failed evidence goes back
to its owning track; this lane does not patch application code or repair data.

## Factorized execution matrix

One eventual case ID is
`PA/<product-row>/<physical-lane>/<route>/<day-or-release>/<zoom>/<cache>/<coverage>/<window>/<role-device-input>/<variant>`.
The full tuple and exact PGQA requirement must be present in its receipt. Expand
each materially different behavior in the product matrix using the axes below.
Per-lane tests and the visible multi-lane intersection are separate cases.
No blank axis means pass or exemption. Reduced combinations require a written
scope rationale and independent acceptance; none is approved in this packet.

| Dimension | Required cases / binding rule | Missing binding and RED dependency |
| --- | --- | --- |
| Product | Every surfaced row plus current private registered product; all soil depths, temperature mean/max/min, wetness depths and static releases independently; also test visible common-history intersection | G0/D1: exact production activation and complete private inventory reconciliation |
| Day | D-newest = newest eligible **terminal** day under source ceiling, D-history = receipt-proved populated historical day, D-empty = receipt-backed governed absence; also missing day, never-written lane, pre-floor, post-ceiling/future and outside-domain cases | Dates are **UNBOUND**, not September 12 by assumption; gapless/reader must provide literal UTC days and receipts for each product |
| Zoom/rung | Map z3/z7/z10/z13 resolve to physical 0/5/9/13; these cover coarse, both middle rungs and detail. Capture each boundary at 4.99/5/5.01, 8.99/9/9.01, 12.99/13/13.01; exactly one rung paints | G4: required-rung inventory and real canvas evidence; zoom numbers alone do not prove support |
| Camera/coverage | Default PNW camera, viewport-derived request bbox; pan interior, source-domain edge, partial overlap, outside, source-part and batch seams; record actual bounds and clipping semantics | Source fallback box is west -125, south 42, east -111, north 49, with camera derived from a 1024x512 container and padding 40. O1 effective client/server bbox and U1 settled camera remain unbound; do not assume deployed defaults |
| Cache | Named browser/application/data-cache cold and warm states; full-index miss/hit, immutable-generation reuse, matching/missing/stale/mismatched rollup, pointer loss before/after bootstrap, negative/positive marker cache, expired outer cache and UTC rollover | G3: record which caches were actually cold and how known; a fresh browser or elapsed wait does not prove cold service/object-store state |
| Coverage route | Whole catalogue and per-lane/rung availability, wire v3, required fields, authority, `latest_recorded_day`, terminal versus missing/absence, source ceiling, common intersections and static/null axes | G1/G3: `/api/v1/parquet/coverage` has **no viewport**; bound result cardinality/bytes and zero historical availability LIST/data-part reads for admitted time-bearing policy. Do not add an invented bbox contract to this route |
| Day route | Exact selected settled UTC day, current viewport bbox, zoom/rung, filters, bounds and typed state; cross-check public tRPC/map and private `/api/v1/parquet/day` | G3: request/response identities, no global collection, accepted `truncated=false`; requested/served/painted/legend/detail/answer must reconcile |
| Window route | `/api/v1/parquet/window`: single-day closed interval, bounded all-populated interval, mixed published/absence/missing interval, range touching floor/ceiling, maximum admitted span, oversize/reversed/malformed refusal; timezone/date transitions | G3: bind literal first/last days and configured limit; exactly one ordered envelope per requested calendar day, no omitted gap or cross-day carry |
| Release route | `/api/v1/parquet/release`: exact pinned release, before first release, between releases, newest eligible as-of, unavailable/withdrawn version, version update with cache identity; static lookup has a null daily axis | G1/G3: bind release key/hash and source watermark. Carry is explicit: requested day differs from served snapshot day when supported. Burn severity cumulatively unions admitted releases at/before day; perimeter snapshot uses requested-day event predicate |
| Refusal | Unpublished/stale/malformed/checksum-invalid availability; missing/lost bootstrap; wrong lane/nature/rung/schema or missing depth/statistic; ceiling violation; unsupported reader, outside domain, timeout/transport failure; invalid inputs and truncation | G3: retain exact typed status, body, bounded counts and visible/agent refusal. Governed absence is not timeout, missingness or an index failure; zero is not missing |
| Interaction | Pan without changing day; bounded scrub; reordered/delayed replies; selected day pin/step; request cancellation; reload/style/opacity; old frame loading disclosure then correct settled paint | G3/G4/G5: browser trace proves no live `/api/fires` and no stale-day repaint. Preserve existing cancellation proof and its batching asymmetry; do not claim it was remeasured here |
| Role/device/input | Anonymous plus permitted viewer/contributor/expert/admin and separate org roles; desktop 1280x720 and narrow 390x844 anchors, portrait/landscape, keyboard/mouse/touch, 200% reflow, assistive tech, reduced motion | G5/U1: actual browser/device/role bindings and permitted fixtures absent; emulation and physical-device results remain separate |
| Metrics | Separate catalogue time, day-row TTFB, request-to-paint; request/error counts, bytes, rows/features, cache observations, interaction/frame/GPU limits where relevant | G3/G4/U1: numeric budgets, clock method, repetitions and thresholds must be declared before measurement; no invented performance target or synthetic settle-time pass |

For a time-bearing lane, the three terminal-day selectors times four zoom probes
times two named cold/warm states yield **24 minimum day-route slots per physical
lane** before coverage/window/release/refusal, interaction and device expansion.
This is a planning formula, not a runnable or executed count. Where a genuine
governed-empty day or route is unavailable, keep that cell blocked pending an
owner-supplied admissible fixture or an independently accepted scope disposition;
never manufacture source absence to complete the matrix.

## Reader trace interpretation

The [September 12 availability contract](../../parquet_reader_cutover_acceptance_20260901/evidence/local-availability-contract-20260912.md)
and [reader blockers](../../parquet_reader_cutover_acceptance_20260901/evidence/local-acceptance-blockers-20260912.md)
preserve a material discrepancy between the literal gate-9 pair of GETs and
actual source request classes. An uncached full read can fetch pointer and
generation; a matching rollup can avoid the generation; stale rollup fallback
can add a probe; a missing pointer can add a bootstrap-marker GET. No reader
conditional GET/304 is implemented. The 60-second inner index cache, 120-second
Python coverage cache and 300-second TypeScript memo are distinct; same-day and
generation-age policy also constrain reuse. These are source contracts, not
measured timings or effective deployment settings.

Default source policy `census_until_bootstrap` can census never-bootstrapped
time-bearing lanes; static lookups can census under either policy. Therefore a
whole-catalogue zero-LIST claim cannot be inferred from source. The operational
owner must provide effective policy, every lane's bootstrap state and counted
pointer/generation/rollup/marker GETs, separating static/census request classes.
Any historical LIST/data-part availability read in the required admitted
time-bearing scope is RED. A literal-count discrepancy needs explicit owner and
independent requirement reconciliation; this packet does not weaken gate 9.

`coverageAuthority` and `sourceCeilingDay` lack a layer-panel consumer at S0 in
the retained reader audit. G3 needs an owner correction with accessible text,
including recorded-versus-carried-day rules; screenshots alone cannot close it.
The client does not universally validate single-day/as-of echoes against the
outgoing day, so exact request/response comparison remains mandatory. Missing
states have no served day; an older placeholder is only a loading frame.

## Source and scientific limits

See [fan-in checklist](fan-in-checklist-20260912.md) for exact evidence fields and
[verdict](final-verdict-20260912.md) for required owner actions. SoilGrids and
SSURGO remain separate withheld/restoration gates. Temperature history and MTBS
have bounded dated receipts; neither establishes present forward health.
Detection-density cells are not perimeters; vegetation retains 0.25-degree
support; station detail is a point. Soil-survey coarse-summary deviation needs
explicit renderer/scientific disposition. Sampled weather has no admitted
forecast horizon. Botanical profiles do not prove occurrence, abundance,
suitability or intervention effects. Unsupported ranking/forecast/effect claims
remain refused, with their own admission and review dependencies.

All future evidence filenames and custody fields are defined in the checklist.
The local packet completes a dependency map and intake contract only. A0's
literal data/deployment freeze and A1–A4 execution remain open, as do metadata
slices a1–a5 (the plan's A3 burn-in is metadata a4; plan A4 final is metadata a5).
