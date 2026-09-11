---
type: verification-evidence
track: parquet_reader_cutover_acceptance_20260901
reviewed_on: 2026-09-11
reviewed_at_utc: 2026-09-11T18:49:29Z
reviewer: separate-reviewer-agent
source_revision: fa202230958fb55521963e886eb031be5fc266c4
status: complete
verdict: local-candidate-approved-production-unaccepted
---

# Independent reader review — September 11

The local reader candidate is approved. The implementation findings are closed
by independent inspection, the author's final integrated checks passed, and all
14 reviewed runtime hashes matched at closure. This is not production acceptance,
track-wide completion or release authorization.

The reviewer is a separate agent context from the code authors. This pass read
the applicable repository, Conductor, layer-lane and testing contracts; inspected
the reader diff, relevant upstream serving semantics and regression tests; and
reviewed the current production evidence packet. The reviewer ran no tests,
production probes or mutations, and edited only this review evidence. The
engineering code-review skill supplied the correctness, performance, security
and maintainability review dimensions.

The comparison starts at `fa202230958fb55521963e886eb031be5fc266c4`, with the
September 11 registry/plan checkpoint treated as current. The checkout also
contains inherited documentation work; that material is not attributed to the
reader author or approved by this review. Runtime content hashes below bind the
actual implementation reviewed independently of the eventual commit.

## Findings and closure

| Finding | Correction inspected | Regression evidence inspected |
| --- | --- | --- |
| Explicit UTC today selected live-window water/weather behavior instead of the named day. | A supplied date selects the exact partition. Point-weather and regional weather retain the selected date. Omitted dates retain the bounded live freshness policy. | `parquet-trpc-readers`, `parquet-context-readers`, `regional-temporal-context`, `wildfire-cancellation` suites. |
| A correctly echoed requested day could still carry an older served day through an exact-day endpoint. | `/day` and every `/window` member require served-day equality; only `/release` permits an earlier served day. Every state must echo the requested day. | `parquet-plane-client` wrong requested day, earlier/future served day, all terminal states, complete window membership and valid older release cases. |
| Climate/soil adapters conflated published-empty viewports, governed absence and unwritten partitions, and could erase truncation. | Explicit `parquet` terminal metadata preserves the distinction. Published-empty results retain publication, served day and truncation. Empty contour geometry retains source-cell evidence. | `parquet-climate-field` and `parquet-trpc-readers` collection cases. |
| Governed absence could be described as observed zero in the regional advisor. | Direct terminal evidence takes precedence over cached capability coverage. Context adapters retain the recorded reason; fire and MTBS retain their direct absent state. Governed capability ranges are a secondary witness. The prompt explicitly prohibits measured-zero/no-activity claims. Point-weather returns a typed unavailable response with served day and evidence. | `regional-temporal-context` cached-published/direct-absence and interior governed-range cases; `parquet-context-readers` all three adapters; `wildfire-cancellation` public point-weather response. |
| A valid empty-but-truncated fire window could lose its truncation when the regional payload omitted the empty fire block, then be described as observed zero. | The fire source state explicitly reports unknown coverage when no positioned cells were returned and the read was truncated. The private window reader can produce this state for excluded unpositioned rows or exhausted row budgets. Nonempty truncated values retain their existing explicit truncation. | `regional-temporal-context` empty truncated window on a published day and prompt prohibition of presence/absence. |
| Drought carried days beyond the publication ceiling were outside the described coverage interval. | `describedThroughDay` includes the proved answerable `latestDay` while preserving the separate source publication ceiling and recorded-day violation gate. | `parquet-slider-capabilities` carried drought day, following undescribed day, true recorded ceiling violation, and unchanged non-carrying lane. |
| Slider captions omitted authority/source ceiling and implied an unpublished index was actively building. | Visible and accessible captions state availability/census/unstated authority and the publication limit without excluding valid carry. An unpublished index is a settled unverified-date refusal. | `LayerTimeSlider` and `layer-time-state` suites. |
| Regional request cancellation did not reach the environmental row reads. | The HTTP signal reaches the assembler and Parquet row readers. Aborted faults reject rather than becoming successful partial context. Shared capability warming remains shielded. | `regional-intelligence-gates`, `regional-temporal-context`, context-adapter and wildfire cancellation cases. |
| Proximity metadata on selected-day values did not retrieve temporal neighbours. | The regional payload now reads one nearest globally published candidate on each side for daily fire, water and weather. It reports each returned observation's own day, signed/absolute day distance and geodesic spatial distance. Missing local observations, terminal states, truncation and refusals remain explicit. | Dedicated `regional-temporal-neighbors` suite: aliases, bounded read counts, authority/description/source ceiling, 180-day boundary, empty candidate, bad identity/day/rung/viewport, terminal/refusal/truncation and abort cases. |
| Python exact-partition feature reads claimed parity for carried/static, cumulative MTBS and rolling vegetation map populations. | Unsupported temporal populations refuse before warehouse access with a named required semantic contract. Supported daily reads retain their own dates and distances. Tool-visible notes distinguish governed absence from measured zero and partition coverage from map answerability. | `test_agent_parquet_tools.py`: six unsupported surfaces across three selected-partition states, forbidden storage/database access, daily weather values and unavailable-upstream absence. |

