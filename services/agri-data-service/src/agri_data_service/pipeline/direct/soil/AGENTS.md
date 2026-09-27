# Provider quota handling

A provider quota response and an exhausted per-turn request budget are typed provider deferrals.
The adapter records them in `unsettled_refusal`, so the forward driver reports `source_unsettled`
without consuming the generic publication retry series. Malformed responses, transport failures,
and publication failures keep their existing bounded error behavior.

Once a quota refusal is received, the shared turn cache stops queued requests before they start.
Already-running requests may finish and their successful responses are retained immediately.
Accounting charges started point requests (climate) or started chunk captures (soil), not queued
work skipped by the quota circuit or deadline. Soil capture retains the existing bounded internal
transport/minute-quota retry policy, so its counter measures captures rather than HTTP attempts.

Production forward commands persist verified, entirely non-null chunks across executor turns for
up to seven days through `pipeline/parquet/source_checkpoint.py`; see that directory's AGENTS.md
"Source response checkpoints" for canonical-byte receipts, full support/chunk binding, CAS, and
lifecycle bounds. Chunks with any null value remain in-memory only, including coastal/ocean chunks
with a stable null mask. Resumption precedes budget checks and never bypasses provider quota.
Cross-turn cooldowns and expired-object cleanup remain unresolved operational work.

A 429 on the settlement probe (below) sets the SAME `SoilSourceCache.deferred_refusal` circuit a
chunk 429 does -- `probe_soil_edge` catches `SoilProviderDeferredError` and records it exactly as
`fill_chunk_day_cache`'s `one()` does, so a throttled probe stops every chunk fan-out for the rest of
the turn without a second code path.

# Candidate publication edge and unsettled frontier

**G0 legacy soil cap.** Every legacy soil turn spends at most **33 logical requests**
(`SoilForwardConfig.request_budget` = `chunks_per_day * max_days + SOIL_EDGE_PROBE_REQUESTS` =
`32 * 1 + 1`), because `SOIL_MAX_DAYS == 1`: an operator or a repair item can never widen a turn past
one day per product, whatever `--max-days` asks for. Three counters, all run-level:

