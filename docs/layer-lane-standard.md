# The Layer Lane Standard

> **RELATIONSHIP NOTE — added 2026-08-22, body below untouched.** A same-day naming
> collision: `conductor/code_styleguides/layer-lanes.md` ("PlantGeo layer-lane
> standard") was created 2026-08-22 alongside the architecture pivot
> (`conductor/RUNBOOK.md` §0.23–§0.24, binding per §0.24.6). It does **not**
> reference or explicitly supersede this file — the two were reconciled during a
> docs audit, not by either author. Both files even carry the same OKF frontmatter
> `type: layer-lane-standard`, which is a tooling/naming collision, not evidence
> either document claims to replace the other.
>
> **They describe two different architectures for the same question — "when is a
> layer done?" — and neither fully supersedes the other:**
> - **`conductor/code_styleguides/layer-lanes.md` is authoritative for structure
>   under the new Parquet/DuckDB architecture**: the `lanes/<layer-slug>/`
>   directory shape, the `kind=observed`/`kind=forecast` Parquet split, Monte
>   Carlo provenance columns, and the `lanes/` ↔ `ml/` import boundary. Use it for
>   any new lane work.
> - **This document (`layer-lane-standard.md`) is SUPERSEDED for its
>   Postgres-specific mechanics** — §1's declaration against
>   `agri.data_source`/`agri.spatial_cell`, §3's four Postgres planes
>   (`agri.signal_observation`, `agri.forecast_observation`, `geo.features`,
>   `geo.drought_areas`), §8's Postgres-ledger-driven cron triad, and §14's
>   Postgres connection facts describe the architecture the pivot is leaving.
> - **This document remains CURRENT on process-level outcomes that
>   `layer-lanes.md` has not yet restated for Parquet**: §0's zero-row-success
>   principle, §5–§7's gap-detection-as-a-loop and governed-absence discipline
>   (partially restated in `layer-lanes.md` §4, but the work-item/cron mechanics
>   are not), §9–§9.1's serving/catalogue-registration mandate, §10's time-slider
>   wiring, and §11's agent-tool exposure contract. A Parquet lane still owes
>   these outcomes; nothing in the new architecture has replaced them yet.
>
> Read `conductor/RUNBOOK.md` §0.23 (the pivot decision) and §0.24 (the stream
> plan) before treating either document as a complete contract on its own.

> **Scheduler correction, 2026-09-02:** §8's three-Railway-cron prescription is superseded.
> A layer still owes the three logical duties—forward refresh, gap authoring/reconciliation and
> coverage status—but they are registered durable lanes inside the single continuous
> `plantgeo-job-executor` service. No tracked `cronSchedule`, Railway cron service, or reconnectable
> drain is permitted. Rollback disables the affected executor lane and never recreates a cron.

One contract per layer, seven planes deep. A layer is **not done** until every
section below is satisfied — the recurring failure in this repo is a layer that
is finished at one plane and silently absent at the next.

## 0. The first principle

**A lane that reports success having written nothing is worse than a lane that
fails.** `backfill_outcome` (`ingest/archive_walk.py:552-607`) encodes this three
distinct ways for three distinct zero-row cases, and every distinction is
load-bearing. Preserve it in anything new.

Corollaries, each learned from a measured incident:

- **`partial` is never `complete`.** A settler that accepted partial coverage
  writes a silent hole back in with no failure file to contradict it.
- **A span no upstream covers must WARN, never return clean-empty.** FIRMS
  resolved products from `start_day` alone and returned `records_seen=0` for
  days a product did cover — indistinguishable from "no fires that week"
  (`ingest/AGENTS.md:113-122`).
- **Never fabricate a date.** MTBS refuses a fire year with no dated release
  announcement rather than falling back to `Ig_Date`, `now()`, or an assumed
  lag. Generalize that refusal, don't special-case around it.

## 1. Declare the lane

One record, one place. Do not spread a layer's identity across `BackfillLane`,
`StreamDefinition`, `LaneCoverageContract` and a `railway.json` — that is how
`source_key` drifted to a value matching no row in `agri.data_source`, so every
reconciliation over it silently returned empty.

A declaration carries: layer/stream name, producer token, source key (**verify
against `agri.data_source.key`, never infer**), grid name and support key
(**verify against `agri.spatial_cell.grid_name`**), forward cadence, publication
cadence **with a measured `cadence_basis` string**, history horizon, chunk/window
shape, credential env var, record cap, and permitted render forms.

Horizon policy is **per-lane configured, max-available**: each lane declares how
far back it must reach, chosen from what its upstream actually serves. Never a
global constant, never derived from the data — a horizon read from the data
makes the contract unfalsifiable, because a lane that lost its first year would
re-contract itself to its own truncated history and report 100%.

## 2. Declare the horizon, two levels

Modelled on FIRMS, which is the only lane that gets this right:

1. **Static `earliest`** for the outright refusal — cheap, needs no client,
   consulted before any fetch exists.
2. **Optional live `resolve_availability(client)`** for upstreams that publish a
   coverage table. Resolve **per chunk**, and **union over every day of the
   span**, never from `start_day` alone.

`HistoryCapability` is **mandatory on every source**, including a typed refusal:
`supported=False` requires a `reason` naming the actual upstream limitation.
A layer with no declared horizon is the exact failure `HistoryCapability` was
written to prevent, wearing a missing declaration.

## 3. Write to exactly one plane

Know which, and never audit across only one:

| plane | what lands there |
|---|---|
| `agri.signal_observation` | NASA POWER, Open-Meteo archives |
| `agri.forecast_observation` | Sentinel-2 NDVI |
| `geo.features` | every map feature producer |
| `geo.drought_areas` | USDM (polygons, its own store) |

A census over one plane reports healthy lanes as dead. That false positive has
already cost one wasted subagent dispatch.

Identity is minted in exactly one place (`ingest/identity.py`), namespaced by
producer token and never the layer name. Raise `MissingNativeKeyError` rather
than synthesising a key from coordinates, a payload hash, a UUID or the clock.

## 4. Separate "when it happened" from "when we could have known"

`observed_at` is the observation time. `data_available_at` is publication time.
`created_at` means "last touched" and may **never** be used to derive lag.

This is a leakage boundary, not bookkeeping — this data trains models. A release
date is looked up or the record is refused. Back it with tripwires: the release
must lead the cohort's last observation by a plausible margin, and must not fall
within seconds of `now()`.

## 5. Detect gaps automatically

One gap rule for the whole repo. `ingest/validation/completeness.py` is the
engine — pure, cadence-aware, floor-aware, no I/O. Anything that re-implements
covered/partial/absent is a weaker special case and should call into it.

- Walk the **stream's own cadence grid**, not the calendar. A gap opens only
  when silence exceeds one cadence period.
- Clip days below the declared floor into their own bucket — neither gap nor
  coverage. Skipping this turned 1,017 real missing days into a reported 12,611.
- Observed-day census is **one** canonical SQL statement. It filters
  `status = 'published' AND geometry_id IS NOT NULL`; a census missing the
  geometry predicate reports days the slider cannot reach.

## 6. Turn a gap into work — the loop

This is the step the repo has historically lacked entirely. Detection that no
verb consumes is not gap-handling.

```
forward refresh  ->  observed days
                          |
                   coverage contract  ->  required days
                          |
                     gaps = required - observed - governed absences
                          |
              author work (shards on the lane's OWN floor-anchored grid)
                          |
                        run  ->  reconcile  ->  status
```

Rules:
- Map gaps onto the lane's **existing** grid. Never invent a second grid, never
  plan a trailing partial window — that is what makes replanning a no-op.
- Author work idempotently (`ON CONFLICT DO NOTHING`), and make reopening a hole
  inside an already-succeeded run work. A hole that no verb can reopen is a
  permanent hole.
- **Dry-run by default**, `--apply` to write, on every verb that mutates the
  ledger.
- Exhaustion is a dead letter, never a silent success — a completeness report
  must be able to read it as missing.

## 7. Record what upstream cannot serve

A day the upstream genuinely has no data for is **not** a gap and must stop
being re-walked forever. Record it as a governed absence in
`agri.signal_coverage_audit` (`status ∈ complete|partial|no_data|failed`).

**This table already exists and is populated. Build on it; do not add another.**
A day both observed and marked `no_data` resolves to covered.

## 8. Register all three duties in the sole executor

Every lane family needs **three durable executor duties**, not three service objects:

| registered duty | cadence | purpose |
|---|---|---|
| forward refresh | the lane's own | keep the live edge current |
| gap-fill | daily | plan gaps, then reconcile-apply |
| coverage-status | daily | report; the only liveness signal |

Executor requirements that cost real hours when missed:

- Register the source cadence and phase in `job_executor_service.py`; do not add a
  `cronSchedule` or another continuously looping writer.
- Select an explicit catch-up policy: source polls normally coalesce to the latest
  due bucket because the source command owns its settlement window, while ledger-backed
  backlog duties replay the oldest missed bucket first.
- Each invocation must use the `agri.job_*` ledger, fenced lease, resumable checkpoint,
  bounded retry/dead-letter policy and idempotent domain publication. The continuous
  executor is the trigger; the ledger is the durable schedule and recovery record.

## 9. Serve it — and register it in the catalogue

Shipping the reader is not shipping the layer.

**The catalogue is the outer relation of a `LEFT JOIN`.** A stream the
observation query emits but the catalogue omits is *silently dropped*: the layer
paints tiles happily and reports zero history, so no time slider ever mounts.
This shipped once already — nine climate streams, invisible sliders, tiles
rendering fine, and the first investigation cleared it because it checked
`hasSelectableDay` and the density floor but not the join.

So, mandatory:
- add the stream to the capability catalogue, **generated from the same constant
  the observation subquery uses** so the two halves cannot drift;
- assert it in a test against a **hand-spelled** list, never against the shared
  constant — importing it lets both sides drift together and still pass;
- assert the catalogue's **bound parameters**, not rendered SQL text: stream
  names are bound params and a text-only assertion cannot see them.

Serving readers resolve a requested day through their own backward window. That
is why a dropped catalogue entry looks like missing data and not a dropped join.

### 9.1 Three ways a toggle may resolve, never a fourth

`warehouseLayerName` on a registry entry must resolve exactly one of three ways.
Anything else is a silent drop, not a variant:

1. **A real catalogue row** — a `geo.layers.name` or a `SLIDER_STREAM_LAYER_NAMES`
   entry.
2. **A declared snapshot** — `SNAPSHOT_SURFACE_LAYER_NAMES`
   (`src/types/time-slider.ts`), for a capability the resolver registers by name
   with no observation window at all. `strategy-recommendations`
   (`layer-registry.ts:401`) was the live instance of this bug: its name matched
   neither a layer nor a stream, so it dropped out of the LEFT JOIN silently until
   the 2026-08-15 pre-aggregation-layer slice named it here.
3. **`null` with a reason** — `permanentlyUnavailableReason` set (a withheld
   capability), or the toggle is genuinely axis-less by design (a live aggregate
   with no per-day history, e.g. `demand-heatmap`) and is listed as such in the
   conformance test below.

`soil-survey` was the other live instance, and a different shape of the same bug:
`0013_soil_survey_persistence.sql` gave it a real `geo.layers` row on 2026-08-05,
and the registry kept `warehouseLayerName: null` anyway — neither withheld nor
declared, just stale relative to what its own upstream migration had already
shipped.

A conformance test walking every registry entry against these three states is
what catches the next one:
`src/__tests__/services/environmental-read-model.test.ts` (the registry cross-
check) and `src/__tests__/services/pre-aggregation-catalogue.test.ts` (the
24-name catalogue itself, cross-checked against the `geo.layers` seed migrations
and the census matviews' own DDL). Extend the hand-spelled lists in both when a
25th name is added; never import the constant under test to check itself.

## 10. Wire the time slider

Every layer with history exposes a slider axis: earliest day, latest day,
coverage gaps, thin ranges, and a density floor. A layer whose upstream is
genuinely current-only declares that (a snapshot/reference kind), rather than
appearing to have an axis it cannot fill.

Per-request coverage counts belong to the **served collection**, measured per
request — never a frozen constant. A constant cannot track a growing lane: a
hard-coded "4 of 397 cells" rendered directly above a live "267 cells drawn"
in the same card.

### 10.1 Persist slider availability; never rediscover history per request

Every time-bearing Parquet lane publishes an immutable generational
`availability.parquet` plus a checksum-bound `_LATEST.json` pointer. The index has one row per
`(day, rung)` and carries terminal state, row count, source/terminal/data/completion receipts and a
nullable governed-absence reason. The file metadata and pointer bind the authoritative ordered
`required_rungs`, so an omitted rung cannot redefine the contract. The slider reads the pointer and
the named Parquet object; it does not list day prefixes, open historical data parts or query
PostgreSQL.

Bootstrap may perform one exact historical inventory against already verified manifests. It writes
an immutable receipt with the source inventory root, required rungs and manifest/checkpoint inputs;
the pointer binds that receipt's key and SHA forever. After that, forward ingestion, backfill and
governed-absence publication extend the previous generation only after their data and completion
markers are durable. Publish the new immutable index, verify its bytes and SHA, then advance the
pointer last with a conditional write. Corrections make another generation; old generations remain
rollback evidence. A missing or invalid index is an explicit refusal, never permission to run the
historical census in a request path.

## 11. Expose it to the agent

A layer is not finished when it renders. The agent must be able to ask:

1. **Value at the UI-selected time** — the agent reads the *same* selected day
   as the map. An agent answering from the live edge while the user looks at a
   past day is answering a different question than the one asked.
2. **Temporal proximity** — nearest observations before/after the selected day,
   with their real distance in days, so the agent can say "nearest reading is
   six days earlier" instead of implying an exact match.
3. **Spatial proximity** — nearest cells/features to a point with their real
   distance, so relevance is earned rather than assumed.

Always return the distance and the observation's own date alongside the value.
An agent tool that silently substitutes a neighbour for an exact answer is the
same class of bug as a lane reporting success having written nothing.

## 12. House style

- `mypy --strict`. No `Any`, no bare `type: ignore`, no broad `dict[str, Any]`.
  Untrusted values arrive as `object`, are validated, then exposed as a named type.
- `dataclass(frozen=True, slots=True)`, `Final` constants, `tuple` over `list`,
  `Literal` for closed sets.
- **Terse one-line doc-comments only.** Rationale, contracts and measurements go
  in the directory `AGENTS.md`, with a one-line pointer from code.
- Every constant carries a **measurement or citation, with a date**.
- Env vars read at call time, never at import — an executor configuration change must
  need no image rebuild.
- SQL lives in `sql/<package>/<name>.sql`, loaded at import. Line 1 is a bare
  `-- <marker>` the tests dispatch on. Header: Purpose / Loaded by / Params, then
  a **clause-by-clause plain-English walkthrough written for someone who does not
  know SQL**. **Never write a colon immediately followed by a word character in a
  SQL comment** — `text()` scans comments and mints a phantom bind parameter.
- Unit tests carry no database; the seam is `AsyncSession.execute` answered by
  statement marker. Cover the success path **and** the failure/partial path.
- **One sweep at the end**: format, lint, typecheck, test — once, after all edits.
- Deviations get appended to the deviations list in `AGENTS.md`, never buried in
  a code comment.

## 13. Definition of done

A layer is finished when all of these are true:

- [ ] lane declared once, with `source_key`/`grid_name` **verified against the DB**
- [ ] `HistoryCapability` declared — a horizon, or a typed refusal with a reason
- [ ] forward refresh is a registered, bounded `plantgeo-job-executor` lane
- [ ] gap detection is a registered executor lane and **authors work**, not just a report
- [ ] unfillable days recorded as governed absences, not re-walked forever
- [ ] coverage-status reports completeness, missing-day count and collapsed ranges
- [ ] serving reader exists **and the stream is in the slider capability catalogue**
- [ ] availability index is bootstrapped once and extended by every terminal ingestion/backfill
      outcome; slider reads require no historical listing or data scan
- [ ] time slider mounts, with a real axis or an honest snapshot/reference declaration
- [ ] agent tools answer at the selected day, with temporal and spatial neighbours
      carrying their distances
- [ ] tests cover success and failure; full sweep green

## 14. Verified environment facts

- Prod warehouse: the loader DSN on the Railway public proxy. Alembic reads
  `DATABASE_URL_SYNC` and **targets production** — never run `upgrade head` casually.
- The SQL-contract suite needs `PLANTGEO_TEST_DATABASE_URL`
  (`postgres://postgres:526152@127.0.0.1:5433/postgres` — **port 5433**, 5432
  rejects that password). **Without the env var it skips silently**: nine tests
  that look green and never ran.
- That suite uses a PostGIS shim (a geometry domain over text), not real PostGIS.
  A new `ST_*` call needs a stub added alongside `st_x`/`st_y`.
- `agri.data_source.key` values: `nasa-power-daily`, `open-meteo-era5-archive`,
  `open-meteo-era5-land-archive`, `sentinel2-ndvi-l2a`.
- `agri.spatial_cell.grid_name` values: `sentinel2-ndvi-0p25deg` (1,568 cells),
  `nasa-power-0.5-degree` (397).
- Wind **direction is not ingested** — NASA POWER requests eight parameters and
  `WD2M` is not among them. Wind is a scalar; barbs are impossible without a new
  source.