No unresolved actionable defect was identified in this candidate's implemented
reader behavior after those corrections. The deliberately unsupported populations
below remain incomplete capabilities, not claims of all-layer parity.

## Review dimensions

- **Correctness:** selected-day binding, terminal evidence precedence, empty versus
  absent semantics, carry/source-ceiling distinction, and explicit neighbour
  limitations are consistent in the inspected code and regression cases.
- **Performance:** transport row/byte/time bounds are retained. Regional neighbour
  selection examines at most 180 calendar days each side from already obtained
  capability metadata, then issues at most six row reads across three concurrent
  source jobs. Each source's two candidate reads are sequential, preserving an
  abort check between them. This can add up to two existing 15-second read budgets
  to context assembly; it is not evidence of measured production latency.
- **Security and authority:** no environmental PostgreSQL fallback, data
  publication, scheduler mutation or deployment path was added. Unsupported
  Python feature semantics refuse before storage access. The inspected public
  evidence distinguishes configuration, deployment identity and observed runtime
  behavior instead of treating them as interchangeable.
- **Maintainability:** new behavior has targeted regression cases and directory
  rationale. The Python concurrency oracle uses an observed waiter barrier rather
  than a sleep, preserving both shared-failure and later-retry assertions. The
  floating-point oracle allows only a stated eight-ULP weighted-mean tolerance;
  counts and extrema remain exact, with negative controls for substantive drift.

## Verification results

The author reports exit code zero for every closure command. The reviewer read
the following closure logs and independently checked all 14 runtime file hashes
against the table below at 2026-09-11 18:49:29 UTC; there were no mismatches. The
reviewer did not rerun tests. Earlier failed sweeps remain diagnostic history and
do not supply the approval result.

| Command | Closure result | Exact source log in this evidence directory |
| --- | --- | --- |
| `npm run check:data-boundary` | PASS: client URL boundary, restricted imports and observation-fabrication checks. | `boundary-closure-20260911.out` |
| `npm run type-check` | PASS. | `typecheck-closure-20260911.out` |
| `npm run lint` | PASS: zero errors, 542 existing warnings. | `lint-closure-20260911.out` |
| `npm run test:changed -- --base fa202230958fb55521963e886eb031be5fc266c4` | Full-selector fallback: 145 files passed, two skipped; 2,226 tests passed, 13 skipped; 203.02 seconds. | `frontend-closure-20260911.out` |
| From `services/agri-data-service`: `python scripts/check.py --changed --base fa202230958fb55521963e886eb031be5fc266c4` | Full-selector fallback: format PASS (0.11s), lint PASS (0.12s), mypy PASS (3.45s), pytest PASS (273.36s). | `python-closure-20260911.out` |

Both selectors conservatively chose their full test surface, rather than just
the reader batch. The frontend selector names the inherited unclassified
`.omc/ultrapilot-state.json`; the Python selector names the unmapped agent
`tools.py` change. Python explicitly reports `receipt_eligible: false`. Its
successful checker output does not print a final test count, so this review
claims no such count and does not derive one from the preceding failed run.

The Python invocation removed the four database-test environment variables under
the repository testing policy. The frontend report includes skipped integration
contracts. These passes neither establish live PostgreSQL integration nor produce
a full-service Python quality receipt. The source log names above identify the
inputs retained by the author's compressed-log manifest and handoff packet.

## Boundaries that remain unaccepted

1. Public observations describe the deployed baseline `fa20223`, not the local
   candidate. First/repeated HTTP requests do not prove controlled cold/warm
   caches, private GET/LIST counts or request-to-paint. The deployment identity
   receipt and the 85-row observation packet preserve this distinction.
2. Time-bearing availability paths have a bounded pointer/generation/rollup
   contract. Static lookup census still performs listing, and the transitional
   missing-bootstrap census policy still exists in code. Configured
   `PARQUET_COVERAGE_AUTHORITY=availability` is recorded; whole-catalogue zero-LIST
   and current in-process operation counts are not established. Static publication
   migration is a cross-lane dependency.
