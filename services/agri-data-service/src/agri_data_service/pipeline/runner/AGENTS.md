# `pipeline/runner/` — the config-lane runner

Track `config_driven_ingestion_20260926`, plan 1B (`f1-runner`), spec §4.2–§4.3, S4–S6, S11, S14,
S19, FR-3–FR-5, FR-15, FR-38, CA17, CA20. **Dark in Phase 1:** no `lanes/*.toml` exists yet, so
nothing dispatches this package in production. Code carries the one-line "what"; the "why" is here.

```
python -m agri_data_service.pipeline.runner --lane <id> --mode forward|gap-fill|transform
    [--compare] [--republish-current] [--weighted-budget N] [--run-id ID]
```

| module | what |
|---|---|
| `contract.py` | the §4.2 Protocols and value types; the ONLY runner module a strategy imports |
| `resolve.py` | S14: strategy key -> `pipeline/lanes/<layer>/<source>.py::STRATEGY` |
| `windows.py` | which days a turn asks about: forward + S19 gate, gap-fill holes, transform dirty days |
| `budget.py` | per-turn per-mode caps, the ledger, `BudgetedClient`, `select_affordable_days` |
| `fetch.py` | the per-unit retry ladder and the quota circuit |
| `cooldown.py` | per-host persisted Retry-After waits ("throttled until"), honoured by later turns |
| `checkpoints.py` | source checkpoints over `pipeline/parquet/source_checkpoint.py` |
| `census.py` | the full-ladder census |
| `receipts.py` | per stream-day turn receipts (S11 digests, units, input digests, pruned inputs) |
| `completeness.py` | bounded yearly proofs of complete source support; absent proof remains source debt |
| `reader.py` / `writer.py` | the read and write ports, and their `pipeline/parquet` bindings |
| `digests.py` | table and response digests |
| `turn.py` | `run_turn`: one turn, start to finish |
| `report.py` | the S5 report and the bounded turn log |
| `exits.py` | S4 codes and the one error -> code mapping |
| `binding.py` | production collaborators (bucket, loader session, metered HTTP client) |
| `__main__.py` | the command |

## The contract

`contract.py` is a DRAFT (plan 1B); the Phase-2 re-freeze fixes it from landed code (spec M1).
Deviations from the spec §4.2 text, each deliberate:

- `ProviderClient.get(endpoint, parameters, *, probe)` returns a `ProviderResponse` (body bytes, the
  credential-free URL, the retrieval instant). A strategy names an endpoint from its provider TOML,
  never a host; the client picks the free or customer host and adds the key.
- `probe_edge(client, window)` takes the runner's `ProbeWindow` (at most 14 days ending at the
  candidate edge) so the strategy never re-derives the window. It lives on its own
  `EdgeProbingStrategy` Protocol so `isinstance` checks S6 without a second registry.
- `rows(day, responses)` may return one table or a mapping stream slug -> table (a lane may write
  several streams, spec §6.1). `Derivation.table` takes the same two shapes; a transform may answer
  a subset of its streams on a day (an output whose inputs have not published yet).
- `DayContext` adds `output_streams` and `input_streams` (a transform's input lane -> its streams,
  in precedence order), which the generic precedence transform routes by.
- `SourceRequest.unit` is unique within a turn: checkpoints, selection and outcomes key on it.
- `ReleaseCalendarStrategy.release_days(first, last)` is optional: a `release_series` owes only
  those days.
- `DayContext.planned_units` (w3 review H1): every unit the runner planned covering the day,
  answered or not, so a `write_and_recheck` strategy states `expected_units` from what was planned
  rather than from what answered.
- `Written.dropped_rows` (w3 review H1/M1): source rows the strategy dropped for the day, by reason
  (a feature its page contract refused, an identity two copies disagree on). The runner sums them
  into the S5 fact `rows_dropped_by_reason`, so a drop is counted where the executor reads, not only
  logged.
- `Written.source_resolved` (post-push review 696f1ae5..21ec86ce, H1): `False` when the strategy
  dropped rows it cannot resolve (water: two series under one identity). The day is still written
  from the rows it kept, but it never earns a completeness proof; see "Unresolved days" below.

Typed fetch errors (`SourceThrottledError`, `SourceUnavailableError`, `TurnBudgetExhaustedError`,
`ProviderConfigurationError`) never reach `settle`; `ingest/http.py`'s own typed errors are
classified the same way (`fetch.py::classify_fetch_error`).