| counter | what it charges on | ceiling |
|---|---|---|
| `requests_spent` / `weighted_calls` | one per logical request (chunk or probe); `weighted_calls` is the Open-Meteo per-location quota cost (`open_meteo_request_weight`) | 33 requests; 1,602 weighted hard, 1,570 on a clean fan-out (`SoilSourceCache.restore` can resume a null-free 18-cell chunk mid-turn, letting all 32 remaining requests be 50-cell ones) |
| `fetch_attempts` | one per `fetch_lane_capture` call, retries included -- a scaffold count, not a wire count | 33 x `MAX_FETCH_ATTEMPTS` = 132 |
| `http_requests` | one per real httpx send, counted by an ASYNC `request` event hook (httpx 0.28's `AsyncClient` awaits every hook; a sync one raises `TypeError` on every send) attached to the client `probe_soil_edge`/`fill_chunk_day_cache` open -- `ingest/http.py::fetch_bounded`'s own transport retries and `upstream_client`'s redirects are otherwise invisible to `fetch_attempts` | 33 x `MAX_FETCH_ATTEMPTS` x `TRANSPORT_RETRY_ATTEMPTS` x (1 + `MAX_REDIRECTS`) = 1,584 |

**Zero headroom.** The 33-request budget has none to spare: one failed chunk still costs its slot in
`requests_spent`, and the NEXT turn only restores checkpointed chunks that are entirely non-null
(`source.py::_checkpoint_eligible`) -- a thin chunk (any null cell) is never checkpointed, so it is
re-asked from scratch next turn too, exactly like a chunk that failed outright.

**The probe.** Once per run, before any product's walk, `probe_soil_edge` asks the archive whether
it has moved past the candidate edge over the SAME fourteen-day window `SOIL_ABSENCE_RECHECK_DAYS`
already re-examines governed absences in (`SOIL_EDGE_PROBE_WINDOW_DAYS`). It requests **two** support
cells straddling the extent centre (`probe_cells`), never one: a one-location Open-Meteo answer is a
bare JSON object, and `canonical_location_document` refuses any non-array body. The centre/offset
arithmetic stays in `Decimal` (the support's own type) until the final `float(...)` handed to
`support.resolve(longitude, latitude)` -- passing a `Decimal` there raises `decimal.InvalidOperation`,
not `SoilSourceError`, so the run would exit 1 on every probe. The probe never raises for an upstream
or body fault -- only `probe_cells` finding the support has changed shape does (exit 1, a code error).
Every other fault folds into one of three statuses, its `detail` carrying the exception class name
and message (e.g. `"ValueError: ..."`):

| status | meaning | walk effect |
|---|---|---|
| `ok` | the archive answered a scored window; `forward.py` further derives `invalid` (a `data` census day the probe called null -- the probe, not the day, is wrong) or `blind` (no valued day and no `data` day at all) from it | a window day not among `valued_days` is gated; `invalid` gates every day NEWER than the product's newest census `data` day; `blind` gates every window day |
| `unavailable` | a transport failure after `MAX_FETCH_ATTEMPTS`, a malformed body, a wrong day axis, an oversized/deeply-nested body (`ArithmeticError`, `RecursionError`), or a spent time budget | every window day is gated; an owed day OLDER than the window is still walked if time remains |
| `deferred` | a 429 (see "Provider quota handling") | every window day is gated; an older owed day is attempted but the quota circuit refuses it before a request, reporting `source_unsettled` |

A **gated** day costs no request and no `--max-days` slot: it is built with `_stopped_day` and
carries one of `newer_than_probed_edge` (an owed day), `absence_unchanged` (an absence recheck),
`probe_blind`, `probe_unavailable`, `probe_deferred` or `probe_invalid`. The slot and deadline are
checked BEFORE the gate on every loop turn, so a day the walk never reached is never reported as
gated. A day whose BASE rung already reads `data` is never gated, whatever the probe says -- it is
settled by definition, owed only for a derived rung, and goes straight to `cache.restore`.
`_product_outcome` ignores gated entries: a product whose only published entries are gated reports
`source_unsettled`, UNLESS every one of them is an absence-recheck gate (`idempotent_noop`), in which
case nothing was materially decided and the product reports `idempotent_noop` too.

**The `invalid` gate.** Before G0's fix, an `invalid` probe let the walk run fully ungated, so a
turn fanned out the newest owed day first -- at the measured 9-day archive lag that day is reliably
all-null, stalling the whole 33-request (1,570-weighted-call) budget on one guaranteed-unsettled day
every turn. The fix: under `invalid`, every day NEWER than the product's newest census `data` day is gated
(`source_unsettled`, `probe_invalid`); the walk only ever considers days at or below it, still capped
at `--max-days`. At 33 requests total, one fan-out per turn is what the budget can afford, so this
gate is not a loss -- it points that one fan-out at a day with a real chance of being settled.

**The ungated walk and its four-skip lookback.** `edge_probe=None` is the ONLY ungated shape now --
production never takes it; only the frontier unit tests (`_publish_product` called directly) do. The
walk may step backward through four all-null frontier days (`SOIL_UNSETTLED_FRONTIER_SKIPS`) without
charging them against `--max-days`, covering the measured 2026-08-11 shape: a nine-day actual edge
beneath the documented five-day candidate. This lookback spends from the SAME 33-request total; it is
not a second allowance. The fifth unsettled day spends the ordinary day slot and ends the walk, and a
thin fanned-out day (any value count other than `ERA5_LAND_VALUE_CELL_COUNT`) still raises and exits
1 -- G0 adds no new exit-1 path there beyond `probe_cells`.

Reports expose `source_unsettled_days`, `unsettled_frontier_days`, `probe_status`, `probe_gated_days`
and the run-level `weighted_calls`/`fetch_attempts`/`http_requests`/`probe` block, and the product
outcome names what materially happened (`published`, the stopping budget, `source_unsettled` or
`idempotent_noop`) rather than treating every non-empty backlog as a publication.
