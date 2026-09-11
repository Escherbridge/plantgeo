---
type: approval-packet
track: parquet_reader_cutover_acceptance_20260901
prepared_on: 2026-09-11
status: awaiting-owner-execution
baseline_revision: fa202230958fb55521963e886eb031be5fc266c4
candidate_branch: codex/reader-cutover-acceptance-20260911
candidate_revision: d56b508fb3afe8adb85969ce1553cd739daf779f
---

# Remaining production reader acceptance packet

This is a proposed bounded evidence collection and acceptance disposition. Preparing it executed
no production operation. The final handoff binds candidate `d56b508fb3afe8adb85969ce1553cd739daf779f` on
`codex/reader-cutover-acceptance-20260911` to the completed checks and independent review in
[reader-local-handoff-20260911.md](reader-local-handoff-20260911.md). Its verification section is
the validation authority; this packet does not turn a pending sweep into a pass. The exact
candidate commit is recorded after local verification and independent review.

Existing read-only authority remains valid for its existing scope. Owner approval of this packet
would accept the specific remaining evidence scope and the static-census contract disposition
below. It would not authorize deploying the candidate. Until a separately authorized release
exists, production requests measure the deployed baseline and cannot prove the candidate's
caption, selected-today, terminal-metadata or regional-neighbour changes.

## Pinned environment and preflight

The [identity receipt](deployment-identities-20260911.json), observed September 11 at 18:03:28 UTC,
records these exact objects:

| Object | Identity |
| --- | --- |
| Railway project | `Aevani`, `6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990` |
| Environment | `production`, `b7cfa813-8a5c-4fcd-80f2-cab736d840a7` |
| Frontend service | `plantgeo-main`, `fa08a3aa-6d1d-43eb-846b-15dbfd887d61` |
| Frontend deployment | `4fca7553-c5b0-4358-ba8b-907941be6c45`, `SUCCESS` |
| Parquet service | `plantgeo-parquet-api`, `33aed861-af76-4fdd-a95e-784bdcc95e55` |
| Parquet deployment | `9b65d7b8-40e0-4dac-9b74-1744b2ffd8fc`, `SUCCESS` |
| Both deployment commits | `fa202230958fb55521963e886eb031be5fc266c4` |
| Configured coverage policy | `PARQUET_COVERAGE_AUTHORITY=availability` |
| Public frontend origin | `https://plantgeo-main-production.up.railway.app` |
| Private reader binding | Existing server-only `AGRI_PARQUET_SERVICE_URL`; its exact current value was not retained in the September 11 identity receipt |

`docs/deployment.md` records the exact address `http://plantgeo-parquet-api.railway.internal:8080`
as an August 28 activation observation. That is historical documentation, not a fresh private
hostname/configuration read in this packet. `parquet-plane-client.ts` names the binding
`AGRI_PARQUET_SERVICE_URL`; no runtime credential or private configuration was fetched for this
clarification.

Before collecting new evidence, use the existing Railway read-only deployment/configuration
operations scoped to those project/environment/service IDs. Retain only deployment IDs, status,
commit, timestamps and the coverage policy value. Compare them to this receipt and to the final
candidate SHA. A changed deployment requires a new identity receipt and explicit classification
as baseline, candidate, or unrelated revision before any result is attributed to it. Readiness
alone is insufficient: `/api/ready` exposes readiness checks and a timestamp, not the commit.

## Proposed read-only execution scope

One collection session is bounded to 30 minutes, at most 160 public row GETs, ten capability
GETs and four readiness GETs, with one active client request at a time. Stop when any bound is
reached and retain uncompleted cells as unproved. Existing retained observations may be reused
as baseline evidence; do not repeat all 85 merely to populate another file.

Allowed operations within this scope are:

1. Read the scoped Railway deployment identity, the one coverage-policy variable, health and
   existing diagnostic logs. Do not dump environment variables, tokens, connection strings,
   request bodies from other users, or unrestricted historical logs.