## Resolution

S14 by `importlib`: `strategy = "soil.open_meteo_era5_land"` imports
`agri_data_service.pipeline.lanes.soil.open_meteo_era5_land` and reads `STRATEGY`. There is no
registry to edit, and nothing shared lives beside the lanes in `pipeline/lanes/` except
`transforms/` and `AGENTS.md`, because `tests/test_layer_import_contract.py::_lane_names` treats
every module there as a lane. `pipeline/runner/` is not sibling-policed; it sits in the `pipeline`
layer. A missing module, a missing `STRATEGY`, the wrong shape for the lane's kind, or a settled
weighted lane firing more than once a day without `probe_edge` (S6) is `StrategyResolutionError`
(exit 78). `resolve_strategy(package=...)` exists so tests resolve fixture strategies
(`tests/runner/fixtures/`) the same way.

## Windows

- **Forward** = `[edge - absence_recheck_days + 1, edge]`, edge = `today - publication_lag_days`;
  a `write_and_recheck` lane never reaches past yesterday UTC (O1). A refuse lane owes its missing
  and incomplete days; a `write_and_recheck` lane re-asks every window day (the rolling
  reconcile), and the rewrite rules decide what is written.
- **A day whose base rung is published but whose coarse rungs are not owes ladder work only**
  (`census.py::LaneCensus.base_data_days`): it is never fanned out upstream. The turn itself
  repairs it after the fan-out (`turn.py::_Turn._repair_ladders` -> `writer.py::repair_ladder` ->
  `pipeline/parquet/gap_fill_repair.py::repair_one_lane_day`: derive the coarse rungs from the
  published base under the lane-day lock, touching no adapter, then claim the day for the index).
  There is no other repairer: the generic `parquet-gap-fill` lane that ran `run_gap_fill`'s ladder
  queue was retired (`execution/lane_specs.py`). Counted as `days_ladder_owed` and
  `ladder_repairs`; a repair the lock refuses is `contended`, one that fails (or that starts past
  90 % of `turn_timeout_seconds`) is `ladder_owed`, both behind the edge so the alarm sees them
  (review H1). Refuse lanes only: a `write_and_recheck` lane re-asks every window day anyway.