3. Browser selected-versus-painted-day, pan/scrub/rung transitions, cancellation
   completion and request-to-paint remain exact production evidence gates.
   Public z5, all climate/soil variants and historical positive samples are not
   covered by the representative route packet.
4. Daily regional neighbours are bounded samples of the nearest globally
   published candidate day, not proof of the nearest local observation over all
   history. A sampled day with no local observation does not authorize searching
   silently farther or claiming no local neighbour exists. Release/static and
   other unsupported regional sources return explicit limitations.
5. Python release/static/MTBS/vegetation point-tool refusal is safer than false
   exact-partition parity but does not implement those map populations. Broader
   map/agent parity remains open. Python availability-neighbour tools return
   partition dates/counts, not local feature values. The existing signal tool's
   newest-120-partition admission limit remains explicitly bounded.
6. MTBS count differences, source-extent truncation/detail payload refusals,
   watershed intermittency, publication freshness and pixel conservation retain
   their named owning gates. A row HTTP 200 with an embedded refusal is not an
   accepted complete response.
7. No deployment, data publication or project-wide acceptance is authorized by
   this review. A release must use the normal reviewed path. Rollback must reverse
   only this candidate or name an exact schema-compatible deployment; the old
   broad `2b4cfef..HEAD` revert must not restore retired PostgreSQL readers.

## Reviewed runtime hashes

SHA-256 of file bytes from the initial 2026-09-11 18:34:52 UTC review, updated for
the separately inspected final Python admission cleanup and empty-truncated-fire
correction. All 14 hashes matched the final runtime files at 18:49:29 UTC.

| Repository path | SHA-256 |
| --- | --- |
| `src/app/api/ai/regional-intelligence/route.ts` | `64e0c369329b210175fb6a5be5f8559869bb903df9e35822c2c585df497506ac` |
| `src/components/map/layer-panel/LayerTimeSlider.tsx` | `c9343ff158aacd9d1ec7b8690541c9fff4c9b37dd1139b46047312c19612d8a9` |
| `src/components/map/layer-panel/layer-coverage-track.ts` | `7312a4edd98da9eab201761289b77a6e6bebeeb3857d40048f608af8bccf97f6` |
| `src/components/map/layer-panel/layer-time-state.ts` | `62d418539d2f4b0f3dfd87775e73eb97d743c1a6938af40fa4a7d6eda315573e` |
| `src/lib/server/services/ai-prompt.ts` | `4bb546fd0d07de9975c0be111d612e60a2613ce744b87a03bd994b9fe96d2d25` |
| `src/lib/server/services/parquet-climate-field.ts` | `d33fb41518c55350cd246ef2082ad49a635e8e05181eb13e1c3c9d94fd66342d` |
| `src/lib/server/services/parquet-context-readers.ts` | `184d93c4226b8dcac06200a90cbbddbb158027c9a697924184378e5cd28e18e9` |
| `src/lib/server/services/parquet-plane-client.ts` | `cad1d3267eb3dfe90f74410618a9392880f0ba960e0909d4a1c965a4daaa02a1` |
| `src/lib/server/services/parquet-slider-capabilities.ts` | `aaf2cb3d21c0ae8e36c5fe30bb401acb93493f4c0a14bf0c87ffe6cbc9ca27e5` |
| `src/lib/server/services/parquet-trpc-readers.ts` | `9072f3409ab8b4b3a8b247a527f4a222e94a48ac71528f4186947bc2919b2967` |
| `src/lib/server/services/regional-context.ts` | `ffb422c7d3780cc44c97bf526a46d152c4e5e29538235d08b8c59aaa7279931d` |
| `src/lib/server/services/regional-temporal-neighbors.ts` | `7944bcb395b298f5b79faa65705d13707a14f65598dfe382ae45df05c827adce` |
| `src/lib/server/trpc/routers/wildfire.ts` | `393b0807748f88430d2039615b17b7c7ae3a3b86daa2ecdda8fe1ac14d4da928` |
| `services/agri-data-service/src/agri_data_service/agent/tools.py` | `123ca470b7701e5a7a6b70b23cbb10f9b55e1720452fe622db4d99a8ae4684a1` |

Supporting evidence: [reader inventory](reader-behavior-inventory-20260911.md),
[public routes and operation budgets](reader-route-evidence-20260911.md),
[deployment identities](deployment-identities-20260911.json), and
[author handoff](reader-local-handoff-20260911.md).