2. GET the public procedures named in [the route matrix](reader-route-evidence-20260911.md),
   supplying the exact day, finite bbox and zoom; GET `environmental.getSliderCapabilities` and
   `/api/ready`. Use the existing authenticated/private connectivity for private reader GETs
   only when available; do not create a public Parquet domain, proxy, tunnel or new route.
3. Observe the existing map UI, selected dates, panning, zoom and request cancellation in a
   browser, within the same request budget. Store a sanitized network trace and screenshots.
4. Where a previously approved diagnostic session already provides the reader's object-store
   credentials, run the existing GET-only rollup inspector from the data-service directory:

   ```text
   uv run --no-sync python scripts/coverage_rollup_status.py --json
   ```

   This command reads the rollup and current lane pointers. It does not measure an HTTP cache
   request or justify a cold-cache claim. Its `--rebuild` variant writes object storage and is
   excluded. Do not copy credentials out of the service to make this command runnable.

The existing public row replay script accepts a new output directory and refuses an existing
one. These commands reproduce the dated main and supplemental route packets; the main packet
uses 72 GETs and the supplemental packet uses twelve. They are optional subsets of the session
budget, not mandatory repetitions:

```powershell
$receipt = 'conductor/tracks/parquet_reader_cutover_acceptance_20260901/evidence'
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
& "$receipt/public-reader-probe-20260911.ps1" -OutputDirectory "$receipt/approval-$stamp-main"
python "$receipt/normalize-public-reader-probes-20260911.py" --input-directory "$receipt/approval-$stamp-main"
& "$receipt/public-reader-probe-20260911.ps1" -Supplemental -OutputDirectory "$receipt/approval-$stamp-supplemental"
python "$receipt/normalize-public-reader-probes-20260911.py" --input-directory "$receipt/approval-$stamp-supplemental"
```

Each replay request has an 18-second client limit and a 2,000,000-byte download limit. Record a
client limit separately from an application refusal; never relabel a deliberately interrupted
capture as the server's payload limit. These scripts preserve first/repeat labels and cannot
establish a cold cache. A replay's deployed SHA stays unset until bound to a fresh identity receipt.

## Controlled cold/warm capability proof

This is still an open production gate. Public latency, authority labels, cache-busting query
parameters and a GET-only rollup inspector do not reveal actual downstream operations.

Capture the implementation layer and cache state explicitly:

| Measurement | Required observation |
| --- | --- |
| Cold metadata reader | An evidenced fresh diagnostic process or fresh serving process; empty process caches; first complete coverage request with pointer/rollup/generation/LIST/data-part counters |
| Warm Python payload | Repeat in the same process inside its 120-second payload TTL; prove whether a builder ran and which object operations occurred |
| Rebuilt coverage | One bounded request after payload expiry; distinguish rollup hits, missing/stale entries, pointer revalidation and retained immutable generation bytes |
| TypeScript cache | Correlate public capability requests to private requests; identify the 300-second same-day cache and revalidation rather than assuming public repeat equals Python repeat |
| Retry attribution | Separate logical SDK operations, physical HTTP attempts/retries, bytes actually transferred, and unavailable byte measures |

Instrument only the coverage path in the pinned process. Counters must distinguish rollup GET,
availability-pointer GET, availability-generation GET, bootstrap-marker GET, static prefix LIST,
time-bearing historical LIST, and historical data-part access. Retain method/category/count,
bounded byte totals, cache events, request correlation ID and source revision. Exclude object
credentials, signed URLs and arbitrary raw paths. Existing `PARQUET_READ_TELEMETRY` describes
row-read stages; enabling it alone does not establish coverage GET/LIST counts or network bytes.

The preferred diagnostic is a separately started, model-free, read-only process with the exact
deployed module/configuration and bounded counters. That proves a fresh instance of the reader;
it must not be labeled a cold request to the existing HTTP worker. Attaching a new diagnostic
hook, changing runtime variables, or restarting a serving process is **not authorized by the
read-only scope above**. If needed, obtain separate explicit approval naming the exact service,
deployment, reviewed diagnostic artifact/hash, invocation, maximum duration, restart count and
restoration method. No executable restart or instrumentation CLI is invented here. If no already
approved counter mechanism is available, preserve this gate as unproved and submit that exact
optional diagnostic proposal before execution.