- **The S19 probe gate** (`plan_forward`, G0's `_probe_gate` generalised): with `probe_edge`, the
  turn spends the probe first. An owed day inside the probe window fans out only when the probe
  shows it valued. An unvalued day NEWER than the probed edge is `unsettled`
  (`newer_than_probed_edge`, `behind_edge = false`: not an alarm). An unvalued day OLDER than the
  edge is also held (a probe null is never fanned out) but counts (`probe_null_behind_edge`).
  `unavailable` / `deferred` / `blind` gate the whole probe window; days older than the probe
  window are walked whatever the probe said (O-R3-1). A probe the turn's own cap refused is
  `deferred`, its gated days `deferred_budget`.
- **The shortwave livelock** (the reason for all of the above): days past a stuck edge cost
  nothing, and the cap is spent oldest-first on days the provider can answer.
- **Gap-fill** = the window before the forward window, back to `[days] floor` (or the source's
  earliest day), capped at the census's `MAX_GAP_WINDOW_DAYS`. Refused unless
  `gap_fill_enabled` (S12). Holes older than the source's history are `retention_exceeded`; more
  than `GAP_FILL_MAX_DAYS_PER_TURN` wait (`deferred_budget`). Oldest first (spec §4.3; the §6.3
  newest-first re-pull needs an `order` field the schema does not have yet).
- **Transform** = the forward window's days whose input digests differ from the transform receipt.
- **Rolling revision** (S11 for late revisions; `revision_block`, w3 review H2): a lane with
  `[days] revision_window_days` re-asks, on every forward turn after the forward fan-out, one block
  of `revision_days_per_turn` PUBLISHED days in `[edge - revision_window_days + 1, window.first - 1]`.
  Blocks are anchored to the day ordinal (a block keeps its days as the window slides) and block `b`
  is the turn's when `b = today (mod revision_rotation_days)`, so every block is re-asked exactly
  once per rotation (19 turns for 550/14/31). Only lane-data days are re-asked (holes are
  gap-fill's); a digest change rewrites one, and nothing about a revised day is ever reported
  unwritten. It spends what the forward fan-out left of the cap, re-buys its own per-unit support
  (water: the names units), is skipped by `--compare` and after an exit-75 fan-out, and is reported
  as `days_revised`, `revision_first`, `revision_last`. This is the "monthly 90-day revision sweep"
  of spec §6.2 as a rotation rather than a mode: no new cron, no executor definition, a bounded
  per-turn cost.

## Budget

`mode_cap`: `forward_max_weighted_calls` or `gap_fill_max_weighted_calls`, lowered (never raised)
by `--weighted-budget` (executor admission, WQ-4, or an operator). On a weighted provider the cap
counts weighted calls priced by `foundation/observability/usage.py::open_meteo_weight_for_url` —
the one formula; this package never writes a second one. On an unweighted provider it counts
logical requests. A logical request is charged once, before it is sent (`BudgetedClient`); a retry
of the same unit costs a fetch attempt only. The probe and a fan-out unit are two sends even with
identical parameters, so the probe flag is part of the ledger key. `select_affordable_days` takes a
day whole or defers it whole: a day's units must all fit, a unit shared with an already-taken day
is free, and a checkpoint-restored unit is free.

## One retry ladder

Spec D2 gives the per-unit retries and the 429 series to the runner: `fetch.py::UnitFetcher` is the
ladder (5xx/timeout/transport: 3 attempts, doubling from 2 s with jitter; 429: 20/40/80/160 s, then
`deferred_quota` and the quota circuit opens for every unit not yet sent). A `Retry-After` replaces
a series step, clamped to `[step, 2 × step]` through SOFT-8's one Retry-After rule,
`ingest/upstream_retry.py::clamped_retry_after` (the legacy `retry_upstream` ladder clamps through
the same helper into `[0, max_delay]`). So the production client (`binding.py`) makes ONE attempt
per call through `ingest/provider_client.py::send_provider_request` (plus `fetch_bounded`'s own
transport re-sends) and never retries a status itself; `provider_client.fetch_single_location`'s
own ladder is for a non-runner caller and is never stacked under the runner. A unit that exhausts
its ladder never discards its siblings; a configuration error cancels every sibling still sending
before it propagates. A rejected key (401/403) or an empty required key is
`ProviderConfigurationError`: the whole turn, exit 78.

**A stated wait the ladder will not honour outlives the turn** (post-push review M3). The clamp
above meant a `Retry-After: 3600` was retried after 20-40 s, and nothing carried the wait into the
next turn, so keyless USGS gap-fill could hold the executor's single queue slot (DISPATCH=queue,
one lane at a time) for about 1.5 h a day. Now `UnitFetcher.fetch_unit` opens the quota circuit at
once when a stated `Retry-After` exceeds `RETRY_AFTER_CEILING_FACTOR` x the current step, would pass
the turn's fetch deadline, or arrives after the series is spent (`fetch.py::exceeds_ladder`): the
unit and every unsent sibling are `deferred_quota`, and nothing is slept. A probe's 429 takes the
same test at the first step (`turn.py::_Turn._spend_probe`), so a probing lane (Open-Meteo, POWER)
told to wait an hour sends nothing more that turn, `_revise` included, and does not re-probe on the
next fire. `fetch.py::hold_stated_wait` records
`now + min(Retry-After, 24 h)` per host through `cooldown.py::ProviderCooldowns` at
`lane-provider-cooldowns/v1/<host>.json` in the availability storage, the persistence receipts and
checkpoints already use (CAS-merged, the later instant wins). The next turn of ANY lane on that host
(`turn.py::_Turn._honour_cooldown`, keyed by the lane endpoint's free host, the backoff meter's key)
opens the circuit before its probe or fan-out: zero requests, every owed day `deferred_quota`, exit 0,
`throttled_until` in the report. CA20's claim retry and ladder repairs still run (they send nothing
upstream). The cron is unchanged; a fire inside the wait is simply cheap. A cooldown is advisory: an
unreadable document fails open (the turn sends), and a failed write is noted in the fetch tally,
never a unit failure. `--compare` honours a cooldown but never writes one. A 429 without
`Retry-After` still walks the 20/40/80/160 s series and persists nothing. A key set during a keyless
cooldown takes effect when the cooldown expires (at most one skipped fire). The trade-off: a stated
wait above 2x the current step (41-160 s on the first 429) now ends the turn's sending where it was
once waited for about 40 s; watch Open-Meteo's minute-level limits for turns that stop early.

