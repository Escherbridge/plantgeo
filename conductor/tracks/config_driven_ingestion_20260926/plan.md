---
type: Implementation Plan
title: Config-driven ingestion — legacy soil cap (G0), framework, hardening loops, early water gauges, swarm cut-over, production QA, deprecation
tags: [config_driven_ingestion_20260926]
resource: ./spec.md
---

# Implementation Plan: Config-driven ingestion

## Overview

This plan follows the owner's migration shape (spec D10). It overrides push-small for this effort
on purpose. It was revised 2026-09-26 for review loop 1 and the round-2 owner answers (spec §3.3,
§14), again for review loop 2 (§3.4, §15), and a third time for review loop 3 and the round-3 owner
decisions (§3.5, §16).

    G0 legacy soil cap (own gate) → owner sets the Open-Meteo key
      → framework (dark) → two review/fix loops + contract re-freeze → water gauges early (own gate)
      → extraction (after G4) + swarm → batch cut-over → per-lane QA + validation (+ climate rebind)
      → deprecation

| phase | ships | owner gate | review at the boundary |
|---|---|---|---|
| 0 | review loops 1–3 folded in; read-only probes P1–P4; contract **draft**; **G0 legacy soil cap**; P5 usage baseline | **G0** (then the owner sets `OPEN_METEO_API_KEY`) | loops 1–3 done (all CHANGES-REQUIRED; spec §14–§16); **targeted re-check** of P1–P9 + G0 (O-R3-2); G0's own `oh-my-claudecode:critic` |
| 1 | framework, dark; legacy bridge fixes; manifest lattice; image/receipt plumbing; registration mirror | — | `/code-review high` (runner, executor, SQL); `/security-review` (keys, `/layers`, operator command) |
| 2 | two review/fix loops; contract **re-frozen from landed code** | **G1** | R1 `oh-my-claudecode:code-reviewer` (opus); R2 `oh-my-claudecode:critic` (opus) |
| 3 | water gauges early lane on USGS daily values | **G2** (USGS key if needed), **G3**, **G4** | one adversarial reviewer for the lane |
| 4 | extraction (sequential, after G4's push); contract registration; swarm of 13 lanes, dark | **G5** | one reviewer for `p4-extract`; one adversarial reviewer per lane |
| 5 | compare runs + batch cut-over; climate gap-fill flips | **G6**, **G7** | `oh-my-claudecode:critic` on the cut-over diff and rollback |
| 6 | per-lane QA round + validation round; climate rebind | **G8** | QA `oh-my-claudecode:verifier`; validation by a different reviewer |
| 7 | deletion of the legacy path | **G9** | `/code-review high` + `/simplify` |

### Working rules (binding on every phase)

1. **Authors run no tests, lint, type-check or build.** Each author reports its predicted failures;
   the coordinator records them as rows in the phase evidence table.
2. **One integrated sweep per phase, run by a separate monitor lane.** From `services/agri-data-service`:
   `uv sync --locked --all-extras`, then `python scripts/check.py --write-receipt`.
   - Judge the per-gate rows, never the exit code: `check.py` exits 0 on FAIL rows.
   - `AGRI_TEST_DATABASE_URL` must be genuinely unset.
   - When `src/**` is touched, also run `npm run type-check`, `npm run lint`, `npm test`,
     `npm run build` and `npm run check:data-boundary`.
   - Ruff runs only here.
   - **Every lane-TOML change, rollback flips included, needs this sweep and a receipt refresh
     before it can build** (spec S13).
3. **Every phase boundary gets an adversarial review** from a reviewer that did not write the code,
   prompted to refute. The reviewer loads `conductor-okf:code-styleguides` and checks
   `conductor/product.md`. The **one-line verdict** goes in `metadata.json` → `reviews`.
   Fixes are batched, then the single sweep runs.
4. **Never run PlantGeo locally.** pytest and `npm run build` are fine; read-only `curl` probes
   are fine.
5. **Every production mutation waits for an explicit owner go** (gate list below). **The owner sets
   secrets.**
6. **Shared checkout.**
   - Check `ListAgents` and `git status` first; `services/strategy-knowledge/` was untracked at
     planning time.
   - Commit with a pathspec, flags before `--`.
   - Never `git add conductor/`.
   - Use `cp` + `rm`, never `git mv`.
   - Use Write/Edit for files containing backticks.
   - Only the monitor writes `QUALITY_RECEIPT.json`.
7. **One evidence table per phase: `evidence/phase<N>.md`.** Its rows are predictions, sweep gate
   rows, review verdicts, probe results and per-lane QA/validation. There are no per-lane evidence
   files. The only other file is `evidence/contract-freeze.md`.
8. **The service set** a push redeploys is listed from Railway at each gate
   (`railway deployment list`). At 2026-09-19 it was plantgeo-main, plantgeo-parquet-api,
   plantgeo-job-executor, plantgeo-martin and plantgeo-ml.
9. **Quiescent pushes, one checkout, no worktrees (spec S17, O-R3-3).** A commit touching
   `services/agri-data-service/**` is made only when `git status --porcelain -- services/agri-data-service`
   shows exactly that commit's files. Every gated push happens at such a point. There is one
   `QUALITY_RECEIPT.json`, written by the monitor on `main`. During a multi-slice authoring window,
   a lane rollback uses the ledger brake; its TOML revert waits for the next quiescent point.

---

## Phase 0: Reviews, probes, contract draft, G0

Goal: turn assumptions into measurements, draft the contract, and cap legacy soil before the paid
key exists. No owner questions remain (spec §10).

- [x] Task: Critic review loop 1 (CHANGES-REQUIRED), disposed in spec §14 and
      `metadata.json` → `reviews.phase0_loop1`.
- [x] Task: Critic review loop 2 (CHANGES-REQUIRED, 4 new HIGH), disposed in spec §15 and
      `reviews.phase0_loop2`.
- [x] Task: Critic review loop 3 (CHANGES-REQUIRED, "G0 NOT READY"), disposed in spec §16 and
      `reviews.phase0_loop3`.
- [ ] Task: **Targeted re-check (O-R3-2)** — a fresh `oh-my-claudecode:critic` verifies only the
      P1–P9 rows of spec §16 and the G0 brief below; not a full loop 4. Verdict in
      `reviews.phase0_recheck`.
- [ ] Task: **P1, Open-Meteo ERA5.** For all 1,568 cells, request the S10 NE-corner node with
      `cell_selection=nearest` for one day and the 7 dailies. Record:
  - that the echoed coordinates equal the requested nodes (a bijection);
  - that every variable exists;
  - the measured `expected_value_units`.
- [ ] Task: **P2, Open-Meteo forecast and historical-forecast endpoints.** Record the IFS model key
      and resolution, the `past_days` reach, the historical-forecast reach back to 2026-07-01, the
      8 dailies, and that the customer hosts exist (A11).
- [ ] Task: **P3, NASA POWER.** Record the UTC shortwave edge with `time-standard=UTC`, whether the
      regional endpoint covers the lattice envelope, and the native-cell de-duplication count (≈98).
- [ ] Task: **P4, USGS Water Data API.** Record:
  - the daily-values collection for `00060`/`00003` over the 8 tiles;
  - the **served `time` format** and whether the day is site-local;
  - the prefix-rule check over 10 gauges × 5 days;
  - key requirement and rate limits;
  - per-gauge DV start dates and whether history reaches **1990-09-30** (A14);
  - 503 samples.
- [ ] Task: Measure weather-observations' weighted calls per turn from its code (W₁ and W in spec
      §6.3). If W₁ + W > 250,000/month, record the G7 split in `evidence/phase0.md`.
- [ ] Task: Write the **contract draft** in `evidence/contract-freeze.md` (status: DRAFT): TOML
      fields (incl. `[budget]` per-mode caps and `[[streams]]`), provider schema, the Protocol text
      in `pipeline/runner/contract.py`, S5 report, S4 exits, S14 convention, lane ids, stream slugs,
      the `precedence_source` column, and the analysis-lattice entry.
- [ ] Task: **G0** — hand the brief below to one sonnet executor verbatim, then run §9's steps.
- [ ] Verification: `evidence/phase0.md` has P1–P5, W, the re-check verdict, the G0 verdict, the
      hole-count row, the G0 and key observation rows, and the draft status. [checkpoint marker]

### G0 task brief (slice `g0-soil-cap`; spec FR-24, §4.4; hand to a sonnet executor verbatim)

**§0 Rules.** You run no tests, lint, type-check or build. Edit only the files in §1. When you
finish, list the tests you expect to fail and why; the coordinator records them. Paths below are
relative to `services/agri-data-service/`; `A/` = `src/agri_data_service/`.