Current implementation budgets at the baseline are 26 time-bearing lanes, four static lanes,
and no live snapshot products:

- A fresh all-hit rollup build uses one rollup GET plus 26 pointer GETs and no time-bearing
  generation GETs. A missing rollup can require one pointer and one bounded generation GET per
  time-bearing lane. Stale entries can add a pointer probe before the full-index fallback.
- Warm Python payload reuse performs no build I/O. Generation reuse and pointer TTL are separate
  caches; a cached generation does not mean its semantic validation was skipped.
- Time-bearing capability reads must perform zero historical prefix LISTs and zero historical
  data-part reads, with no PostgreSQL observation query or bootstrap invocation.
- Static evacuation-zones, fire-perimeters, soil-survey and watersheds intentionally retain
  census listing under the current policy. Logical listing count and physical pagination must
  be recorded separately.

The owner must explicitly accept either the current **time-bearing zero-historical-LIST/data-part
invariant with separately reported static census**, or keep literal whole-catalogue zero LIST
blocked for the static-coverage owner. Do not declare the original whole-catalogue gate passed
by excluding static work silently. Likewise, do not require one generation GET for a valid rollup
hit merely because the older specification predates the optimization. Approval of this contract
disposition does not authorize changing coverage publication or the registry.

## Product/day/rung completion matrix

Start from [the retained 85 observations](public-reader-observations-20260911.json). Pin each row
to physical lane, declared source footprint, requested day, served day, zoom/rung, support,
terminal state, truncation and response hash. Fill missing cells in a declared matrix before
spending requests; stop on any unsupported source footprint rather than searching the world.

- Add z5, and exercise the currently unmeasured climate variants: air-temperature min/max;
  dew point, precipitation, relative humidity, shortwave radiation, wind speed; soil-wetness
  surface/root-zone/profile. Complete the other rungs for variants that have no retained sample.
- Add soil moisture root-zone/deep, every declared soil-temperature depth and VPD. Map public
  parameter IDs to the current physical lane catalogue; do not equate one shared route with
  all of its variants being tested.
- Obtain actual historical positive rows for the graduated temperature lanes. The August 6
  Boise samples are published-empty, not positive-history proof. Choose at most two locations
  per historical product from its own retained source footprint/receipt; an MTBS footprint is
  not evidence of a temperature footprint. If no admissible location is known, name the source
  owner and retain that matrix cell as unproved.
- Require at least one positive and one bounded-empty viewport per supported reader family,
  then representative governed absence, unwritten day, missing lane/rung, payload refusal and
  truncated answer. Select terminal days from current availability/source receipts; never
  manufacture an absence to make a test case.
- For explicit today, verify the candidate's water/weather private request is `/day`. An omitted
  date may retain its live freshness window. Baseline `fa20223` takes a two-day `/window` for
  explicit today, so its public response cannot prove the correction.
- Preserve soil-survey's typed withholding until its owning static-admission/reader work proves
  a serving surface. A failed or empty static request does not license a PostgreSQL fallback.

The bounded responses already found these open checks:

| Finding | Required disposition |
| --- | --- |
| MTBS source extent `[-125,42,-111,49]`, September 11: 746 rows at z0 versus 747 at z9, both `truncated=false` | Multiscale owner checks support identities, source weights and clipping before deciding whether the counts conserve the same population; discrepancy alone is not established data loss |
| Same MTBS extent at z13: explicit upstream byte-limit refusal | Record the refusal as a correct bound; assess whether that extent/zoom should be supported and whether smaller admitted detail views work |
| Boise MTBS z13: one explicit upstream HTTP 503 fault | One bounded retry in the same admitted bbox with stage/timing evidence; do not infer universal z13 failure from two different refusals |
| Colorado MTBS requests: `truncated=true`, outside admitted source extent | Preserve the descriptor and incomplete-coverage disclosure; do not accept this as complete global burn-history coverage |
| Watersheds z13: first HTTP 503 upstream fault at about 14.61 seconds, repeat published-empty at about 13.17 seconds | Correlate one bounded retry with existing admission/timing logs and retain intermittency; do not increase timeouts or memory as part of evidence collection |