## Census

Four rungs per stream-day, folded (`fold_ladder`): `data` only when every rung is `data`; an
absent base under coarse rows, or a rung with both data and absence, raises (exit 70). Listing is
by month prefix for a short window and by year for a long (gap-fill) one — forty years are forty
listings per rung, not four hundred and eighty.

Source coverage and zoom-ladder completion are separate. A strategy implementing
`SourceCoverageStrategy.source_unit_ids(lane, region)` declares the stable source support expected
for every day; `Written.expected_unit_ids` must match it, and `present_unit_ids` identifies the
subset that answered. The runner passes that support into `LaneReader.census` as
`expected_unit_ids`. The returned `source_owed` mapping includes every published base day without
a matching full-support proof, even when all zoom tiers are present. `owed_days()` includes this
debt; only a base day with no source debt is repairable as ladder-only work. The report separates
`days_source_owed` from `days_ladder_owed`. A history-depth audit must use this support-aware census,
not the partition-only `read_census` function. Source-owed days never restore old checkpoints.

Proofs live at the managed infrastructure namespace
`lane-source-completeness/v1/<stream>/<year>.json`; this is runner evidence, not a data stream and
not disposable scratch. Each strict versioned document binds its stream and year and contains at
most 366 complete dates, each bound to a digest of the expected unit identities. Missing dates,
missing documents and changed support mean owed work; corrupt or unreadable documents fail the
turn rather than clearing debt. A 36-year re-pull needs 37 small proof reads instead of one GET
for every historical receipt. CAS retries merge independent day updates within the same year.

The writer holds the existing session-scoped lane-day lock while invalidating the prior proof,
writing the intended receipt with `publication_state = "pending"`, publishing data, replacing that
receipt with `publication_state = "complete"`, and finally confirming full source support. The
pending receipt is durable before any partition mutation and preserves the maximum intended
coverage when the final receipt write fails. While it is pending, every partial replacement is
refused; only a fully answered publication can resolve the uncertain relationship between stored
rows and their receipt. Pending is never an equal-digest or source-completeness proof. Recovery
retires the pending state by replacing the same receipt after successful publication, so no extra
journal namespace or cleanup job is introduced. Receipts without this additive field decode as
complete for backward compatibility. A crash between these operations leaves source debt. Receipt
equality alone cannot repair a missing proof: the next fully answered turn republishes the day.
Compare mode only reads these documents. Counts-only old receipts remain decodable, but cannot
establish support identity or authorize an intermediate partial replacement. A fully answered
replacement upgrades them. For identified coverage, a partial rewrite needs a superset of the
previously published identities; a larger count with a different missing tile is refused and
reported, preserving previously served gauges until a coverage-preserving answer arrives.

**Unresolved days** (post-push review H1). Evidence `phase3.md` found 341 identities of
`USGS-12010000` (two time-series IDs, 1990-09-30 to 1991-09-05) dropped as identity conflicts while
`confirm` still issued 310 full proofs, all outside the 550-day revision window, so they would never
be re-pulled. Now `Written.source_resolved` rides into `DayReceipt.source_resolved`, and
`SourceCompleteness.confirm` writes `completeness.py::unresolved_digest(expected)` instead of the
proof for a fully answered but unresolved receipt. The marker is a valid 64-hex entry (a pre-marker
decoder accepts it and reads the day as owed) that never equals `coverage_digest`, so the day stays
in `source_owed`; `ObjectStoreLaneReader.census` also reports it in `LaneCensus.source_unresolved`
from the same yearly read. Each turn that settles such a day reports it `unwritten` with reason
`source_unresolved` (detail: the dropped rows by reason) and counts census-known ones as
`days_source_unresolved`. Re-asking cannot settle it, so `plan_gap_fill(last=...)` asks
`LaneCensus.unresolved_days()` only in a turn that owes no other reachable hole. Until then they are
`GapFillPlan.held`: never fanned out, so they cost no request and no budget, and each is reported
`source_unresolved` (as are the ones past the day cap once they are asked), never `deferred_budget`.
Ordering them last was not enough: `_fan_out` re-sorts by day and `select_affordable_days` spends the
cap greedily in that order, so with fewer than 366 real holes the 1990 days took about 96 of the 112
calls and scattered holes were deferred every fire (follow-up review MEDIUM). The cost: a hole that
never fills (a day upstream keeps failing) postpones every unresolved re-ask, since `_revise` asks
only `LaneCensus.lane_data_days()`, which excludes source-owed days. `decide_rewrite` writes an
unresolved full answer once (`unresolved_recorded`, which records the marker, crash-safe because the
census decides), then only on a digest change; a resolved answer later earns the proof
(`source_completed`). A series-choice rule needs no migration: once the strategy resolves the day,
the next re-ask proves it. Proofs issued before this change (the 310 dates) are NOT retracted by
code; removing those entries from `lane-source-completeness/v1/water-gauges-daily/{1990,1991}.json`
is an owner-approved production step.