**Goal.** Every legacy soil process spends at most **33 logical requests** (≤ 1,602 weighted calls
hard, 1,570 on a clean fan-out — review R4; ≤ 132 fetch attempts (≤ 1,584 HTTP requests incl. transport retries and redirect hops)); a day the edge probe shows
unsettled is never fanned out; a probe that cannot
answer never fails the run; the report states requests, weighted calls and HTTP attempts. Nothing
outside `A/pipeline/direct/soil/` changes behaviour.

**§1 Files.**

| file | what you do |
|---|---|
| `A/pipeline/direct/soil/forward.py` | constants §2, probe call §4, walk §5, report §5 |
| `A/pipeline/direct/soil/source.py` | constants §2, accounting §3, `probe_cells`, `SoilEdgeProbe`, `probe_soil_edge` §4 |
| `A/pipeline/direct/soil/AGENTS.md` | §8 |
| `A/pipeline/direct/AGENTS.md` | §8 — only the three soil passages named there |
| `tests/direct/soil/test_forward_command.py` | §7 edits |
| `tests/direct/soil/conftest.py` | add `probe_body`, `probe_location` helpers only |
| `tests/direct/soil/test_edge_probe.py` | new, §6 harness + §7 tests |

Read-only: `A/pipeline/direct/soil/{products,support,adapter}.py`, `A/execution/open_meteo_lane.py`,
`A/ingest/open_meteo.py`, `A/ingest/http.py`, `A/execution/gap_repair_contract.py`,
`A/execution/lane_specs.py`, `tests/direct/soil/test_adapter.py` (`SessionDouble`),
`tests/parquet/test_objectstore_writer.py` (`RecordingBackend`),
`tests/parquet/test_availability_index.py` (`MemoryAvailabilityStorage`),
`tests/direct/climate/test_forward_command.py` (the settings / `from_settings` patch precedent, §6).
Do **not** touch `tests/direct/soil/test_lane_registrations.py` or anything under `A/execution/`:
`f1-executor` owns the soil schedule pins at G1.

**§2 Constants and coordinates.**

- `forward.py`:
  - `SOIL_MAX_DAYS: Final = 1` (was 5). `_validate_config` already rejects `--max-days` outside
    `1..SOIL_MAX_DAYS`; `gap_repair_contract.py` reads it as soil's repair `max_days_cap`, so repair
    runs inherit the cap without an edit there.
  - New `SOIL_EDGE_PROBE_REQUESTS: Final = 1`.
  - New `SOIL_EDGE_PROBE_WINDOW_DAYS: Final = SOIL_ABSENCE_RECHECK_DAYS` (14).
  - `SoilForwardConfig.request_budget` returns
    `chunks_per_day * self.max_days + SOIL_EDGE_PROBE_REQUESTS` (= 32 × 1 + 1 = **33**). Keep
    `SOIL_UNSETTLED_FRONTIER_SKIPS` and `_steps_past_unsettled_frontier` exactly as they are; they
    still govern the ungated walk.
  - Rewrite the `SoilForwardConfig.request_budget` docstring (review R7): it still describes the old
    "one additional day in the bounded lookback" (`+ SOIL_UNSETTLED_FRONTIER_SKIPS`) budget. It now
    funds `max_days` fan-outs plus one edge probe; the ungated lookback spends from that same 33.
- `source.py`:
  - New `SOIL_EDGE_PROBE_CELL_COUNT: Final = 2`, commented: two, not one, because a one-location
    Open-Meteo answer is a bare JSON object and `execution/open_meteo_lane.py::canonical_location_document`
    raises `ValueError` on any non-array body.
  - New `probe_cells(support: Era5LandSupport) -> tuple[Era5LandSupportCell, Era5LandSupportCell]`:
    centre longitude = `(ERA5_LAND_SUPPORT_WEST + ERA5_LAND_SUPPORT_EAST) / 2` = −118.0; centre
    latitude = `(ERA5_LAND_SUPPORT_SOUTH + ERA5_LAND_SUPPORT_NORTH) / 2` = 45.5; probe row latitude
    = centre latitude − `ERA5_LAND_SUPPORT_STEP_DEGREES / 2` = 45.375; probe longitudes = centre
    longitude ∓ step/2. **The probe cells as (lon, lat) are (−118.125, 45.375) and (−117.875, 45.375).**
    Resolve each with **`support.resolve(longitude, latitude)` — longitude FIRST**. If either
    resolves to `None`, raise `SoilSourceError` (a changed support is a code error; the run exits 1).
  - **Decimal trap (review R3):** the `ERA5_LAND_SUPPORT_*` constants are `Decimal`
    (`support.py:53-58`) and `Era5LandSupport.resolve` runs `Decimal(repr(value))` on its arguments,
    so passing a `Decimal` raises `decimal.InvalidOperation` — not a `SoilSourceError`, so the run
    would exit 1 every time. Do the centre/offset arithmetic in `Decimal`, then pass
    **`float(...)`** of each coordinate to `resolve`.
  - Fix the stale `ERA5_LAND_CHUNK_CELL_COUNT` comment (`source.py:81-83`, review R7): quota is not
    "weighted by locations x variables x timesteps"; per the captured pricing page one request costs
    1.0 per location at ≤ 10 variables and ≤ 14 days (see §3 `open_meteo_request_weight`).
  - **Coordinate-order trap:** `ingest/open_meteo.py::archive_daily_request` takes points
    **latitude FIRST**, exactly as `_fetch_chunk_day` builds them: `[(cell.cell_latitude, cell.cell_longitude), …]`.

**§3 Accounting (requests, weighted calls, HTTP attempts).**

- New `source.py::open_meteo_request_weight(locations: int, days: int, variables: int) -> float` =
  `locations * max(1.0, days / 14) * max(1.0, variables / 10)` (one model). A chunk request is
  `len(chunk.cells)` (50, or 18 for the last chunk); the probe is 2.