## Browser and neighbour acceptance

For each representative point, polygon and field family, retain a browser request-to-paint
sequence with the selected day, requested day, response served day, painted-data day, viewport,
map zoom/physical rung, terminal/truncated state and visible caption. Include one ordinary pan,
one date scrub, and crossings of 5, 9 and 13 in both directions. Panning must change bbox without
changing selected day; scrubbing must change day without requesting an unbounded collection.
Fire pixel requests must use `wildfire.getFireDetections`; the trace must contain no `/api/fires`
request. Record this live-request proof separately from the deleted route's repository proof.
Prior painted data while a request is pending must not become the answer for the newly selected
day. Verify source ceiling and coverage authority in visible and accessible caption text, including
drought's separately named carried read-through date. Capture one superseded request and trace
its downstream cancellation/admission lifecycle; browser cancellation alone does not prove the
worker stopped. Shared capability warming intentionally retains independent cancellation.

Neighbour APIs answer different questions and need separate verdicts:

| Surface | What must be proved |
| --- | --- |
| Candidate Next.js `getRegionalTemporalNeighbours` inside regional context | Viewed fire/gauge/weather dates, availability-only candidate selection, excluded gaps/absences, at most one candidate per side/source and six reads total; returned actual values carry their own observed day and signed/absolute temporal and spatial distances |
| Empty local Next.js candidate | Explicit “no local observation on nearest globally published day”; it must not claim the nearest local observation anywhere in the 180-day window |
| Unsupported/conflicting Next.js inputs | Release/static/non-Parquet layers and conflicting source aliases refuse explicitly; missing/withheld authority causes zero candidate row reads |
| Python `query_observation_temporal_neighbors` | Globally covered before/after dates from availability; this metadata result does not itself retrieve the local measured values of the Next.js helper |
| Python `query_signal_neighbors_in_time` | Actual admitted signal values within its declared spatial/temporal/partition bounds; retain scanned-window narrowing and support refusals instead of describing an unbounded nearest result |

The existing public `/api/ai/regional-intelligence` endpoint is a POST that reserves quota and
opens a conversation before model work. It is not included in this read-only GET packet.
Use existing model-free reader diagnostics for context proof when already authorized. A live AI
UI request requires a separately stated authorization naming the test account, coordinates,
one-request quota/conversation effect and model invocation; no authorization is inferred from
the ability to browse the map. Do not create a temporary public neighbour endpoint for testing.

## Stop conditions and delivered evidence

Stop the affected operation immediately on unexpected deployment/configuration identity,
credential exposure, a write-capable diagnostic path, time-bearing capability historical LIST or
data-part access, an environmental PostgreSQL observation fallback, unbound day/viewport/rung,
discarded terminal evidence, or silent truncation. Also stop on the session budget, repeated
transport/capacity failure, or unexpected resource pressure. Preserve the first refusal and one
bounded comparison where permitted; do not enter a retry loop or widen the query to obtain rows.

Write a new dated receipt directory under this track; never overwrite September 11 observations.
Deliver the fresh deployment/configuration receipt, request matrix with missing cells, sanitized
trace/counter records, timing stages and unavailable measures, response bodies or lossless archives
with hashes, browser screenshots, cancellation lifecycle, neighbour outcome packet, source/MTBS
conservation owner findings, and a per-gate pass/refuse/unproved decision with exact limitations.
Include request counts and any stop condition. A scoped result is not project-wide acceptance.

This packet implies no deployment, merge/push, publisher/bootstrap/rollup rebuild, availability
pointer edit, scheduler action, database write/migration/restore, object-storage write or retired
PostgreSQL reader restoration. Any later release approval must separately name the final reviewed
candidate and its normal release path. Rollback must reverse only that candidate's reader commits
or name a freshly verified schema-compatible deployment. The historical `2b4cfef..HEAD` revert is
not valid rollback authority after environmental PostgreSQL retirement.