**Unresolved days keep a turn `incomplete`, on purpose** (follow-up review LOW 2). Each
`source_unresolved` entry is `behind_edge`, so it counts in `days_unwritten`, and every water
gap-fill turn (and any forward turn whose window holds one) reports `outcome=incomplete`; the
executor's `TurnReport.incomplete` stays true and `consecutive_incomplete_buckets` never resets
while the days exist. That is the intended signal: such a day serves data with a gauge's rows
dropped, and which series to keep is an open owner decision. The cost is that the standing
`lane_incomplete` incident no longer singles out a new real hole; read `unwritten_by_reason` (a
`deferred_budget` or `upstream_unavailable` count beside `source_unresolved`). Revisit when the
owner picks a series rule: if days stay unresolved by design after that, move them to a fact outside
`days_unwritten`.

Forward turns discover ladder debt independently of the decision to re-ask their full recent
window. Repair candidates are censused again after publication: an unchanged digest cannot hide
a missing coarse rung, an already rewritten ladder is not derived twice, and a source-incomplete
base is never treated as sufficient input for ladder-only repair.

## Checkpoints

Only a unit answer whose every requested parameter carried values is kept (per-parameter
eligibility); a replay goes back through the strategy's own `fetch` with the ORIGINAL retrieval
instant, and a held answer the parser now refuses is a miss, never a failure. Keys bind the lane,
provider, support digest, the unit's days and its credential-free URL
(`RUNNER_CHECKPOINT_PROVIDER_PREFIX` keeps them apart from the legacy writers'). Compare mode reads
checkpoints and never writes one. **A checkpoint only finishes an unserved day** (`turn.py::
_Turn._restorable`): a unit is restored only when every owed day it covers is `missing` or
`incomplete` for every stream. A served or absent day is re-asked, so a `write_and_recheck` lane's
rolling reconcile sees upstream revisions instead of replaying its own 7-day-old answer (review M3).

## Turn receipts

`lane-turn-receipts/v1/<stream>/<day>.json` in the availability storage, outside every `layer=`
prefix so no census or serving walk ever reads one. A receipt holds the source digest (S11), the
unit counts, a transform's input digests, and the input streams a rebuild pruned — the audit
outlives the pruned partition (§4.6). A transform lane also keeps one lane-level receipt per day
under `_lane.<lane id>` (`receipts.py::transform_receipt_stream`; no stream slug starts with `_`),
written after every answered output (review M5).

The key prefix is v1; the document's `schema_version` is `lane-turn-receipt-v2` (post-push review
L6): it adds `publication_state` (696f1ae5) and `source_resolved` (H1). `DayReceipt.from_payload`
reads v1 and v2 (a v1 receipt without `publication_state` is complete; one written by
696f1ae5..21ec86ce keeps its state).

**Rollback note.** Do not roll back below 696f1ae5: every reader before it accepts only v1 and has
no `publication_state`, so it would read a pending receipt as complete and could let a partial
replacement through. v2 makes every pre-v2 reader (including 696f1ae5..21ec86ce) refuse a receipt
written by this code with `TurnReceiptError`: a turn that meets one fails closed (exit 70) instead
of misreading it, so rolling back past this commit stops those turns until it is rolled forward
again. The same holds in a mixed-image window: a service still on an older commit after a failed
redeploy exits 70 on the first v2 receipt it reads, until it runs this code.

## Digests

`digests.py::table_digest` hashes the Arrow IPC stream of `combine_chunks()`, so chunking never
changes the answer. `reader.canonical_digest` conforms a table to its stream schema first (column
order, types, grain sort), so a built table and the published copy of the same rows agree. That is
what S11's written-day digest, compare mode and CA17 compare.

## Writer