- `SoilSourceCache` gains `weighted_calls: float = 0.0` and `http_attempts: int = 0`.
- Wherever `cache.requests_spent += 1` runs for a chunk (in `fill_chunk_day_cache`'s `one()`), also
  add `open_meteo_request_weight(len(chunk.cells), 1, len(SOIL_SOURCE_PARAMETERS))` to
  `cache.weighted_calls`. The probe adds 1 to `requests_spent` and 2 to `weighted_calls`.
- HTTP attempts: thread the cache into `_fetch_chunk_day` and `_capture_chunk` (keyword `cache`),
  and pass `fetch_lane_capture` a `fetch_text` closure built **at call time** that does
  `cache.http_attempts += 1` and then awaits the **module-level** `fetch_archive_daily`, so a test
  that monkeypatches `source.fetch_archive_daily` sees every attempt. The probe uses the same
  closure. `fetch_lane_capture` retries up to `MAX_FETCH_ATTEMPTS` (4) times, so the ceiling is
  33 × 4 = **132 HTTP attempts**.
- Weighted ceiling (review R4): **1,602 hard, 1,570 on a clean fan-out.** `SoilSourceCache.restore`
  restores only null-free chunks (`source.py:559-561`); if the 18-cell chunk is restored from a
  checkpoint and an in-run re-ask follows, all 32 remaining requests can be 50-cell ones:
  2 + 32 × 50 = 1,602. Tests assert `weighted_calls <= 1602` except the clean-fan-out equality test.

**§4 The probe and its statuses.**

- `@dataclass(frozen=True, slots=True) class SoilEdgeProbe` in `source.py`: `status`
  (`"ok" | "unavailable" | "deferred"` from the fetch; `forward.py` derives `"invalid"` and
  `"blind"` per product), `window_first: date`, `window_last: date`,
  `valued_days: frozenset[date]`, `detail: str | None`.
- `async def probe_soil_edge(*, support, window_first, window_last, cache, deadline, now=None) -> SoilEdgeProbe`:
  1. If `cache.deferred_refusal` is set, return `deferred` without a request.
  1b. **Deadline check (review R1):** call `require_time_remaining(deadline, day=window_last)`
     *before* charging the request; its `SoilTimeBudgetExhaustedError` → `unavailable` (no request
     charged). Without this, a spent deadline plus a good body would return `ok`, because
     `fetch_lane_capture` consults the deadline only inside a backoff sleep.
  2. Charge the request (§3), then send ONE `archive_daily_request([(lat, lon) of both probe cells],
     SOIL_SOURCE_PARAMETERS, window_first, window_last, model=OPEN_METEO_ERA5_LAND_MODEL)` through
     `fetch_lane_capture(OPEN_METEO_ARCHIVE_LANE, "edge-probe", OpenMeteoProductRequest(...), client=…,
     fetch_text=<counting closure>, error_factory=<same refuse mapping as _capture_chunk>,
     retrieved_at=now, sleep=deadline_bounded_sleep(deadline, day=window_last))` inside
     `upstream_client(OPEN_METEO_ARCHIVE_BOUNDS)`.
  3. Parse: `ordered_locations(OPEN_METEO_ARCHIVE_LANE, capture.canonical_payload, 2)`;
     `validated_grid_point` per cell; `_daily_block`; `daily["time"]` must be a list of exactly 14
     strings whose first ten characters equal the window days in order; per parameter
     `bounded_numeric_series(..., expected_count=14)`. A day is **valued** when every parameter is
     non-null at both cells.
  4. **It never raises for an upstream or body fault:**

     | cause | status |
     |---|---|
     | parsed | `ok` (with `valued_days`) |
     | `SoilProviderDeferredError` (429; set `cache.deferred_refusal` as a chunk would) | `deferred` |
     | `SoilSourceUnsettledError` (transport failure after 4 attempts, or a parse refusal incl. a wrong day axis) | `unavailable` |
     | `ValueError` (a non-array or malformed body from `canonical_location_document` / `ordered_locations`) | `unavailable` |
     | `SoilTimeBudgetExhaustedError` (step 1b, or a backoff that would outlast the budget) | `unavailable` |

     **Catch order (review R5):** `SoilProviderDeferredError` subclasses `SoilSourceUnsettledError`
     (`source.py:105`), so its `except` clause must come **first**. Caught the other way round, a 429
     becomes `unavailable`, the quota circuit is never set, and the older-day walk fires chunk
     requests into the wall.

     Only `probe_cells` raising `SoilSourceError` escapes.
- `forward.py::run_soil_forward` computes `ceiling = settled_through(products[0], today=today)`,
  asserts `SOIL_DISTINCT_PUBLICATION_CLOCKS == 1`, and calls `probe_soil_edge` **once per run**,
  after `support`/`chunks` are loaded and before the product loop, with window
  `[ceiling − 13 days, ceiling]`.
- Per product, `forward.py` derives the effective status from the run's probe and that product's
  census (`statuses[LANE_BASE_ZOOM_TIER]` over the window):
  - `ok` stays `ok` unless a day the census holds as `data` is not valued → **`invalid`**;
  - `ok` with no valued day and no `data` day in the window → **`blind`**;
  - `unavailable` and `deferred` pass through.

| effective status | owed day in the window | absence recheck in the window | owed day older than the window | exit |
|---|---|---|---|---|
| `ok` | walked if valued; else gated: `source_unsettled`, detail `newer_than_probed_edge` | walked if valued; else gated: `idempotent_noop`, detail `absence_unchanged` | walked as today | 0 |
| `invalid` | gated (`source_unsettled`, `probe_invalid`) if newer than the product's newest census `data` day, else walked; capped at 33 (G0 critic G2) | same rule | walked | 0 |
| `blind` | gated: `source_unsettled`, `probe_blind` | gated: `idempotent_noop`, `probe_blind` | walked | 0 |
| `unavailable` | gated: `source_unsettled`, `probe_unavailable` | gated: `idempotent_noop`, `probe_unavailable` | walked if time remains | 0 |
| `deferred` | gated: `source_unsettled`, `probe_deferred` | gated: `idempotent_noop`, `probe_deferred` | attempted; the quota circuit refuses before any request → `source_unsettled`, walk stops | 0 |

The probe adds **one** exit-1 path: `probe_cells` failing (a changed support). Legacy exit-1 paths
are unchanged (review R8), among them contention timeout (`forward.py:515`), `blocked` (`:605`),
census conflict (`:776`), a ladder that fails verification (`:634`), and a thin fan-out:
`build_soil_day` refuses ≠ 1,470 values, `DirectSoilFieldAdapter.__call__` wraps it as
`DirectSoilFieldError`, and `_publish_locked_day` retries from the in-memory cache with 0 requests,
then raises.

**§5 The walk and the report.**

- `_publish_product(..., edge_probe: SoilEdgeProbe | None = None)`. **`None` means ungated** (the
  walk `invalid` uses). `run_soil_forward` always passes the run's probe.
- A gated day costs **no request and no slot**. Build it with `_stopped_day(day, outcome=…,
  detail=…)`, append it to `published` and to a new list `probe_gated_days`, and change the slot
  test to `len(published) - len(unsettled_frontier_days) - len(probe_gated_days) >= config.max_days`.
- `_product_outcome` ignores gated entries; a product whose only entries are gated reports
  `source_unsettled`.
- Report additions:
  - run level: `weighted_calls`, `fetch_attempts`, `http_requests`, and `probe: {status, window_first, window_last,
    valued_days, detail}`;
  - per product: `probe_status` and `probe_gated_days` — **including** the `forward.py::_skipped`
    report shape (review R6), so every product entry carries both keys whatever path built it.
- `request_budget` now reads 33. `WRITER_CONTRACT` needs no change: every outcome word used is
  already in `turn_outcomes`.

**§6 Test harness for run-level tests (`tests/direct/soil/test_edge_probe.py`).** Drive
`forward.run_soil_forward` across **all eight products** with
`SoilForwardConfig(product_id="all", max_days=1, time_budget_seconds=SOIL_DEFAULT_TIME_BUDGET_SECONDS,
retry_attempts=4, retry_base_seconds=5.0, retry_max_seconds=60.0, contention_timeout_seconds=300.0,
today=date(2026, 9, 20))`. The ceiling is 2026-09-15 and the window 2026-09-02..2026-09-15. Monkeypatch:

1. **Settings seam (review R2):** `settings` is a pydantic `BaseSettings` *instance*
   (`A/config.py:57`, `:562`); assigning a method on the instance raises `ValueError`. Copy the
   in-tree precedent `tests/direct/climate/test_forward_command.py:727-737` exactly:
   `monkeypatch.setattr(forward.settings.__class__, "require_local_source_loader_database_url", lambda _self: "postgresql://unused")`
   — note the `_self` parameter.
2. `forward.local_source_loader_session` → an `asynccontextmanager` yielding `SessionDouble()`.
3. `forward.ObjectStore.from_settings` → `classmethod(lambda _cls, _source=None: ObjectStore(RecordingBackend()))`
   (same precedent; a bare `lambda:` fails).
4. `forward.BotoAvailabilityStorage.from_settings` → `classmethod(lambda _cls, _source=None: None)`, and
   `forward.SourceResponseCheckpoints` → `lambda _storage: SourceResponseCheckpoints(MemoryAvailabilityStorage())`
   (availability publication off as in `frontier_turn`; checkpoints in memory).
5. `forward.load_era5_land_support` → `AsyncMock(return_value=support)` (the conftest fixture).
6. `forward._tier_status_window` → a census stub built with `test_forward_command._statuses`: every
   day is `data` at all four rungs except the days the test lists as owed (`missing`) or `absent`.
7. `forward.postgres_lane_day_lock` → `test_forward_command.always_granted`.
8. `forward._retry_owed_availability` → `AsyncMock(return_value=0)`.
9. `forward.asyncio.sleep` → `AsyncMock()`. This is the same `asyncio` module `source` uses, so
   `deadline_bounded_sleep` backoffs are instant too.
10. `monkeypatch.delenv("OPEN_METEO_API_KEY", raising=False)`, so request URLs are the keyless URLs.
11. **Counting seam:** `source.fetch_archive_daily` → `fake(client, url) -> str`.
    - It parses the query (`latitude`, `longitude`, `start_date`, `end_date`) and appends to a
      `calls` list.
    - It returns `probe_body(...)` for the 2-cell probe coordinates, or
      `chunk_body(chunk, day=…, null_cell_keys=…).decode()` for a chunk (matched by its coordinate
      list).
    - Fault injection: raise `OpenMeteoRateLimitError("daily", "Daily API request limit exceeded")`
      (→ `deferred`), raise an `ingest/http.py::UpstreamError` (retried; 4 raises → `unavailable`),
      or return `'{"latitude": 45.4}'` (bare object → `ValueError` → `unavailable`).
    - `len(calls)` is the HTTP-attempt count; with no fault injected it equals logical requests.
    - **Exact null sets (review R6):** `conftest.py::chunk_body` defaults to `null_cell_keys=()`
      (1,568 values), which `build_soil_day` refuses. Every case names its set:
      a valued day = `masked_cell_keys(support)` (1,470 values); an all-null day = every cell key;
      the thin day = the mask plus one more cell (1,469 values).
    - The probe body's `valued_days` must include **every** day the census stub holds as `data`
      inside the window; otherwise the effective status silently becomes `invalid` and the test
      exercises the wrong branch.
- Exit-code tests call `await forward.main([])` with the same patches; it returns 0 or 1.
- New conftest helpers: `probe_location(cell, *, days, valued_days, ordinal)` (a multi-day
  `location_object`) and `probe_body(cells, *, days, valued_days) -> bytes` (two locations, a 14-day
  `daily` block, run through the real `canonical_location_document`).

**§7 Tests (each line: name — what it asserts).**

New file `tests/direct/soil/test_edge_probe.py`:

- `test_the_probe_cells_are_the_two_support_cells_beside_the_extent_centre` — `probe_cells(support)` returns cells whose (lon, lat) are (−118.125, 45.375) and (−117.875, 45.375), both outside `masked_cell_keys(support)`.
- `test_the_probe_is_one_two_location_request_over_the_fourteen_day_recheck_window` — the first recorded call has `latitude=45.375,45.375`, `longitude=-118.125,-117.875`, `start_date=2026-09-02`, `end_date=2026-09-15` and every `SOIL_SOURCE_PARAMETERS` variable; the report counts it as 1 request and 2 weighted calls.
- `test_a_probe_null_window_day_costs_no_request_and_no_slot` — owed 09-15..09-12 (probe null) and 09-11 (probe valued): the calls are 1 probe + 32 chunks, all for 09-11; 09-15..09-12 are in every product's `probe_gated_days` with `newer_than_probed_edge`.
- `test_a_probe_null_day_is_not_fanned_out_on_the_next_run` — two consecutive runs with only probe-null days owed: each run makes exactly 1 call and reports `requests_spent == 1`.
- `test_an_absence_recheck_fans_out_only_when_the_probe_shows_values` — an `absent` day in the window: probe null → 1 call and `absence_unchanged`; probe valued → 33 calls.
- `test_a_probe_null_on_a_published_day_marks_the_probe_invalid_and_the_run_stays_capped` — a `data` day in the window answered null: every product reports `probe_status == "invalid"`, the walk is ungated, and `len(calls) <= 33`.
- `test_a_blind_probe_fans_out_nothing_inside_the_window` — no valued and no `data` day in the window: `probe_status == "blind"`, no chunk call for any window day, and an owed day older than the window is still fanned out.
- `test_an_unavailable_probe_gates_the_window_and_the_run_still_completes` — parametrised: four `UpstreamError`s; a bare-object body; a 13-day `time` axis; **a budget that runs out in the probe's backoff: `time_budget_seconds=10` plus one `UpstreamError`, so the 15 s backoff outlasts the budget inside `source.py::deadline_bounded_sleep` — no clock patch** (review R1). `probe.status == "unavailable"`, no chunk call for a window day, `main([])` returns 0, and in the first three cases an owed day older than the window is walked.
- `test_a_throttled_probe_is_deferred_and_the_run_exits_zero` — a daily-quota 429 on the probe: `probe.status == "deferred"`, exactly 1 call in total (the quota circuit stops every chunk), and `main([])` returns 0.
- `test_no_run_spends_more_than_thirty_three_requests` — parametrised: an all-null frontier; a valued window day; an old gap plus a recheck; 3 chunks failing once each. `requests_spent <= 33`, `weighted_calls <= 1602` (the hard bound, review R4), `http_attempts <= 132`.
- `test_a_thin_fanned_out_day_raises_after_at_most_thirty_three_requests` — probe valued, fan-out answers 1,469 values: `run_soil_forward` raises `DirectSoilFieldError`, `len(calls) == 33` (the in-run retries re-ask nothing), and `main([])` returns 1.
- `test_weighted_calls_and_http_attempts_are_reported` — a clean fan-out reports `requests_spent == 33`, `weighted_calls == 1570`, `http_attempts == 33`; one chunk failing once adds 1 to `http_attempts` only.

Edits in `tests/direct/soil/test_forward_command.py`:

- `test_the_request_budget_is_counted_in_chunks_and_not_in_cells` — `config().request_budget == CHUNKS_PER_DAY + 1` and `config(max_days=3).request_budget == CHUNKS_PER_DAY * 3 + 1`.
- `test_the_cli_defaults_to_every_product_and_one_day` — `parsed.request_budget == CHUNKS_PER_DAY + 1`.
- New `test_max_days_is_one_so_every_invocation_fans_out_at_most_one_day` — `SOIL_MAX_DAYS == 1` and `parse_args(["--max-days", "2"])` raises `SystemExit`.
- **Unchanged**, because `frontier_turn` passes no `edge_probe` (ungated, the walk `invalid` uses):
  `test_september_15_null_steps_to_september_14_and_publishes_it`,
  `test_four_null_frontier_days_reach_the_measured_nine_day_edge_and_publish`,
  `test_the_frontier_lookback_stops_after_four_skips_and_one_unsettled_slot`,
  `test_a_provider_deferral_is_not_treated_as_an_unsettled_frontier`.

There is no repair-binding test in G0 (review P4): the parser rejection above is what bounds a
repair's `--max-days`, and `f1-legacy-bridge` pins soil → `no_repair_binding` at G1.

**§8 AGENTS.md edits.**

- `A/pipeline/direct/soil/AGENTS.md`:
  - rewrite "Candidate publication edge and unsettled frontier": the 33-request budget (32 + 1 probe),
    `SOIL_MAX_DAYS = 1`, the two-cell 14-day probe and why two, the §4 status table with exits, the
    four-skip lookback now applying only to the ungated walk (`invalid`), a thin day still raising
    (exit 1), and the three counters;
  - in "Provider quota handling", note that a 429 on the probe sets the same quota circuit.
- `A/pipeline/direct/AGENTS.md` — only these three soil passages:
  1. the whole `### Entry point` subsection under `## ERA5-Land soil fields`:
     - `--max-days` (default 1, max 1), the 33-request cap and the probe;
     - the key moves requests to the paid customer host once `OPEN_METEO_API_KEY` is set;
     - it is active in production, activated by the executor allow-list;
     - cadence and phase are **not** restated — point at `execution/lane_specs.py`;
     - drop "SHIPS IN SHADOW" and "The archive needs no key";
  2. in `## One distinct day per turn, across all eight products`, the soil budget formula becomes
     `chunks_per_day x --max-days + 1` probe;
  3. in `### One archive request per support chunk-day`, replace the "locations x variables x
     timesteps" / "1,568 x 8 x 1 = 12,544 weighted units" sentences with the pricing rule captured at
     `.omc/research/open-meteo-pricing-20260926.html` (≤ 10 variables and ≤ 14 days cost 1.0 per
     location): one soil day = 1,568 weighted calls.

**§9 Steps after the executor (coordinator and owner, not the executor).**

- [ ] **Evidence row (A18):** one keyless, read-only archive request for the two probe cells over
      a settled 14-day window shows every variable non-null.
- [ ] **Hole-count row (review P8), before the key:** the soil census count of owed days older than
      the probe window. Each costs one 1,570 fan-out per run until drained; the realistic interim
      figure (1,616/day) holds only once it is zero. G6 refreshes the same row.
- [x] **G0 review:** (done 2026-09-26: `g0-critic-review.md` + `g0-code-review-high.md`, three fix batches, `reviews.g0_implementation`) one fresh-context `oh-my-claudecode:critic` refutes: (a) no run can exceed 33
      logical requests, 132 fetch attempts or 1,584 HTTP requests, whatever its flags; (b) no probe-null day is ever
      fanned out; (c) no probe fault can fail the run or silently stall the lane. Verdict →
      `reviews.g0`. Fix batch; monitor sweep + receipt.
- [x] **G0 (owner go): push** (done: `0df6ac50`, 2026-09-27 00:54Z; four deploys `SUCCESS`; observation from the 01:50Z bucket) at a quiescent point (nothing else in the service tree; `ListAgents`
      checked). Observe 24 h with bounded log reads:
  - every soil run has `requests_spent` ≤ 33, `fetch_attempts` ≤ 132 and `http_requests` ≤ 1,584;
  - `probe_status` is `ok`, or **`deferred`, which is expected on the free tier** (A20);
  - a newly settled day is published;
  - runs per bucket are recorded;
  - `invalid_repair_request` failures for soil repair items authored before G0 are expected and
    harmless (review P9(iii));
  - a persistent `invalid`, `blind` or `unavailable` gets a **probe fix** (its own review and go),
    never a revert.
- [ ] **Plan-tier precondition (re-check, flagged out of scope):** the captured pricing page
      (`.omc/research/open-meteo-pricing-20260926.html:92`) says the Historical API needs the
      Professional plan. The owner confirms the key's tier covers the archive endpoint before
      setting it; otherwise every post-key probe returns `unavailable` and soil lags ~14 days.
- [ ] **The owner sets `OPEN_METEO_API_KEY`** on `plantgeo-job-executor` (it redeploys the
      executor). The key is still EMPTY as of 2026-09-26. From then on, **no executor build without
      G0 may run while the key is set** — that includes a Railway dashboard rollback to a pre-G0
      deployment (UNVERIFIED path, re-check (c)): clear the key first.
- [ ] **P5, Open-Meteo usage baseline:** for three full UTC days after the key, read the customer
      portal's usage (if it shows any; A16), compare it with the soil runs' self-reported
      `weighted_calls`, `fetch_attempts` and `http_requests`, and re-issue spec §6.3 from the measured per-request
      cost before G1. If the portal shows nothing, record that and keep the ≥ 1.0 per location floor.

**§10 Post-review amendments (G0 fix batch, 2026-09-26).** Two reviews of the implementation
(`.omc/research/ingestion-grill-20260926/g0-critic-review.md`, `g0-code-review-high.md`) changed
the brief as follows; where §2–§8 disagree, this section wins.

- **Counters (critic G1):** `http_attempts` is renamed `fetch_attempts` (scaffold attempts, ≤ 132),
  and a new `http_requests` counts every request actually sent, through an httpx
  `event_hooks["request"]` hook on the probe's and the chunk fetch's client (≤ 33 × 4 × 3 × 4 = 1,584, including redirect hops;
  because `ingest/http.py::fetch_bounded` re-sends up to 3 times per attempt).
- **`invalid` (critic G2):** window days newer than the product's newest census `data` day are
  gated (`probe_invalid`); production never walks fully ungated. The four-skip lookback runs only
  with `edge_probe=None` (tests); at 33 requests one fan-out per turn is affordable.
- **Base-rung exemption (code-review #3):** a day whose base rung is `data` is never probe-gated.
- **Ordering (code-review #6):** the slot and deadline checks run before the probe gate.
- **Outcome (code-review #5, critic G3):** a product with only absence-recheck gates reports
  `idempotent_noop`; `_product_outcome` tolerates entries without `"day"`.
- **Fault detail (code-review #4, critic G7):** the `unavailable` detail carries the exception class
  and message; `ArithmeticError` and `RecursionError` also map to `unavailable`.
- **Tests (critic G4, G5):** the absence-recheck, throttled, unavailable, next-run and overspend
  tests exercise the branches their names claim; new tests cover R1's spent deadline, a failing
  `probe_cells` (exit 1), `invalid` gating, the base-rung exemption and the `http_requests` hook.
- **Code hygiene (code-review #9, #10):** one day-axis validator; no test-only `_pending_days`
  wrapper; terse docstrings with the rationale in `soil/AGENTS.md`; no review citations in code;
  an explicit raise replaces the runtime `assert`.
- **Not changed, by decision:** the always-on probe (48 weighted/day); zero headroom after one
  fan-out (the next turn restores checkpoints); probe network waits vs the turn deadline
  (pre-existing, critic G6); the frontier tests' 64/160 budgets.

Worst case after G0 (spec §6.3): per run 1 probe × 2 locations + 32 chunks (31 × 50 + 18 = 1,568
locations) = **1,570 weighted** on a clean fan-out, **1,602 hard** (review R4), ≤ 132 HTTP
attempts. Until G1: 28 runs/day (24 forward + ≤ 4 repair) × 1,602 = **≤ 44,856/day** hard
(43,960 clean; was ≤ 244,608). After G1 (6-hourly, no repair): 4 × 1,602 = **≤ 6,408/day**
(6,280 clean). A failure episode adds ≤ 15 × 1,602 = 24,030 once before the breaker holds.

## Phase 1: Framework (dark) + legacy bridge

Goal: land the framework, with every production lane still on the legacy path, plus the legacy
fixes that protect production during the migration. Slices and file ownership are in
`metadata.json` → `partitions`. `f1-config` goes first (after G0); the other six then run in
parallel.

### 1A. `f1-config`: lane config, image plumbing, analysis lattice

- [ ] Task (TDD): `foundation/lane_config/models.py` + `loader.py`: frozen models (incl. per-mode
      `[budget]` caps and `[[streams]]`), a lazy loader taking (directory, `Region`), per-lane
      errors (S8), and the §4.1 invariants.
- [ ] Task (TDD): `tests/lane_config/test_lane_toml_contract.py` over the real `lanes/`, covering:
  - validity and S14 resolution;
  - crons;
  - no footprint literals and no key-shaped values;
  - recheck > lag;
  - gap-fill and pruning off unless enabled (S12);
  - a settled (`refuse`) weighted-provider lane firing > 1/day resolves a strategy with
    `probe_edge` (S6, S19); `write_and_recheck` lanes are exempt.
- [ ] Task (TDD) **C1:** add `COPY lanes/ lanes/` to both stages of
      `services/agri-data-service/Dockerfile`, and add `"lanes"` to
      `scripts/quality_receipt.py::DIGEST_DIRECTORIES`. Update the pinned tuple in
      `tests/scripts/test_quality_receipt.py`. Replace the digest-set enumeration in
      `services/agri-data-service/.dockerignore` with a pointer to `DIGEST_DIRECTORIES` /
      `DIGEST_FILES`. A static test pins the COPY lines and asserts no `.dockerignore` pattern
      excludes a digest input. The directory setting defaults to the right path in the repo and in
      the image.
- [ ] Task (TDD) **C2:** `foundation/region/manifest.py::Region.analysis_lattices` (optional, empty by
      default, so `kenya_highlands.json` / `.ts` stay valid unedited), and the `analysis-0p25` entry
      in `foundation/region/pnw.json` and `src/lib/region/pnw.ts` (+ `region.ts` type). A parity
      test proves the 1,568 cell keys equal soil's `sentinel2-ndvi-0p25deg` keys (plan-cell
      fixture). The existing manifest parity tests follow the files; grep for them before launch.
- [ ] Task: `lanes/_providers/open-meteo.toml` (archive, forecast and historical-forecast hosts
      with customer variants; `api_key_env = "OPEN_METEO_API_KEY"`; `weighted = true`; the weight
      formula incl. models; 5M budget) and `nasa-power.toml` (`time_standard = "UTC"` required).
      `lanes/AGENTS.md`.

### 1B. `f1-runner`: contract, resolution, runner, precedence

- [ ] Task: `pipeline/runner/contract.py`: the spec §4.2 Protocols and value types (draft).
      `pipeline/runner/resolve.py`: the S14 resolver (`importlib`, no per-lane entries). **Nothing
      is added beside the lanes in `pipeline/lanes/`** except `transforms/` and `AGENTS.md`;
      `pipeline/lanes/calendar.py` and `__init__.py` are untouched. `tests/test_layer_import_contract.py`
      passes unedited.
- [ ] Task (TDD): `pipeline/runner/windows.py`:
  - forward edge window; provisional windows end at yesterday UTC;
  - **the S19 probe gate:** `tests/runner/test_windows.py::test_a_forward_fire_fans_out_only_when_the_probe_shows_a_new_settled_day`,
    `::test_a_forward_fire_with_an_unmoved_edge_spends_only_the_probe` and
    `::test_an_unavailable_or_deferred_probe_gates_the_window_and_exits_zero` (O-R3-1);
  - gap-fill hole list capped by capability, retention and budget, and disabled unless enabled;
  - transform dirty days by digest.
  Includes the shortwave livelock regression.
- [ ] Task (TDD): `fetch.py` (per-unit retries; 429 series), `budget.py` (per-mode per-turn caps;
      `tests/runner/test_budget.py::test_a_turn_never_exceeds_its_mode_cap`), `census.py` (full
      ladder), `checkpoints.py` (retrieval-instant keys; per-parameter eligibility).
- [ ] Task (TDD): `writer.py`, with the **S11 rewrite rules** tested three ways: a same-count digest
      change, a partial with more units, and an input change for a transform. Delegate to
      `pipeline/parquet/*`.
- [ ] Task (TDD): `report.py` + `__main__.py`: the S5 report (with `requests`, `weighted_calls`,
      `http_attempts` and `probe`), S4 exits, stdout/stderr discipline; `--compare` cannot construct
      a writer.
- [ ] Task (TDD): `pipeline/lanes/transforms/precedence.py`: `precedence_source`, and
      prune-after-supersession (off by default). It imports only `pipeline/runner/contract.py`.
- [ ] Task: conformance fixtures for five natures (`tests/runner/fixtures/`); `AGENTS.md` files.

### 1C. `f1-providers`: provider client

- [ ] Task (TDD): `ingest/provider_client.py` builds on `ingest/open_meteo_endpoint.py`'s
      free/customer pattern and accepts a single-location (bare object) body. A required empty key
      raises a named config error. `ingest/open_meteo.py` gains the customer forecast and
      historical-forecast hosts. Legacy callers stay byte-for-byte unchanged (pinned).
- [ ] Task (TDD): `ingest/http.py`: a status-aware retry helper; legacy `fetch_bounded_json_sized`
      is unchanged.

### 1D. `f1-executor`: work queue, cron, breaker, ledger, legacy fixes

- [ ] Task (TDD): `execution/cron_schedule.py` (S9).
- [ ] Task (TDD): `execution/lane_catalogue.py`. `tests/execution/test_lane_catalogue.py` pins the
      legacy id set literally and **derives** the config id set from `lanes/`, so a disabled TOML
      needs no test edit. Config wins. No id is on both paths.
- [ ] Task (TDD) **H6/FR-11:** `execution/lane_specs.py::parse_activation` and the kill-switch warn
      and open an incident on unknown ids; they never exit.
- [ ] Task (TDD) **O6/FR-21 soil cadence:** the legacy soil spec in `lane_specs.py` goes to
      `cadence_seconds = 21_600` with `phase_offset_seconds = 3_000` unchanged (buckets at
      00:50/06:50/12:50/18:50 UTC), on top of G0's 33-request cap: ≤ 4 runs/day, ≤ 6,280 weighted/day.
      Its schedule string changes `"50 * * * *"` → **`"50 */6 * * *"`**. Update both pins (review P4):
      `tests/direct/soil/test_lane_registrations.py::EXPECTED_SCHEDULE` and
      `tests/test_job_executor_service.py::EXPECTED_SCHEDULES["soil-era5-land-direct-forward"]`.
- [ ] Task (TDD) **N6/FR-21 climate lag:** the legacy climate `_spec` uses
      `CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS` (5) instead of
      `CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS`; pinned in `tests/execution/test_lane_cadence.py`.
- [ ] Task (TDD) **H5/S15:** implement the work queue.
  - The leader session holds the advisory lock; `run_executor_tick` dispatches without awaiting
    lanes.
  - Each lane gets its own `AsyncSession`, a per-lane advisory lock and
    `jobs/worker.py::run_job_slice`; `jobs/lease.py` is adjusted for per-lane sessions.
  - Leader loss cancels lane tasks.
  - `PLANTGEO_JOB_EXECUTOR_MAX_CONCURRENT_LANES` defaults to **1**, and `max_lanes_per_tick` is
    retired.
  - **S16 switches:** `DISPATCH=queue|serial` and `BREAKER_MODE=split|legacy`.
- [ ] Task (TDD) breaker split, in `lane_scheduling.py` + `sql/execution/select_latest_run.sql` +
      `execution/lane_incidents.py`:
  - exit 75 gets half-open probes and auto-release;
  - code class gets a hold + **one `agri.job_incident` row, no webhook or email** (O4);
  - the post-supersession streak is fixed;
  - no per-tick repeated line.
- [ ] Task (TDD): complete printed commands (parsed in the test); `sql/jobs/refresh_job_run_rollup.sql`
      writes `last_error_summary`; `sql/execution/select_provider_usage.sql`.
- [ ] Task (TDD) **FR-10:** `gap_repair.py::author_gap_repairs` persists every candidate (≥2
      candidates, a rollback-honouring fake). **NEW-1:** re-base `tests/execution/test_gap_repair.py`'s
      `SHORTWAVE` fixtures on `drought` and `vegetation` (both keep their bindings at G1), and expect
      `no_repair_binding` for soil streams.
- [ ] Task: `_unwritten_entries` reads the legacy keys and S5 (pinned both ways).
      `src/lib/server/trpc/routers/jobs.ts` shows cron, next fire, open incidents, failure text and
      provider usage. `execution/AGENTS.md` drift fixes (executor F10).

### 1E. `f1-legacy-bridge`: shortwave drop, repair exclusions

- [ ] Task (TDD) **M5:** remove shortwave from the legacy climate writer's turn, in
      `pipeline/direct/climate/forward.py`, `products.py` and `source.py`: its product iteration,
      its URL parameters and the distinct-clock budget (the budget becomes 397). The shortwave
      stream registration, census and serving stay intact: `CLIMATE_FIELD_PRODUCTS` membership is
      unchanged because `lane_registry.py` and `parquet_ops/coverage.py` read it. Update
      `pipeline/direct/climate/AGENTS.md` and the NASA POWER `### The request budget` subsection of
      `pipeline/direct/AGENTS.md` (794 → 397; review P9(ii), NEW-9).
- [ ] Task (TDD) **N6:** `execution/gap_repair_contract.py::REPAIR_BINDINGS` drops the
      `_product_bindings(CLIMATE_FIELD_PRODUCTS, …)` and `_product_bindings(SOIL_FIELD_PRODUCTS, …)`
      blocks: all 11 legacy climate streams and all 8 soil streams become `no_repair_binding`.
      `tests/execution/test_legacy_repair_exclusions.py` pins every one of the 19 slugs.

### 1F. `f1-registry-bridge`

- [ ] Task (TDD): `lane_registry.py` takes lane facts from the TOML when present, with literals kept
      and parity pinned. `lane_ceiling.py` and `parquet_ops/freshness.py` prefer the probed edge.
- [ ] Task (TDD) **N2/S18:** new `pipeline/parquet/config_stream_registrations.py` holding
      `ConfigStreamRow` data rows (initially empty); `lane_registry.py` turns each row into a
      `LaneRegistration` (a refusal adapter naming the owning strategy module) and splices it into
      `LANE_REGISTRATIONS`, `LANE_REGISTRY` and `CALENDAR_HISTORY_FLOOR`, and the calendar's
      `floor_basis` text names the lane that sets the minimum instead of "fire-detections". New
      `tests/parquet/test_config_stream_registrations.py`: every row equals its lane TOML's
      `[[streams]]` entry; every `[[streams]]` slug has exactly one registration; a fixture row is
      served by `authorized_serving` and listed by `coverage.registered_census_lanes`.
      `tests/parquet/test_calendar_dimension.py` is edited only if it pins the floor's value or text.

### 1G. `f1-serving` (H1)

- [ ] Task (TDD): `planes/layer_service.py` (`ConfigLayerService` **delegates to `parquet_ops`**)
      and `planes/layer_service_registry.py`, keyed by stream slug. Golden responses for every
      current route are unchanged. `planes/signal.py` and `planes/water_gauges.py` are not wrapped
      (they have only test importers).
- [ ] Task (TDD): DI in `app.py` + `interface/http/parquet_routes.py`, with a runtime-import
      annotation test; `GET /layers`; the web client `parquet-layer-metadata.ts` (not yet consumed).
      `parquet_ops/authorized_serving.py` and `warehouse_reader.py` are edited only if a seam is
      needed.

### 1Z. Gate

- [ ] Task: The monitor sweep (Python + web) goes into `evidence/phase1.md`. Run `/code-review high`
      and `/security-review`, and record the verdicts in `reviews.phase1`.
- [ ] Verification: sweep green by gate rows; verdicts recorded; the legacy behaviour changes are
      only FR-8 to FR-11 and FR-21, each listed in the table. [checkpoint marker]

## Phase 2: Two review/fix loops, contract re-freeze, G1

Goal: the framework survives two refutation attempts, and the contract the swarm codes against is
frozen from the code that actually landed (review M1). One author lane runs the fix batches
serially, within `f1-*` files.

- [ ] Task: **R1.** `oh-my-claudecode:code-reviewer` (opus): "refute that this runner carries soil,
      water gauges, drought, evacuation-zones and the precedence transform without a per-lane
      branch". Fix batch, then monitor sweep.
- [ ] Task: **R2.** `oh-my-claudecode:critic` (opus): concurrency (queue, sessions, leader loss),
      flips in both directions, budget under 429 re-asks, the probe gate, transform dirty days, S11.
      Fix batch, then monitor sweep. Both verdicts go in `reviews.phase2`.
- [ ] Task: **Re-freeze** `evidence/contract-freeze.md` from landed code (status: FROZEN at commit
      X), including the stream slug list the S18 mirror will carry. Every later author reads this,
      not the Phase-0 draft.
- [ ] Task: **G1 (owner go): push** at a quiescent point. Observe for 24 h with bounded log reads:
  - every legacy lane settles;
  - FR-10 shows distinct persisted repairs, none on a climate or soil stream;
  - shortwave is gone from legacy turns;
  - soil runs 6-hourly and each run still reports `requests_spent` ≤ 33;
  - `/admin/jobs` shows errors and incidents;
  - the **single-lane peak RSS** is recorded (it sizes G6's concurrency decision).
- [ ] Verification: two verdicts; a FROZEN contract; the G1 observation rows. [checkpoint marker]

## Phase 3: Water gauges early (own gate; spec §7a)

Goal: move water gauges off the degrading legacy host to USGS daily values, serving a daily mean
with real history to 1990-09-30, ahead of the batch. Authored in the shared checkout; nothing else
authors in the service tree until G4's push (O-R3-3).

- [ ] Task (`w3-water-gauges`, opus, no tests run):
  - `ingest/usgs_water_data.py`;
  - `pipeline/lanes/water_gauges/usgs_water_data.py` (`STRATEGY`; per-tile units; the named day
    via `publisher_named_day(time)` verbatim, never a UTC window and never convert-then-truncate;
    identity `monitoring_location_id:time:statistic_id`; approval status column); imports only
    `pipeline/runner/contract.py`, `ingest`, `foundation`, `warehouse`, `pipeline/parquet`;
  - `lanes/water-gauges-daily.toml` (`gap_fill_enabled = false`; `[days] floor = "1990-09-30"`,
    citing `lane_registry.py::_MEASURED_COMPLETE_HISTORY_FLOORS["water-gauges"]`);
  - `lanes/_providers/usgs-water-data.toml`;
  - `warehouse/schemas/water_gauges_daily.py`;
  - **the S18 mirror row** for `water-gauges-daily` in `pipeline/parquet/config_stream_registrations.py`
    (moves `CALENDAR_HISTORY_FLOOR` to 1990-09-30; A19);
  - `tests/lanes/water_gauges/**`; `tests/execution/test_lane_catalogue.py`,
    `tests/parquet/test_lane_contract.py` and `tests/test_job_executor_service.py` only if a derived
    pin still needs a row;
  - the manifest binding `usgs_water_data`;
  - the web switch prepared behind a single stream-name constant: the water reader,
    `WaterDetails.tsx`, `WaterLayer.tsx`, `alert-engine.ts::checkStreamflowAlerts` (its
    `warehouse:water-gauges` source), the slider capability `parquetLanes` entry, the attribution row
    in `parquet-trpc-readers/shared.ts`, the about-page source term (`src/app/about/page.tsx`), and
    `hover-fields.ts` if it names the stream; the matching rows in
    `src/__tests__/services/parquet-trpc-readers.test.ts` and
    `src/__tests__/services/parquet-slider-capabilities.test.ts`. The UI layer key stays `water-gauges`;
  - `docs/lanes/water-gauges.md`.
- [ ] Task: One adversarial reviewer (fresh context) refutes day semantics, per-tile independence,
      the digest-rewrite of approval changes, history depth, the registration, and whether any
      reader joins the calendar for pre-2000 days (A19). Fix batch, monitor sweep, and the verdict
      in `reviews.phase3`.
- [ ] Task: **G2 (owner):** set `USGS_WATER_DATA_API_KEY` if P4 requires it.
      **G3 (owner go):** a commit enabling `water-gauges-daily` and its gap-fill, with sweep and
      receipt refresh, then push at a quiescent point. The legacy IV lane keeps serving. The
      calendar floor moves to 1990-09-30; its next version carries the earlier days (plus a one-off
      calendar export under this go only if A19's check found a reader that needs them).
- [ ] Task: **Validation rows** (there is no row compare across APIs):
  - 10 gauges × 5 days against the modern daily values, and against legacy `dv` while it exists;
  - forward/gap-fill equality on overlapping days;
  - the named-day check;
  - **history depth: the census holds no owed day in [1990-09-30 (or the A14-lowered floor), edge].**
- [ ] Task: **QA rows:** ≥72 h of forward turns with exit 0 and a correct S5 report; the re-pull
      progressing within budget.
- [ ] Task: **G4 (owner go), only when every validation row passes, history depth included:** flip
      the stream-name constant and the attribution (sweep + receipt + push), then pause the legacy
      `water-gauges-direct-forward` through the ledger verb. Rollback: re-point and resume the
      legacy lane.
- [ ] Verification: review verdict, validation rows and QA rows recorded; G4 done. [checkpoint marker]

## Phase 4: Extraction, contract registration, swarm (dark)

Goal: every remaining lane has a strategy + TOML that passed its own review, built without
importing anything Phase 7 deletes. **Everything here starts after G4's push**, sequentially, in
the shared checkout (O-R3-3).

- [ ] Task: **`p4-extract`** (after G4's push). Move what non-lane modules and the shadow
      strategies import from `pipeline/direct/**`, **with the full import closure** (review P6):
  - the climate, soil and vegetation product catalogues → `warehouse/field_products/`;
  - → `pipeline/lanes/<layer>/` (each with its `__init__.py`):
    - burn-severity: `source_protocol.py`, `mtbs.py`, `products.py`;
    - drought: `source_protocol.py`, `usdm.py`, `products.py`;
    - evacuation-zones: `watermark.py`, `products.py`, `rows.py`, `source.py`, `support.py`;
    - watersheds: `watermark.py`, `source.py`;
    - sensors: `merge_sensors_day`;
    - crop-cover: `source_protocol.py` (`CropCoverSource`, `USDA_CROP_COVER_SOURCE`), `source.py`,
      `products.py`;
    - land-context: `source_protocol.py` (`LandContextSource`, `BLM_LAND_CONTEXT_SOURCE`),
      `source.py`, `products.py`.
  - None of those modules imports further `pipeline/direct/**` code (grep at launch), so no
    `pipeline/lanes/**` module imports `pipeline/direct/**`.
  - Re-point `pipeline/source_bindings.py`, `lane_registry.py` (import lines only),
    `parquet_ops/coverage.py`, `snapshot_product_catalog.py` and `sensor_absence_correction.py`.
  - Legacy files become re-export shims with unchanged behaviour.
  - Legacy tests that monkeypatch an attribute of a moved module are re-pointed to the new module
    (edit-if-needed; list in `metadata.json`).
  - Monitor sweep + receipt on `main`; one adversarial reviewer; verdict in `reviews.phase4`.
- [ ] Task: **`p4-contract-freeze`** (after `p4-extract`): stream-registry and lane-contract test
      expectations; **the S18 mirror rows for the 24 climate streams** (from the FROZEN contract; the
      calendar floor moves to 1984-01-01, A19); the remaining provider files (list in
      `metadata.json`); the additive manifest bindings for `meteorology-*` / `shortwave-*`. Its
      reviewer repeats the A19 check for the climate readers.
- [ ] Task: **The swarm** (re-verify partitions: HEAD vs `computed_at_commit`, a real grep of every
      `owns`, `ListAgents`). There are 13 slices; briefs carry the FROZEN contract, the lane's §7
      row, its evidence section and its read-only legacy modules.
  - All TOMLs ship `executor = "legacy"` (migrated) or `enabled = false` (new/shadow), with
    `gap_fill_enabled = false`, per-mode `[budget]` caps and a `[[streams]]` table.
  - Settled weighted-provider lanes firing more than once a day implement `probe_edge` (S6, S19).
  - `s-climate`: 3 ingest lanes + 2 transforms; S10 nodes computed by integer arithmetic (never
    soil's `nearest_native_grid_point`); the provisional window ends yesterday UTC; the POWER
    re-grid; the newest-first re-pull order; **no web files** (`p6-climate-rebind` owns them).
  - The other 12 slices: see `metadata.json` (the lane-specific acceptance is in each slice's
    `task`).
- [ ] Task: Per-lane adversarial reviews (one reviewer each, separate context), one verdict row per
      lane in `evidence/phase4.md` and `reviews.phase4`. Fix batches per lane.
- [ ] Task: Monitor sweep → **G5 (owner go): push dark** at a quiescent point. Confirm the deployed
      catalogue lists every new TOML as legacy or disabled, and record the calendar floor.
- [ ] Verification: a verdict per lane; sweep green; the catalogue matches spec §7. [checkpoint marker]

## Phase 5: Compare, cut-over, climate gap-fill flips

- [ ] Task: **G6 (owner go), one sequence with an abort condition:**
  1. Compare runs in the executor container (`--mode forward --compare`, read-only) for the 10
     migrated active lanes, recorded as rows. **Abort G6 if any row shows an unexplained delta.**
  2. Refresh the G0 soil hole-count row (hole-runs × 1,568 = the expected soil gap-fill spend; the
     cap is 1,600/turn, one daily fire, ≤ 48,000/month).
  3. One `p5-cutover` commit:
     - `executor = "config"` on burn-severity, drought, evacuation-zones, fire-detections,
       fire-perimeters, sensors, soil, vegetation, watersheds and weather-observations;
     - `gap_fill_enabled = true` on those lanes (**this fills vegetation 2026-09-01..05**);
     - `enabled = true` on the 5 climate lanes, with their gap-fill still off;
     - provisional pruning on (old G10).
     - If the G1 RSS × 2 fits with margin, the owner also sets
       `PLANTGEO_JOB_EXECUTOR_MAX_CONCURRENT_LANES=2`.
  4. `oh-my-claudecode:critic` reviews the diff and rollback (verdict in `reviews.phase5`); monitor
     sweep + receipt; push at a quiescent point.
- [ ] Task: **G7 (owner go; ≈1.75M Open-Meteo calls + POWER requests):** one commit flipping
      `gap_fill_enabled` on `meteorology-era5-settled` (the five-layer re-pull to 1984,
      newest-first, 8,000/turn), `shortwave-nasa-power-settled` (the mandatory re-grid) and
      `meteorology-ifs-provisional` (the historical-forecast bootstrap). If Phase 0 recorded a split,
      the re-pull's `gap_fill_max_weighted_calls` is lowered so it spans two months. Sweep + receipt,
      then push. Watch provider month-to-date daily.
- [ ] Verification: each flipped lane's first config turn has exit 0 and an S5 report; the re-pull is
      within budget. [checkpoint marker]

### Rollback (from G0/G3/G6 until that lane's Phase-7 deletion)

1. **Brake (no deploy):** `agri-service ops jobs-set-lane-enabled --definition plantgeo.executor.<lane> --disabled --operator <who> --reason <why> --apply`.
2. **Revert (owner go):** set `executor = "legacy"`. **Run the monitor sweep and refresh the receipt
   before the push** (S13), at a quiescent point (S17). The same definition key resumes the legacy
   checkpoint.
3. **G0 revert (review P7):** the owner **first clears `OPEN_METEO_API_KEY` — mandatory** once it is
   set — then a revert commit (sweep + receipt + go). A probe fault is fixed forward (a probe-fix
   push under its own review and go), never by a revert.
4. **Shared runtime:** the S16 switches (`DISPATCH=serial`, `BREAKER_MODE=legacy`, concurrency 1)
   or a revert commit.
5. **Climate and water gauges:** re-point the web back; the legacy lanes are still intact.
6. **Triggers:**
   - any exit 70/78 on a flipped lane;
   - a new `gap_range` versus the pre-G6 baseline;
   - a compare delta appearing in production;
   - a soil run reporting `requests_spent` > 33, `fetch_attempts` > 132 or `http_requests` > 1,584 (a cap breach: brake, then
     the G0 revert above);
   - an owner call.
   - Not triggers: `probe_status = "deferred"` on the free tier; `invalid_repair_request` on
     pre-G0 soil repair items (spec A20).

## Phase 6: QA round, validation round, climate rebind

- [ ] Task: **QA rows per lane** (`oh-my-claudecode:verifier`, read-only):
  - ≥3 scheduled forward turns (hourly: 72 h; daily: 3; weekly: 2 releases) with exit 0 and
    correct S5;
  - one gap-fill turn;
  - no new `gap_range`;
  - the published edge within the declared lag of the probed edge;
  - budget held (per-turn caps and provider month-to-date);
  - RSS at the configured concurrency.
- [ ] Task: **Validation rows per lane** (a different reviewer, read-only): 5 cells × 3 days against
      upstream; served values via `/api/v1/parquet/*` and `/layers`; governed absences carry
      proofs.
  - Climate also checks the ERA5-vs-POWER overlap distributions, the NE-node vs centroid seam, wind
    at 10 m, the extent, and cell-key equality with soil.
- [ ] Task: **G8 (owner go), the climate rebind** (`p6-climate-rebind`): stream reads (the web
      readers, the slider capability `parquetLanes`, `ClimateFieldLayer.tsx`, `climate-field.ts`,
      `agent/report.py`, the manifest bindings) move to the `meteorology-*` / `shortwave-*` slugs;
      UI layer keys stay; **soil wetness is removed** from every web list
      (`regional-intelligence.ts`, `regional-analysis-workflow.ts`, `layer-region-binding.ts`,
      `climate-field.ts`, both region manifests and the rest of the metadata list) and their tests;
      the legacy climate streams stay readable. Web + Python sweep, push, then a browser check.
- [ ] Task: A failing lane rolls back, gets one fix batch and one sweep, and re-enters QA.
- [ ] Verification: both rows for every flipped lane; G8 done and browser-checked. [checkpoint marker]

## Phase 7: Deprecation

Order matters (review N8): the shared files lose their imports first, then the modules go.

- [ ] Task (`d7-legacy-shared`, first): remove the migrated entries and ids, the lane-fact literals,
      and every import of a `pipeline/direct/<lane>` module from `lane_specs.py`,
      `gap_repair_contract.py`, `lane_ids.py`, `lane_registry.py` and `job_executor_service.py`;
      remove `gap_repair*` authoring + `RepairAuthoringClock` + the allow-list parse (the
      kill-switch stays) and **`mtbs-forward`** (O7). Update `AGENTS.md` files and the lean RUNBOOK
      row.
- [ ] Task (`d7-legacy-lane-modules`, second): for each lane with both Phase-6 rows, delete its
      `pipeline/direct/<lane>/**` (including the `p4-extract` shims), the legacy water-gauges
      modules and the legacy tests.
  - **Grep importers first**; none may remain outside the files being deleted. `p4-extract` moved
    the whole import closure, so none should.
  - The legacy `climate-field-*` / `soil-wetness-*` / `water-gauges` schema modules stay (their
    history remains readable).
  - Shadow lanes' legacy modules stay.
- [ ] Task: Monitor sweep; `/code-review high` + `/simplify`; verdict. **G9 (owner go):** push;
      retire `climate-nasa-power-direct-forward` and `water-gauges-direct-forward`.
- [ ] Task: Closure: a retrospective row in `evidence/phase7.md`, `tracks.md` status, memory notes,
      and follow-ups (id renames, 3DHP, the ML wind feature change, the FIRMS cap, a lazy
      `LANE_REGISTRY` so the S18 mirror becomes synthesis).
- [ ] Verification: no importer of a deleted module remains; sweep green; verdict. [checkpoint marker]

---

## Owner gates (nothing below happens without an explicit go)

| gate | step | effect |
|---|---|---|
| G0 | push the legacy soil cap (FR-24); after 24 h and the hole-count row, **the owner sets `OPEN_METEO_API_KEY`** | Redeploys the listed services, then the executor. Soil: ≤ 33 requests (≤ 1,602 weighted hard / 1,570 clean, ≤ 132 fetch attempts (≤ 1,584 HTTP requests incl. transport retries and redirect hops)) per run; interim ceiling ≤ 44,856/day on the paid key. Precondition: the owner confirms the key's tier covers the archive API. Any later G0 revert — including a dashboard rollback — requires clearing the key first. |
| G1 | push phases 1–2, dark | Redeploys the listed services. Concurrency 1. FR-8/9/10/11 and FR-21 (shortwave drop, climate + soil repair exclusions, climate lag, soil `"50 */6 * * *"`) take effect. |
| G2 | **owner sets** `USGS_WATER_DATA_API_KEY` before G3 if P4 requires it | Redeploys the executor. |
| G3 | enable `water-gauges-daily` + its DV re-pull to 1990-09-30 | Writes a new stream in parallel with legacy; the calendar floor moves to 1990-09-30 (next calendar version, A19). |
| G4 | water serve switch + legacy water paused — **only after the history-depth row passes** | Users see daily means back to 1990-09-30. Phase 4 may start after this push. |
| G5 | push `p4-extract` + the swarm, dark | No new lane runs; the calendar floor moves to 1984-01-01 (next calendar version, A19). |
| G6 | compare runs → cut-over push (+ migrated gap-fill incl. vegetation 09-01..05 and soil ≤ 1,600/turn, + pruning, + concurrency 2 if the evidence holds) | 10 lanes on the runner; 5 climate lanes start without history. |
| G7 | climate gap-fill flips | ≈1.75M Open-Meteo calls over ~9 days (or two months if split); the POWER solar re-grid; the IFS bootstrap. |
| G8 | climate rebind + web deploy | Users see Open-Meteo climate, wind at 10 m, the smaller extent, no soil wetness. |
| G9 | deprecation push(es) | Deletes code; retires legacy climate and water gauges; deletes `mtbs-forward`. |

## Tripwires

`metadata.json` → `partitions.tripwires` holds the full list. These are the load-bearing ones:

- G0 lands before any Phase-1 authoring; the key is set only after G0's observation; clearing the key
  is mandatory before any G0 revert.
- A soil probe fault exits 0 (`unavailable`/`deferred`); only a thin fan-out or a changed support
  exits 1.
- A TOML change without a sweep and receipt refresh fails the image build; every gated push is made
  at a quiescent point in the one shared checkout (S17; no worktrees).
- Nothing shared lives in `pipeline/lanes/`; the contract is `pipeline/runner/contract.py` (S14).
- Swarm lanes never edit shared framework files or `pipeline/direct/**`, and never import from
  `pipeline/direct/**` or another layer's `pipeline/lanes/<layer>/`.
- New streams are registered through the S18 mirror, never by a swarm lane.
- Sanic-ext injected types stay runtime imports.
- POWER URLs pin `time-standard=UTC`.
- Provisional lanes never write today.
- ERA5 nodes are computed by integer arithmetic; soil's `nearest_native_grid_point` is never reused
  at 0.25°.
- The water-gauges day is `publisher_named_day(time)` verbatim: never a UTC window, never
  convert-then-truncate.
- Compare mode cannot write.