`writer.py::decide_rewrite` is the one statement of S11: write a missing/incomplete day; retract a
disproven absence; rewrite a settled data day when its source digest changed; rewrite a partial
`write_and_recheck` day only on strictly more units; a data day with no runner receipt (written
before cut-over) is rewritten once, which is how it gains one; a governed absence never overwrites
data (fail-closed); and a `write_and_recheck` answer with FEWER units than the written day is
`fewer_units`, never written, so a tile that failed this turn cannot erase the gauges it served
last turn (the counterpart of "strictly more units"). `ObjectStoreLaneWriter` writes every day through
`pipeline/parquet/gap_fill.py::fill_one_lane_day` (lane-day lock, base rung, derived tiers,
completion marker, availability generation and pointer), then the turn receipt. It cannot be
constructed with a compare permit. **A lane-day another run holds is `contended`, never exit 70**
(review M4): `fill_one_lane_day`'s `contended` outcome, a refused prune lock and a refused repair
lock raise `LaneDayContendedError`; the turn records that stream-day `contended` and writes the
rest. **A coverage refusal under the lock is `refused_partial`** (post-push review L5): `write_day`
re-reads the receipt after taking the lock, and when another run published coverage this answer
lacks, it raises `LaneDayCoverageRefusedError` (not a `LaneDayContendedError` subclass, so the turn
cannot report it `contended`); the turn records `refused_partial` with the refusal's S11 word and
writes the rest. Any other non-publishing outcome (`blocked`, `raised`) is still `LaneWriteError`,
exit 70. **A static lookup's registration keeps its legacy watermark** (`LaneRegistration` refuses
a static lane without one), so `fill_one_lane_day` brackets the write with that watermark read, as
the legacy path does. Prune retracts the coarse rungs first and the base last, under the lane-day
lock.

**Free recheck (S11 for ERA5T):** when a forward fan-out's units answer more days than were owed (a
14-day request answers 14 days), every published window day all of whose units answered is settled
too, at no extra cost, and rewritten only on a digest change. Such a day is never reported unwritten.

## The turn

`turn.py::run_turn` validates first (exit 78 before any read or send): mode vs kind; a writing turn
needs `executor = "config"`, `enabled = true` and a writer; `--compare` must NOT have a writer and
runs forward or transform only (it is allowed on a `legacy` lane — that is its purpose before G6);
`--republish-current` is a static_lookup forward option; a static lookup must implement
`probe_edge` (it reads its watermark there). Then:

1. **CA20:** a forward writing turn retries its streams' owed `availability/pending/` claims first
   (the legacy `_retry_owed_availability` contract: bounded per stream, a fault logged and never a
   block on the writes).
2. Choose the days (`windows.py`), spend the probe, select what the cap affords, restore
   checkpoints, fetch (80 % of `turn_timeout_seconds`; the rest is kept for writing), `settle`,
   `rows`, the S11 rules, write or compare.
3. A strategy fault in `settle`/`rows`/`derive` is that day's `strategy_error`, never the turn's;
   a fault in `plan_requests` is a code fault (exit 70). `Absent` without a proof is a
   `strategy_error`; a partial `Written` on a `refuse` lane is `refused_partial`.
4. **A unit that stays down.** On a `refuse` lane, and for any day none of whose units answered, the
   day is held and reported with its worst unit's reason. On a `write_and_recheck` lane an owed day
   with SOME units answered is settled from those (spec §7a: "a tile that stays down is reported
   as unwritten for its gauges"); the day also gets one `unwritten` entry naming the unanswered
   units (`_short_day_detail`). The strategy's `Written` is then partial (present < planned), the
   receipt keeps the counts, the next turn's fuller answer is `more_units`, and a later shorter one
   is `fewer_units`. A free recheck or a revised day is only ever settled whole.

**Static lookup:** the probe returns the source watermark as the one valued day; the lane owes a
snapshot dated there when the served snapshot is older, and nothing when it is current. **CA17
`--republish-current`:** refetch the served snapshot's day, and only when every stream's canonical
digest equals the served one, rewrite it through the normal writer with availability (receipt
outcome `republished`); a mismatch is refused (exit 78, `republish.refused = digest_mismatch`) and
nothing is written. Each production use needs an owner go (CQ-8). Spec §4.3 step 7 says static
lookups get no availability extension (other-lanes F6); CA17 (§4.8.9, later) requires "a new
availability generation", so the runner extends availability for static writes too.

**Transform:** reads the input lanes' published partitions and never imports lane code. Input
digests come from the inputs' turn receipts, else from the published table. A day whose inputs'
digests equal the transform receipt is clean; a dirty day whose rebuilt output is digest-equal only
refreshes its receipt. The transform receipt is the lane-level one when present, else the one every
output holds: a derivation that answers a subset of its outputs (an output whose inputs have not
published) is therefore clean until an input changes, and a turn that died between two outputs
leaves the day dirty because the lane-level receipt is written last. With `[pruning]` on (S12, a gate flip), superseded input stream-days of a
`write_and_recheck` input lane are retracted after the output is written; the pruned digest stays
in the receipt, so the day is not dirtied again by its own prune, and a prune that failed is retried
next turn.

## The S5 report

The last stdout line, event `plantgeo_lane_turn_report`, **no `level`** (the executor's parser
takes the last such line, else the last JSON object with no `level`). `TurnReportBuilder.to_payload`
refuses to serialise until the turn states its `unwritten` list (`close_unwritten`) or declares it
unknown (`mark_unwritten_unknown`, `unwritten_known = false`); `__main__` writes it from `finally`
on every path. `days_unwritten` counts only days behind the provider edge (S5); `outcome` is
`completed` / `incomplete` on exit 0. Facts: `requests`, `weighted_calls` (logical), `fetch_attempts`,
the `probe` block and `probe_status`, `http_*`, `bytes_in`, `backoff_seconds` and
`weighted_calls_metered` (deltas of `foundation/observability/usage.py`'s per-host meter),
`retry_backoff_seconds` (this ladder), `rows_written` / `rows_built`, `partitions_written`,
`bytes_written`, `elapsed_seconds`, `phase_seconds_{census,probe,fetch,settle,write}`,
`checkpoint_restores`, `days_revised`, `revision_first`/`revision_last`, `rows_dropped_by_reason`,
`days_source_unresolved`, `throttled_until`,
`log_lines_*`, and the writer's `availability_*` tally (the executor reads it
as publication debt). `TurnLog` keeps ≤ 200 debug/info/warn lines each per turn (the rest counted
in `log_lines_suppressed`); errors are never dropped. Nothing here calls `print`.

## Exit codes

S4: `0` completed (unwritten days are in the report), `75` every unit the turn sent ended
`upstream_unavailable` and none was answered or restored, `70` anything unexpected, `78` a
configuration fault (`exits.py::exit_code_for`, argument errors included). A probe that cannot
answer never fails the run (O-R3-1). `deferred_quota` is a reported day, not an exit.

## The command

`__main__.main(argv, *, bind=None)`: `configure_logging("service")` (info to stdout, errors to
stderr), load `lanes/` for the active region through `foundation/lane_config` (never re-parsed
here), resolve the strategy, bind the ports, run the turn. `bind` is the seam the command tests use;
production binds `binding.py::production_ports`.

## Production binding

`binding.py::production_ports`: `ObjectStore` and `BotoAvailabilityStorage` from settings, turn
receipts and checkpoints in the availability storage, one metered `ingest/http.py::upstream_client`,
and — unless comparing — one loader session (`LOCAL_SOURCE_LOADER_DATABASE_URL`) for the writer's
advisory locks. `ConfigProviderClient` delegates to `ingest/provider_client.py` (plan 1C's seam):
`provider_endpoint_request` resolves the declared endpoint; one with a `customer_host` REQUIRES its
key (`api_key_env`), sent only inside `KeyedRequestUrl`, so a keyed URL never reaches a log line or
an exception string; an empty key is a named configuration error (FR-2), never a silent fall-back to
the free host. `send_provider_request` makes the one attempt, and `binding.py` maps its status onto
the runner's typed errors. Where a key travels is the provider file's `api_key_transport`
(`query`, the default, or `header`): Open-Meteo's customer hosts take `apikey` in the query
(`provider_client.PROVIDER_API_KEY_PARAMETERS`, unchanged), USGS's optional key goes in `X-Api-Key`
(`api_key_header`), so its send URL is the credential-free `request_url` and a keyed send never
follows a redirect (`ingest/AGENTS.md`, provider_client.py). `tests/runner/test_binding.py`
drives the real client through `main` over `httpx.MockTransport`: the key reaches the customer
host and nothing else (report, stderr, `request_url`, checkpoints), and an empty or rejected key
exits 78.
