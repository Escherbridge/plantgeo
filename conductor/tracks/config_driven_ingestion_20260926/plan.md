---
type: Implementation Plan
title: Config-driven ingestion — legacy soil cap (G0), observability and soft-failure wave (GL-1–GL-5), framework, hardening loops, early water gauges, swarm cut-over, production QA, supersession cleanup lane
tags: [config_driven_ingestion_20260926]
resource: ./spec.md
---

# Implementation Plan: Config-driven ingestion

## Overview

This plan follows the owner's migration shape (spec D10). It overrides push-small for this effort
on purpose. It was revised 2026-09-26 for review loop 1 and the round-2 owner answers (spec §3.3,
§14), again for review loop 2 (§3.4, §15), and a third time for review loop 3 and the round-3 owner
decisions (§3.5, §16). It was revised a fourth time 2026-09-27 for the owner's request of
2026-09-26, a dedicated cleanup lane (spec §4.8, §18): Phase 7 is now that lane. It was revised a
fifth time 2026-09-27 for the owner's second request of 2026-09-26, an observability and
soft-failure wave (spec §4.9, §19; answers §3.6): Phase 0W ships GL-1–GL-5 as small pushes, and the
hold ladder (the design's GL-6) is folded into `f1-executor` (1D).

    G0 legacy soil cap (own gate) → owner sets the Open-Meteo key
      → Wave O: observability + soft failure (GL-1 … GL-5, small pushes, before any Phase-1 authoring)
      → framework (dark) → two review/fix loops + contract re-freeze → water gauges early (own gate)
      → extraction (after G4) + swarm → batch cut-over → per-lane QA + validation (+ climate rebind)
      → supersession cleanup lane (verbs → readiness → quarantine → observe → delete → scaffold;
        deletes no data)

| phase | ships | owner gate | review at the boundary |
|---|---|---|---|
| 0 | review loops 1–3 folded in; read-only probes P1–P4; contract **draft**; **G0 legacy soil cap**; P5 usage baseline | **G0** (then the owner sets `OPEN_METEO_API_KEY`) | loops 1–3 done (all CHANGES-REQUIRED; spec §14–§16); **targeted re-check** of P1–P9 + G0 (O-R3-2); G0's own `oh-my-claudecode:critic` |
| 0W | **Wave O** (spec §4.9): logging contract, source metering, usage report, soft-failure basics; five small pushes after G0's 24 h observation | **GL-1, GL-2, GL-3, GL-4, GL-5** | per push: `/code-review high`; `/security-review` (GL-1–GL-3); `oh-my-claudecode:critic` (GL-5) |
| 1 | framework, dark; legacy bridge fixes; manifest lattice; image/receipt plumbing; registration mirror; Wave O's G1 half (hold ladder, paid cap, monthly receipt) | — | `/code-review high` (runner, executor, SQL); `/security-review` (keys, `/layers`, operator command) |
| 2 | two review/fix loops; contract **re-frozen from landed code** | **G1** | R1 `oh-my-claudecode:code-reviewer` (opus); R2 `oh-my-claudecode:critic` (opus) |
| 3 | water gauges early lane on USGS daily values | **G2** (USGS key if needed), **G3**, **G4** | one adversarial reviewer for the lane |
| 4 | extraction (sequential, after G4's push); contract registration; swarm of 13 lanes, dark | **G5** | one reviewer for `p4-extract`; one adversarial reviewer per lane |
| 5 | compare runs + batch cut-over; climate gap-fill flips | **G6**, **G7** | `oh-my-claudecode:critic` on the cut-over diff and rollback |
| 6 | per-lane QA round + validation round; climate rebind | **G8** | QA `oh-my-claudecode:verifier`; validation by a different reviewer |
| 7 | **supersession cleanup lane** (spec §4.8): cleanup verbs; readiness dossier; repair retire; per-cohort quarantine, observation and deletion; scaffolding and docs closure; data never deleted | **G9.0, G9V-1a, G9Q-1a, G9V-1b, G9Q-1b, G9D-n, G9R-n, G9Q-2, G9S** (G9X never scheduled) | per push, §7.4: `/code-review high` + `/security-review` (G9.0); critic on the 7R dossier; critic + `/code-review high` (each 7Q); `/code-review high` + `/simplify` + critic (each 7D); `/code-review high` + critic (7S) |

### Working rules (binding on every phase)

1. **Authors run no tests, lint, type-check or build.** Each author reports its predicted failures;
   the coordinator records them as rows in the phase evidence table.
2. **One integrated sweep per phase, run by a separate monitor lane.** In Phase 7 each push gate
   (G9.0, G9Q-1a, G9Q-1b, G9D-n, G9Q-2, G9S) counts as a phase (spec CA16); in Phase 0W so does each
   Wave O push (GL-1–GL-5). From `services/agri-data-service`:
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
   files. The only other file is `evidence/contract-freeze.md`. Phase 0W writes into
   `evidence/phase0.md` under one "GL-n" section per push.
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
- [ ] Task: **Wave O** — Phase 0W below, after G0's 24 h observation and before any Phase-1
      authoring (spec §3.6 WQ-3).
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

## Phase 0W: Observability and soft failure (Wave O; GL-1–GL-5)

Owner request 2026-09-26 (verbatim in spec §19): robust logging in every lane, graceful soft
failure (no run may break permanently or break another lane), logs usable for efficiency and for
auditing that we do not abuse our sources; alerting later. Spec §4.9 defines the wave, §3.6 records
the answers WQ-1–WQ-7 and the publication-debt alignment (PD). Design record:
`.omc/research/ingestion-grill-20260926/observability-wave-design.md` (revision 2, **not
re-reviewed as a whole**; its next adversarial passes are the per-push reviews below and, for the
folded ladder, the Phase-1/2 reviews of `f1-executor`). Slices: `metadata.json` → `partitions`
(`o1-logging-core`, `o3-ingest-meter`, `o2a-exit-classes`, `o5a-executor-observability`,
`o4-usage-report`, `o2b-incidents`, `o5b-soft-failure`); tripwires wave-o-1–wave-o-11. Paths are
relative to `services/agri-data-service/src/agri_data_service/` (tests: `services/agri-data-service/tests/`).

Goal: before any Phase-1 authoring, every lane logs one redacted JSON contract, every upstream send
is metered and attributed to a turn, a failure is recorded on an incident row without failing a
tick or another lane, and "are we abusing a source?" is answered by one read-only report.

**Preconditions (every push):**

- G0's 24 h observation is closed (`evidence/phase0.md`).
- **Launch waits for a quiescent service tree:** `git status --porcelain -- services/agri-data-service`
  is empty **at launch**, not only at the push. At `d37c202f` it is not (another session's
  `agent/**`, `interface/cli/agent.py`, `routes/agent_tools.py` and test edits). The
  publication-debt merge (owner 2026-09-27; `execution/job_executor_service.py::TurnReport.incomplete`
  counts `publication_debt`) must also have landed: `o5a` and `o5b` edit the same files. If the tree
  is not quiescent within 48 h of G0's observation closing, the coordinator asks the owner (A25).
- The partitions are re-grepped at HEAD (stamps are `85c4b8f4`), incl. the widened cleanup-lane
  check (wave-o-9).
- **Before GL-1 only:** the coordinator reads Railway's docs for the stderr JSON `level` mapping, the
  per-replica log-rate limit and the maximum line size, and records them in
  `foundation/observability/AGENTS.md` (A23).

```
G0 observed (24 h) + quiescent tree + Railway facts recorded
  -> GL-1 o1-logging-core ------------------------------------ push, smoke read (<= 1 h)
  -> GL-2 o3-ingest-meter ------------------------------------ push, smoke read
  -> GL-3 o2a-exit-classes -> o5a-executor-observability ----- push, 24 h observation
  -> GL-4 o4-usage-report ------------------------------------ push, smoke read
  -> GL-5 o2b-incidents -> o5b-soft-failure ------------------ push, 24 h observation
  -> Phase 1 (f1-config first). GL-6 (the ladder) is folded into f1-executor (1D) and ships at G1.
```

**Each push:** authoring (authors run nothing) → one monitor sweep (rows in `evidence/phase0.md`
§GL-n) → reviews in separate contexts → one fix batch + one re-sweep → owner go → push → confirm that
every service built from the agri image matches (`railway deployment list`) → observation. A push
whose non-test diff exceeds ~500 lines splits at its named seam. Each GL commit contains exactly that
push's files (S17). No variable is required and no secret is set.

### 0W.1 GL-1: `o1-logging-core` (sonnet; seam GL-1a everything except the router / GL-1b `router.py` + `usage.py`)

- [x] Task (TDD): `foundation/observability/logging.py::configure_logging(profile, *, long_running=False, console=False)`
      ported from `app.py::create_app`'s chain and pinned in order (spec §4.9.1); a `StreamSink`
      that resolves `sys.stdout`/`sys.stderr` when it writes; Railway levels; the redacted stdlib
      bridge with pinned WARNING floors; `logging.captureWarnings(True)`; a deferred
      `get_logger(name)`. `app.py`'s configure block and `jobs/worker.py`'s module logger switch to it.
- [x] Task (TDD): `foundation/observability/redaction.py` (`redact_strict`, `redact_for_log`,
      `redact_value`, `describe_error`, `register_secret_values`); `jobs/lease.py::redact_text` and
      `ingest/results.py::redact_secrets` become re-exports; the dead `run_isolated_job` /
      `any_job_failed` are deleted; `describe_error` replaces `error=str(error)` at the three
      `routes/ops.py` sites.
- [x] Task (TDD): `events.py`; `vocabulary.py`, **frozen at the end of GL-1** (`ExitClass` incl.
      `infra`, `TurnOutcome`, incident kinds, metric keys, pool labels, `LANE_LOGICAL_CAPS` from
      `budget_headline` (soil 1,602), log levels; no `POOL_BULK_LANES` and no `pool_saturated`, WQ-4);
      `usage.py` (host → provider and pool label, the Open-Meteo weight table, the usage line);
      `router.py` (`ChildLogRouter`, the module only; wired at GL-3); `bootstrap.py::arm_from_environment`,
      called once and guarded from the package root `__init__.py`.
- [x] Task: `interface/cli/root.py::cli` configures `tool` for every group **except `agent`**;
      `tests/conftest.py` gains one autouse fixture (deletes `PLANTGEO_TURN_*`; after each test
      `structlog.reset_defaults()`, restores the stdlib root's handlers and level,
      `logging.captureWarnings(False)`); `docs/env-vars.md` rows (the coordinator adds them if the
      file is dirty at launch); `foundation/observability/AGENTS.md`.
- Tests (authors write them; they run none):
  - `tests/foundation/observability/test_logging.py`: `test_service_profile_routes_debug_info_warn_to_stdout_and_error_to_stderr`,
    `test_tool_profile_renders_json_with_level_to_stderr`, `test_every_line_is_one_flat_json_object_with_event_level_timestamp_service`,
    `test_warning_renders_as_railway_warn`, `test_configure_is_idempotent_and_reaches_module_level_loggers`,
    `test_exception_locals_never_reach_the_line`, `test_dict_tracebacks_and_show_locals_are_never_configured`,
    `test_debug_level_never_enables_third_party_debug`, `test_engine_execute_under_debug_emits_no_sql`,
    `test_stdlib_records_are_redacted`, `test_turn_context_from_environment_is_bound`,
    `test_sinks_resolve_streams_at_write_time`, `test_configure_inside_clirunner_does_not_capture_later_output`,
    `test_worker_logger_follows_configuration_after_import`, `test_non_string_leaf_repr_is_redacted`
    (an `httpx.Request` with `?apikey=CANARY`), `test_sqlalchemy_traceback_drops_sql_and_parameters`,
    `test_python_warnings_route_as_warn`, `test_agent_group_is_not_reconfigured`.
  - `test_redaction.py`: revision 1's eleven (design record §6.1) plus
    `test_dotenv_and_dsn_password_values_are_scrubbed`, `test_truncation_never_leaves_a_secret_prefix`,
    `test_key_match_is_casefolded_and_manifest_key_survives`, `test_depth_limit_stubs`.
  - `test_router.py`: revision 1's eleven plus `test_rate_bucket_drops_info_keeps_error_report_lane_turn`,
    `test_unterminated_line_over_64_kib_becomes_a_stub`, `test_third_party_warning_prefix_routes_as_warn`,
    `test_unclosed_usage_pid_flags_usage_incomplete`, `test_latest_last_send_outcome_wins_across_usage_lines`.
  - `test_events.py` (unchanged from revision 1).
  - `test_usage.py`: `test_turn_usage_line_is_flat_bounded_numeric_and_carries_level`,
    `test_zero_host_usage_line_is_still_written`, `test_provider_and_pool_resolve_from_host`,
    `test_open_meteo_weight_table` (parametrised per URL shape), `test_weight_equals_g0_on_soil_request_builder`,
    `test_logical_caps_come_from_budget_headline`, `test_rss_is_null_when_resource_is_unavailable`.
  - `test_bootstrap.py`: `test_arming_happens_only_with_a_turn_id`,
    `test_usage_line_survives_an_unflushed_report_under_python_m` (20 KiB, and 7 + 2 KiB),
    `test_manual_python_m_run_emits_one_operator_line`, `test_script_file_main_is_operator_origin`,
    `test_executor_and_pytest_register_no_operator_line`, `test_sigterm_keeps_the_default_disposition`,
    `test_garbled_switch_resolves_to_legacy`, `test_invalid_number_falls_back_with_one_warning`.
  - `tests/interface/test_ops_panel_log_redaction.py::test_every_ops_error_site_logs_class_name_only`;
    `tests/test_app_logging.py::test_web_process_lines_are_redacted_json`.
- Sweep proof: `test_bootstrap.py::test_usage_line_survives_an_unflushed_report_under_python_m`; the
  redaction suite; `test_configure_inside_clirunner_does_not_capture_later_output`;
  `tests/test_layer_import_contract.py` and `tests/interface/test_availability_cli.py` pass **unedited**.
- Reviews: `/code-review high` + `/security-review` → `reviews.phase0_gl1`.
- [x] **GL-1 (owner go): push.** (done: `f2a27473`, 2026-09-27 20:20Z; four deploys `SUCCESS`; row 3 PASS, `evidence/phase0.md` §GL-1) Smoke read (≤ 1 h): row 3 (secret probe) on the executor and the
      web services. [checkpoint marker]

### 0W.2 GL-2: `o3-ingest-meter` (sonnet)

- [ ] Task (TDD): `ingest/http.py`: request and response hooks in `upstream_client` and a new
      `upstream_sync_client()` (both gain a test-only `transport=` keyword; G0's hook coexists);
      per-host counters, `last_send_outcome` + `last_send_at`, `bytes_in` via `_read_bounded_body`,
      `transport_failures` apart from 5xx; every hook fail-open into `meter_errors`;
      `PLANTGEO_UPSTREAM_TELEMETRY`; the WQ-5 User-Agent (a caller's own header wins).
- [ ] Task (TDD): `ingest/upstream_retry.py`: `UpstreamRetryPolicy.probe_attempts: int | None = None`
      (a per-policy opt-in) and backoff accounting. `ingest/usgs_nwis.py`: per-tile retry, the USGS
      policy (3 attempts, 45 s ceiling, `probe_attempts=1`), all-or-nothing kept, the failed tile
      named.
- [ ] Task: `pipeline/direct/burn_severity/capture.py::_capture_snapshot` swaps
      `httpx.Client(follow_redirects=False, trust_env=False)` for `upstream_sync_client(...)` with the
      same arguments (the one lane edit); `ingest/AGENTS.md` metering sections.
- Tests:
  - `tests/test_ingest_http_meter.py`: revision 1's twelve (incl. `test_raising_meter_never_fails_the_send`
    and `test_telemetry_off_is_byte_for_byte_legacy`) plus `test_last_send_outcome_is_recorded` and
    `test_factories_accept_a_transport`.
  - `tests/test_ingest_sync_client.py`: revision 1's two.
  - `tests/test_no_raw_http_clients.py::test_no_raw_http_clients_outside_the_allow_list` (allow-list
    per spec §4.9.2; an entry whose file is gone or matches nothing never fails the guard).
  - `tests/test_ingest_upstream_retry.py`: `test_evacuation_zones_policy_keeps_six_attempts_under_probe`,
    `test_usgs_policy_uses_one_attempt_under_probe`.
  - USGS: revision 1's seven, in `tests/test_ingest_usgs_nwis.py`.
- Sweep proof: `test_raising_meter_never_fails_the_send`; `test_telemetry_off_is_byte_for_byte_legacy`;
  the raw-client guard; the USGS bounds.
- Reviews: `/code-review high` + `/security-review` (secrets, the User-Agent) → `reviews.phase0_gl2`.
- [ ] **GL-2 (owner go): push.** Smoke read: row 6 (every active lane settles as the day before).
      [checkpoint marker]

### 0W.3 GL-3: `o2a-exit-classes` (sonnet) → `o5a-executor-observability` (sonnet, high effort)

- [ ] Task (TDD, `o2a`): `execution/exit_classes.py::classify_exit` with the R1–R4 tables and
      `WRAPPER_EVIDENCE` (spec §4.9.3); each fixture is built from that lane's real `main()` print.
- [ ] Task (TDD, `o5a`), in `execution/job_executor_service.py` and `execution/turn_reports.py`:
  - `run_scheduled_command` makes a uuid4 `turn_id` its first act, passes the `PLANTGEO_TURN_*`,
    `PLANTGEO_LANE_ID` and `PLANTGEO_ATTEMPT` environment, and stamps `metrics.turn_id` and
    `metrics.spawned` (false on every return before `create_subprocess_exec`, true right after);
  - the router tee in front of the sinks (the raw tails, `parse_terminal_report` and
    `_command_failure_reason` still get raw bytes); `_drain_both` gathers with `return_exceptions=True`;
  - the usage fold into `job_attempt.metrics` (spec §4.9.2 keys, the charging basis, `usage_open` /
    `usage` paired by pid); the merged `publication_debt` metric is kept;
  - `classify_exit` stamping, **observational only**; `turn_outcome` `incomplete` also for
    publication debt (PD);
  - the report parse (the last `plantgeo_lane_turn_report` line, else the last JSON object with no
    `level` key);
  - tick volume: `tick_started`, `leader_*` and `tick_healthy` to debug; the summary on a change and
    hourly; exactly one `plantgeo_job_executor_lane_turn` per terminal handler outcome, pre-spawn
    included; `operator_action_required` gains `exit_class`;
  - `execution/AGENTS.md` "Turn reports" and "Command stderr reaches the ledger".
- Tests:
  - `o2a`, `tests/execution/test_exit_classes.py`: `test_each_legacy_lane_real_failure_print_classifies`
    (every §4.9.3 lane × {429, 503, `KeyError`}), `test_wrapper_needs_a_failed_last_send`,
    `test_wrapper_after_successful_last_send_is_code`, `test_infra_tokens`, `test_conflict_is_code`,
    `test_stderr_fallback_reads_last_exception_line_only`, `test_timeout_is_hang`,
    `test_pre_spawn_is_config`, `test_missing_class_is_code`.
  - `o5a`: revision 1's tests in the new `tests/execution/test_child_log_router_wiring.py`,
    `test_attempt_metrics.py`, `test_tick_volume.py` and `test_end_to_end_child_routing.py`, plus
    `test_spawned_marker_distinguishes_pre_spawn_from_spawned`, `test_turn_id_on_every_lane_turn_including_pre_spawn`,
    `test_exit_class_is_stamped_from_classify_exit`, and in the end-to-end file
    `test_report_intact_with_an_armed_python_m_child`; pins in `test_command_stderr_capture.py`,
    `test_operator_action_surface.py` and `tests/test_job_executor_service.py`.
- Sweep proof: `tests/execution/test_end_to_end_child_routing.py::test_a_real_child_through_run_scheduled_command_routes_redacts_and_meters`.
  The child is a temporary module run with `python -m`. It raises with canaries in its locals, prints
  a legacy success line on stderr, prints a DSN, a bearer token and a repr header, drives the meter
  through `transport=`, and prints an **unflushed** report. Assertions (via `capfd`): stderr carries
  only error lines; no canary appears anywhere; the report is intact; `metrics.usage.hosts` is
  populated; exactly one `lane_turn`.
- Reviews: `/code-review high` + `/security-review` (the tee) → `reviews.phase0_gl3`.
- [ ] **GL-3 (owner go): push.** 24 h observation: rows 1, 2, 3, 5, 6, 8, 9. [checkpoint marker]

### 0W.4 GL-4: `o4-usage-report` (sonnet)

- [ ] Task (TDD): `sql/execution/select_provider_usage.sql` (per period, pool, provider, host and
      definition over `jsonb_each(metrics->'usage'->'hosts')`) and
      `sql/execution/select_provider_month_to_date.sql` (one statement; CTEs `epoch`, `metered`,
      `lost`; `:logical_caps` from `vocabulary.LANE_LOGICAL_CAPS`); `execution/usage_report.py` with
      `month_to_date(session, *, pool, now)` as the **only** loader of the second file; repair
      definitions roll up to their lane by stripping `REPAIR_LANE_SUFFIX`.
- [ ] Task (TDD): `agri-service ops jobs-usage-report [--days N | --since D --until D] [--lane ID …] [--pool P] [--by pool|lane|host|day] [--format table|json]`
      (one registration in `interface/cli/ops.py`): a read-only transaction, `statement_timeout = 30s`,
      `LOCAL_SOURCE_LOADER_DATABASE_URL`, JSON on stdout, three sections fault-isolated in the style
      of `scripts/readiness.py` (spec §4.9.2).
- Tests (`tests/execution/test_usage_report.py`, `test_usage_sql.py`): `test_runs_in_a_read_only_transaction_with_statement_timeout`,
  `test_sections_are_fault_isolated`, `test_groups_by_day_pool_host_and_lane`,
  `test_repair_definitions_roll_up_to_the_owning_lane`, `test_pre_epoch_and_unspawned_attempts_are_never_charged`,
  `test_running_attempts_are_excluded`, `test_suspect_basis_is_separate_from_charged`,
  `test_lost_after_epoch_is_suspect_at_the_logical_cap`, `test_month_to_date_is_the_only_loader`,
  `test_json_on_stdout_logs_on_stderr`, `test_verb_is_registered_and_help_parses`,
  `test_usage_sql_headers_bind_params_and_one_statement_each`.
- Sweep proof: the `o4` suite, including the pre-epoch and unspawned rows.
- Review: `/code-review high` → `reviews.phase0_gl4`.
- [ ] **GL-4 (owner go): push.** Smoke read: row 4. [checkpoint marker]

### 0W.5 GL-5: `o2b-incidents` (sonnet) → `o5b-soft-failure` (opus)

- [ ] Task (TDD, `o2b`): `execution/lane_incidents.py` (upsert, resolve and select helpers inside a
      savepoint wrapper, `INCIDENT_SEVERITY`, `reconcile`, the repair-breaker ladder);
      `execution/lane_specs.py`, quarantine only (`ActivationConfig.quarantined`, `active_lanes`
      defined as parsed ids minus quarantined, the legacy incumbent kept on a conflict);
      `sql/execution/{upsert_lane_incident,resolve_lane_incident,select_lane_incidents,select_run_final_attempt}.sql`;
      `tests/test_job_executor_service.py::test_unknown_lane_is_rejected` rewritten for quarantine.
- [ ] Task (TDD, `o5b`), in `execution/job_executor_service.py`: quarantine wiring; the switch rules;
      savepoints on every Wave O statement; per-lane and repair-plan isolation; `tick_partial`; the
      incident lifecycles; the hold recorded and reconciled (release stays operator-only until G1);
      repair withholding and the repair breaker; the `executor_repair_authoring` incident; fleet
      correlation; `execution/AGENTS.md` "Soft failure". **No pool brake (WQ-4).**
- Tests:
  - `o2b`: `tests/execution/test_activation_quarantine.py` (revision 1's five plus
    `test_active_lanes_excludes_quarantined_through_plan_gap_repairs`, `test_conflict_keeps_the_legacy_incumbent`);
    `test_lane_incident_sql.py` (`test_upsert_never_moves_last_seen_backwards`,
    `test_resolve_renames_the_fingerprint`, `test_select_returns_open_and_recent_resolved_holds`,
    `test_final_attempt_query_returns_status_and_metrics`, `test_each_file_holds_one_statement`);
    `test_lane_incidents.py` (`test_reconcile_rules_table`, `test_repair_breaker_ladder_1_2_4_7_days`,
    `test_every_incident_severity_maps_to_event_severity`). The design's
    `test_pool_brake_membership_follows_key_presence` is dropped (WQ-4).
  - `o5b`: `tests/execution/test_executor_resilience.py` (`test_raising_incident_read_leaves_every_lane_dispatching_as_head`,
    `test_incident_write_failure_rolls_back_to_the_savepoint_only`, `test_dead_connection_keeps_the_tick_level_reraise`,
    `test_planning_fault_in_one_lane_leaves_the_other_running`, `test_repair_planning_fault_is_isolated_per_lane`,
    `test_executor_starts_with_every_malformed_variable`, `test_executor_starts_with_an_unknown_lane_in_the_allow_list`,
    `test_leader_fault_logs_partial_tick_before_reraise`, `test_escalation_survives_an_executor_restart`,
    `test_report_missing_streak_survives_restart`, `test_blocked_open_run_is_announced_and_recorded`,
    `test_lease_lost_three_times_opens_one_incident`, `test_announcements_fall_back_to_a_process_set_when_row_write_fails`,
    `test_fleet_correlation_logs_one_error`, `test_soft_failure_off_restores_head_planning`);
    `test_hold_record.py` (`test_hold_opens_with_class_from_the_final_attempt`,
    `test_operator_supersession_resolves_next_tick`, `test_enable_after_disable_returns_to_held`,
    `test_inactive_lane_hold_is_paused_not_escalated`, `test_orphan_hold_incident_is_reconciled`);
    `test_repair_withholding.py` (`test_held_lane_authors_no_repair_and_resumes_when_released`,
    `test_repair_breaker_withholds_after_two_code_failures`,
    `test_invalid_repair_request_does_not_trip_the_repair_breaker`,
    `test_repair_authoring_failure_opens_incident_and_escalates`; the two pool-brake tests are
    dropped, WQ-4); the `RepairAuthoringClock` pin in `test_self_healing.py`.
- Sweep proof: `tests/execution/test_soft_failure_fault_injection.py`, parametrised over a
  climate-wrapped 503 → `upstream`; the water-gauges pair → `upstream`; a drought wrapper with a
  failed last send → `upstream`; a wrapper after a 200 → `code`; `KeyError`; exits 75, 70 and 78; a
  hang; exit 0 without a report; **exit 0 with publication debt → `incomplete`** (PD); zero fetches;
  100,000 flood lines; self-SIGKILL (POSIX); a raising incident read. It asserts the class, the
  incident state, and that **a second lane completes unaffected**. Also
  `test_executor_starts_with_every_malformed_variable`.
- Review: `/code-review high` + `oh-my-claudecode:critic` prompted to refute every rule of spec
  §4.9.3 → `reviews.phase0_gl5`.
- [ ] **GL-5 (owner go): push.** 24 h observation: rows 6, 7, 10, 11. Phase-1 authoring may start
      after it. [checkpoint marker]

### 0W.6 Observation rows (bounded production reads)

| # | push | check | passes when |
|---|---|---|---|
| 1 | GL-3 | `@level:error` | only genuine failures and announcements; zero `*_complete` lines |
| 2 | GL-3 | `@event:plantgeo_job_executor_lane_turn` | exactly one per terminal outcome, with `turn_id`, `spawned` and `exit_class` |
| 3 | GL-1, GL-3 | secret probe (`apikey=`, `://*:*@`, `Bearer `, `Authorization`, `[SQL:`) | only `[redacted…]` |
| 4 | GL-4 | `jobs-usage-report --days 1` | a row per active lane. Soil per turn: `requests` ≤ 33, `http_requests` ≤ 1,584 (G0's committed bound), `weighted_calls_metered` ≥ `weighted_calls`. Month to date starts at the epoch with no pre-epoch charge. `suspect` lists only named kills or interruptions. |
| 5 | GL-3 | executor volume | about 30 tick lines a day or fewer |
| 6 | GL-2, GL-3, GL-5 | every active lane | settles as it did the day before |
| 7 | GL-5 (G1 form in Phase 2) | any hold | recorded and reconciled (GL-5); probe → probation → resolve, or the rung advancing on one fingerprint (G1) |
| 8 | GL-3 | `rss_peak_kib`, `start_lag_seconds` | recorded |
| 9 | GL-3 | `meter_errors` | 0 |
| 10 | GL-5 | repairs | none for a lane that is ledger-held |
| 11 | GL-5 | incidents | no open incident without a defined exit (paused and inactive included); a `lane_incomplete` with reason `publication_debt` is checked against the lane's pending claims (A26) |

### 0W.7 Efficiency and auditability queries

Run from a shell linked to the Aevani project, always bounded with `--since`/`--days` and `--lines`.
A filter string that starts with `-@` breaks the CLI.

Available today (HEAD):

```bash
railway logs --service plantgeo-job-executor --since 6h --lines 800 --json --filter "@event:plantgeo_job_executor_tick"
railway logs --service plantgeo-job-executor --since 24h --lines 400 --json --filter "@event:soil_forward_complete OR @event:climate_forward_complete"   # tagged level:error today; ignore the level
railway logs --service plantgeo-job-executor --since 24h --lines 400 --json --filter "@event:water_gauges_forward_complete"
railway logs --service plantgeo-job-executor --since 7d --lines 200 --filter "plantgeo_job_executor_operator_action_required"
```

```sql
SELECT d.name, a.started_at, a.status, a.failure_class,
       a.metrics->>'elapsed_seconds' AS elapsed_s, a.metrics->>'days_unwritten' AS days_unwritten,
       a.metrics->>'stderr_bytes' AS stderr_bytes
FROM agri.job_attempt a JOIN agri.job_work_item w ON w.id = a.job_work_item_id
JOIN agri.job_run r ON r.id = w.job_run_id JOIN agri.job_definition d ON d.id = r.job_definition_id
WHERE d.name LIKE 'plantgeo.executor.%' AND a.started_at > now() - interval '24 hours'
ORDER BY a.started_at DESC;
SELECT fingerprint, incident_type, status, summary, occurrence_count, last_seen_at FROM agri.job_incident ORDER BY last_seen_at DESC LIMIT 20;
```

- Answerable today: which lanes ran and failed, durations, what is held, and soil and climate
  logical requests (only while Railway keeps the logs).
- Not answerable today: sends, 429/5xx rates, bytes, backoff, month-to-date spend, silent exit-0
  lanes, repair spend while held.

After GL-1–GL-5 (and G1):

```bash
railway logs --service plantgeo-job-executor --since 24h --lines 2000 --json --filter "@level:error"
railway logs --service plantgeo-job-executor --since 24h --lines 2000 --json --filter "@level:warn AND @event:plantgeo_job_executor_lane_turn"
railway logs --service plantgeo-job-executor --since 24h --lines 2000 --json --filter "@event:plantgeo_job_executor_lane_turn AND @lane:soil-era5-land-direct-forward"
railway logs --service plantgeo-job-executor --since 24h --lines 1000 --json --filter "@event:plantgeo_source_usage AND @pool:open-meteo-paid"
railway logs --service plantgeo-job-executor --since 24h --lines 500  --json --filter "@event:plantgeo_source_request_failed AND @status:429"
railway logs --service plantgeo-job-executor --since 24h --lines 500  --json --filter "@turn_id:<uuid>"
railway logs --service plantgeo-job-executor --since 7d  --lines 300  --json --filter "@event:plantgeo_job_executor_hold_opened OR @event:plantgeo_job_executor_hold_probe OR @event:plantgeo_job_executor_hold_probation OR @event:plantgeo_job_executor_hold_released OR @event:plantgeo_job_executor_hold_reconciled OR @event:plantgeo_job_executor_hold_chronic OR @event:plantgeo_job_executor_hold_flapping"
railway logs --service plantgeo-job-executor --since 7d  --lines 200  --json --filter "@event:plantgeo_job_executor_budget_deferred OR @event:plantgeo_job_executor_repair_breaker_opened OR @event:plantgeo_job_executor_lane_plan_failed OR @event:plantgeo_job_executor_incident_write_failed"
railway logs --service plantgeo-job-executor --since 7d  --lines 200  --json --filter "@event:plantgeo_child_log_truncated OR @event:plantgeo_source_meter_error OR @event:plantgeo_job_executor_lane_report_missing OR @event:plantgeo_job_executor_config_fallback"
railway ssh --service plantgeo-job-executor "agri-service ops jobs-usage-report --days 1"
railway ssh --service plantgeo-job-executor "agri-service ops jobs-usage-report --since 2026-10-01 --by pool"
railway ssh --service plantgeo-job-executor "agri-service ops jobs-usage-report --since 2026-10-01 --by lane --pool open-meteo-paid --format json"
railway ssh --service plantgeo-job-executor "agri-service ops jobs-usage-report --days 7 --by host --lane water-gauges-direct-forward"
```

```sql
-- Open soft-failure state, one row per episode (restart-proof)
SELECT incident_type, fingerprint, status, severity, first_seen_at, cooldown_until, occurrence_count,
       detail->>'state' AS state, detail->>'rung' AS rung, detail->>'exit_class' AS exit_class,
       detail->>'chain_first_seen_at' AS chain_since
FROM agri.job_incident WHERE status <> 'resolved' ORDER BY first_seen_at;
```

Verify once that numeric comparisons on custom attributes (e.g. `@elapsed_seconds:>=600`) work
before relying on them. `budget_deferred` exists only from G1.

| question | answered by |
|---|---|
| Month-to-date paid Open-Meteo spend against the 60 % and 95 % lines; how much of it is suspect | report section 1 |
| Which lane (repairs included) drives spend, and its trend | `--by lane` / `--by day` |
| Are we abusing a source: sends per host, 429/5xx rates, User-Agent | `--by host`; `plantgeo_source_request_failed` |
| Retry amplification; throttled time versus working time | `http_requests/requests`; `backoff_seconds/elapsed_seconds` |
| What one turn spent | `@turn_id` joined to `metrics.turn_id` |
| What is held, why, at which rung, next probe, flapping | report section 3; the SQL above |
| Silent, blind, probe-gated and publication-debt lanes; incomplete usage | the section 2 flag columns |
| Head-of-line delay; G6 sizing | `start_lag_seconds`; `rss_peak_kib` |
| Manual spend | `plantgeo_source_usage` with `run_origin=operator` |

### 0W.8 Rollback

- **A switch** (spec §4.9.3: `PLANTGEO_JOB_EXECUTOR_SOFT_FAILURE`, `PLANTGEO_UPSTREAM_TELEMETRY`,
  `PLANTGEO_LOG_ROUTING`; from G1 `PLANTGEO_JOB_EXECUTOR_CODE_PROBE_HOURS`) set by the owner, or
  **a revert** (sweep, receipt, go). Incident rows already written stay as history.
- **Triggers:** any secret hit; a lost terminal report; a router regression; a probe storm;
  `meter_errors > 0`; any lane stuck in a state without a transition.
- [ ] Verification: each GL push has its sweep rows, its verdict (`reviews.phase0_gl1`–`phase0_gl5`)
      and its observation rows in `evidence/phase0.md` §GL-n; no Phase-1 authoring started before
      GL-5's observation closed. [checkpoint marker]

## Phase 1: Framework (dark) + legacy bridge

Goal: land the framework, with every production lane still on the legacy path, plus the legacy
fixes that protect production during the migration. Slices and file ownership are in
`metadata.json` → `partitions`. `f1-config` goes first (after G0 and Wave O's GL-5 push); the other
six then run in parallel.

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
- [ ] Task (TDD) **CA4 (cleanup-lane amendment, spec §4.8.9; a G1 blocker):**
      `infra/job-executor/Dockerfile` copies `lanes/` into **both** its quality-receipt stage and its
      runtime stage (`scripts/quality_receipt.py::digest_input_paths` silently skips a missing
      directory); the static COPY pin covers this Dockerfile too.
- [ ] Task (TDD) **Wave O (spec §4.9.2; WQ-4):** `lanes/_providers/open-meteo.toml` declares only the
      paid monthly budget (5,000,000; `ceiling_fraction = 0.60`, `stop_fraction = 0.95`) and its
      hosts; no windowed pools or reserves. Test: every host in every provider file maps to a
      provider and a pool label (`foundation/observability/usage.py::provider_for_host`).

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
- [ ] Task (TDD) **cleanup-lane amendments (spec §4.8.9):**
  - **CA20:** a config forward turn retries the lane's owed `availability/pending/` claims first
    (the legacy `_retry_owed_availability` contract), pinned by a conformance test.
  - **CA17:** a static_lookup `--republish-current` forward option: refetch, assert digest equality
    with the served snapshot, rewrite through the normal writer + availability generation + pointer
    path; refuse on mismatch. Each production use needs an owner go (CQ-8).
- [ ] Task (TDD) **Wave O (spec FR-38; USE-6):** `report.py` refuses to serialise without
      `unwritten`, prints from `finally` and carries no `level`; the S5 report adds
      `unwritten_known`, `fetch_attempts`, `http_*`, `bytes_in`, `backoff_seconds`, `rows_*`,
      `partitions_written`, `bytes_written`, `elapsed_seconds`, `phase_seconds_*`,
      `checkpoint_restores` and `log_lines_*` (`weighted_calls` is logical); `__main__.py` calls
      `configure_logging("service")`; ≤ 200 info lines per turn; `--weighted-budget`; no `print`.

### 1C. `f1-providers`: provider client

- [ ] Task (TDD): `ingest/provider_client.py` builds on `ingest/open_meteo_endpoint.py`'s
      free/customer pattern and accepts a single-location (bare object) body. A required empty key
      raises a named config error. `ingest/open_meteo.py` gains the customer forecast and
      historical-forecast hosts. Legacy callers stay byte-for-byte unchanged (pinned).
- [ ] Task (TDD): `ingest/http.py`: a status-aware retry helper; legacy `fetch_bounded_json_sized`
      is unchanged.
- [ ] Task **CA9 (cleanup-lane amendment):** `docs/env-vars.md` gains a paragraph for the second
      `OPEN_METEO_API_KEY` consumer (the config provider client). `git status` first: another
      session edits that file.
- [ ] Task (TDD) **Wave O (after `o3-ingest-meter`; spec §4.9.2):** the provider client builds on
      `upstream_client` / `upstream_sync_client` (metered since GL-2); SOFT-8, one retry ladder on
      `ingest/upstream_retry.py::retry_upstream` with a Retry-After clamp; `KeyedRequestUrl`, so a
      keyed URL never reaches a log or an exception string;
      `foundation/observability/usage.py::open_meteo_weight` is the one weight formula.

### 1D. `f1-executor`: work queue, cron, breaker, ledger, legacy fixes

`lane_incidents.py`, `exit_classes.py` and the provider-usage SQL already exist when this slice
launches (Wave O, GL-3–GL-5). **This slice extends that machinery; it never rebuilds it.**

- [ ] Task (TDD): `execution/cron_schedule.py` (S9).
- [ ] Task (TDD): `execution/lane_catalogue.py`. `tests/execution/test_lane_catalogue.py` pins the
      legacy id set literally and **derives** the config id set from `lanes/`, so a disabled TOML
      needs no test edit. Config wins. No id is on both paths.
- [ ] Task (TDD) **H6/FR-11:** the legacy allow-list half is delivered at GL-5 (`o2b-incidents`
      quarantines unknown and non-executable ids, spec §4.9.3). Here the kill-switch gets the same
      rule: it warns and opens an incident on unknown ids; it never exits.
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
  - **S16 switches:** `DISPATCH=queue|serial` and `BREAKER_MODE=split|legacy` (`legacy` subsumes
    Wave O's `SOFT_FAILURE=off`).
- [ ] Task (TDD) breaker split, **extending GL-5's incident-row hold**, in `lane_scheduling.py` +
      `sql/execution/select_latest_run.sql` + `execution/lane_incidents.py` +
      `execution/exit_classes.py` (native 75/70/78 into `classify_exit`):
  - exit 75, and a legacy non-zero exit with R1–R4 upstream or infra evidence (WQ-2), get
    single-attempt half-open probes and auto-release;
  - code class gets a hold + **one `agri.job_incident` row per episode, no webhook or email** (O4),
    re-attempted on the `CODE_PROBE_HOURS` ladder (WQ-1; next task);
  - the post-supersession streak is fixed (F4, marker-aware);
  - no per-tick repeated line.
- [ ] Task (TDD) **Wave O at G1, GL-6 folded here (spec §4.9.3; WQ-1, WQ-2, WQ-3):**
  - `execution/lane_incidents.py`: the ladders (upstream and infra 1, 2, 4, 8, 16, 24 h, then
    daily; code, hang and config `PLANTGEO_JOB_EXECUTOR_CODE_PROBE_HOURS`, default `6,12,24`, then
    daily; empty or unparseable = operator-only), the probe schedule, `PROGRESS_EVIDENCE`,
    probation, watch, chain and flapping rules.
  - `execution/job_executor_service.py`: probes through the `_release_by_process_start` pattern
    inside a savepoint (append the probe with `superseded_run_id`, `supersede_failed_run(operator="executor:probe")`,
    commit, then `_open_scheduled_run(…, max_attempts=1)`); a probe fires only when
    `verdict.newer_bucket_exists`; the planner's probe flag threaded through `_execute_due_lane` into
    the handler closure and the child environment (`PLANTGEO_TURN_PROBE=1`); `fence_lost` on a probe
    item parks (inconclusive); inconclusive handling (same rung; the fourth counts as failed,
    `lost_repeatedly`); probation (2 conclusive clean buckets, or 48 h without a failed bucket →
    `probation_expired` + `lane_incomplete` reason `inconclusive`); the 24 h watch
    (`max_attempts=2`, 1 when flapping); 7-day chaining; `hold_flapping` at 3 episodes. **No deploy
    probe (WQ-7).** `execution/AGENTS.md` "Holds and probes".
  - `jobs/worker.py::open_job_run` gains a `max_attempts` keyword.
  - Tests: `tests/execution/test_hold_ladder.py` (`test_upstream_and_infra_use_the_upstream_ladder`,
    `test_code_probe_hours_empty_is_operator_only`, `test_probation_resolves_after_two_conclusive_or_48h`,
    `test_watch_attempts_drop_to_one_when_flapping`); `tests/execution/test_hold_probes.py`
    (`test_probe_work_item_has_one_attempt`, `test_failed_probe_advances_the_rung_on_one_fingerprint`,
    `test_lost_probe_does_not_advance_the_rung_or_flip_the_ladder`, `test_fence_lost_on_a_probe_parks`,
    `test_three_inconclusive_probes_count_as_failed`, `test_daily_lane_never_probes_more_often_than_its_cadence`,
    `test_inconclusive_probation_expiry_opens_lane_incomplete`, `test_probation_is_excluded_from_chronic`,
    `test_watch_window_opens_two_attempt_buckets`, `test_reopen_within_7_days_inherits_rung_and_chain`,
    `test_three_episodes_in_7_days_log_hold_flapping_once`, `test_operator_supersession_is_told_apart_from_a_probe`,
    `test_deploy_probe_is_off_by_default_and_folds_when_on`); probe cases (single attempt,
    inconclusive, probation) added to `tests/execution/test_soft_failure_fault_injection.py`.
- [ ] Task (TDD) **Wave O at G1, budget, receipt, retention (spec §4.9.2; WQ-4, WQ-6):**
  - `execution/provider_budget.py` enforces **only the paid Open-Meteo monthly cap**: gap-fill is
    admitted while charged + suspect + the turn cap ≤ 0.60 × 5,000,000; forward stops only when
    charged ≥ 0.95 × 5,000,000; suspect > 10 % of charged opens `budget_basis_suspect` and refuses
    gap-fill. It imports `execution/usage_report.py::month_to_date` (never loads the SQL again),
    runs per lane inside planning **before `fair_due_order`** (a `deferred_budget` result; no run
    opened; never `JobHandlerOutcome.deferred`), maps legacy soil and its repair to
    `open-meteo-paid` only while the key is set, and takes the provider try-lock on
    `db/engine.py::executor_lane_pool`. `CHARGE_BASIS` comes from P5 (default `metered`).
  - `execution/usage_receipt.py` writes `receipts/source-usage/<YYYY-MM>.json` to the existing
    object store after each UTC month closes.
  - `job-logs-maintain` runs once per UTC day in the repair-authoring slot; S16 dual read; `jobs.ts`
    shows incidents, acknowledgement and usage (normalised parity with the Python SQL).
  - Tests: `test_refused_lanes_never_take_a_selection_slot` (two refused lanes and one healthy lane
    at `max_lanes_per_tick=2`: the healthy lane runs every tick), `test_suspect_charges_never_stop_forward`,
    `test_admission_imports_month_to_date`, and, new at integration,
    `test_gap_fill_is_refused_at_sixty_percent_and_forward_at_ninety_five_percent` (WQ-4) and
    `test_monthly_usage_receipt_is_written_once_per_closed_month` (WQ-6). The design's
    `test_windowed_pool_reserve_protects_the_realtime_lane` is dropped (WQ-4).
- [ ] Task (TDD): complete printed commands (parsed in the test); `sql/jobs/refresh_job_run_rollup.sql`
      writes `last_error_summary`; provider usage is read through `o4-usage-report`'s
      `execution/usage_report.py::month_to_date` (its SQL is never loaded twice).
- [ ] Task (TDD) **FR-10:** `gap_repair.py::author_gap_repairs` persists every candidate (≥2
      candidates, a rollback-honouring fake). **NEW-1:** re-base `tests/execution/test_gap_repair.py`'s
      `SHORTWAVE` fixtures on `drought` and `vegetation` (both keep their bindings at G1), and expect
      `no_repair_binding` for soil streams.
- [ ] Task: `_unwritten_entries` reads the legacy keys and S5 (pinned both ways).
      `src/lib/server/trpc/routers/jobs.ts` shows cron, next fire, open incidents, failure text and
      provider usage. `execution/AGENTS.md` drift fixes (executor F10).
- [ ] Task (TDD) **cleanup-lane amendments (spec §4.8.9; RC-8 re-checks them at 7R):**
  - **CA1:** a ledger-visible config-path marker on every work item a config lane dispatches.
  - **CA2:** brake (`execution/job_lane_control.py::resolve_definition`) and supersede resolve
    through the catalogue, including `<lane>:gap-fill`; config-lane definitions register only
    through `sql/execution/insert_definition.sql` (`ON CONFLICT DO NOTHING`) with
    `read_lane_pause_state` honoured, never `jobs/worker.py::ensure_job_definition`. Test: a braked
    config lane and its `:gap-fill` are not dispatched on the next tick
    (`tests/test_job_lane_control.py`, `tests/test_job_run_supersession.py`).
  - **CA3:** config dispatch and supersede gate only on TOML `enabled` + catalogue + CA12; the test
    removes the lane's `LaneExecutionSpec` **and** its `ACTIVE_LANES` token.
  - **CA8:** legacy repair **authoring and driving** (`_plan_repair_runs` and the repair-kind path
    of `run_scheduled_command`) skip `executor = "config"` lanes; tested with an open `:gap-repair`
    run on a config lane.
  - **CA12:** one named, tested env kill-switch distinct from `ACTIVE_LANES`.
  - **CA13:** the config path keeps the definition name and `EXECUTOR_DEFINITION_VERSION` (no
    version bump; if one is unavoidable, the G6 go carries an explicit lane-wide resume).

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
      needed. `app.py`'s logging configure block moved to `o1-logging-core` at GL-1; this slice
      edits DI only.

### 1Z. Gate

- [ ] Task: The monitor sweep (Python + web) goes into `evidence/phase1.md`. Run `/code-review high`
      and `/security-review`, and record the verdicts in `reviews.phase1`.
- [ ] Verification: sweep green by gate rows; verdicts recorded; the legacy behaviour changes are
      only FR-8 to FR-11, FR-21 and Wave O's G1 half (FR-36, FR-37, the FR-34 receipt), each listed
      in the table. [checkpoint marker]

## Phase 2: Two review/fix loops, contract re-freeze, G1

Goal: the framework survives two refutation attempts, and the contract the swarm codes against is
frozen from the code that actually landed (review M1). One author lane runs the fix batches
serially, within `f1-*` files.

- [ ] Task: **R1.** `oh-my-claudecode:code-reviewer` (opus): "refute that this runner carries soil,
      water gauges, drought, evacuation-zones and the precedence transform without a per-lane
      branch". Fix batch, then monitor sweep.
- [ ] Task: **R2.** `oh-my-claudecode:critic` (opus): concurrency (queue, sessions, leader loss),
      flips in both directions, budget under 429 re-asks, the probe gate, transform dirty days, S11,
      and (Wave O) **refute that a failure stops a lane permanently, reaches another lane, or is
      masked**, including the folded hold ladder and the paid-cap admission.
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
  - the **single-lane peak RSS** is recorded (it sizes G6's concurrency decision);
  - Wave O at G1: any hold is probed on its ladder (row 7 in its G1 form: probe → probation →
    resolve, or the rung advancing on one fingerprint); `jobs-usage-report` section 1 shows the paid
    pool against the 60 % and 95 % lines.
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
  - `docs/lanes/water-gauges.md`;
  - **cleanup-lane amendments (spec §4.8.9):** **CA5** `src/lib/map/layer-registry.ts::PLATFORM_LAYERS["water-gauges"].warehouseLayerName`
    goes through the stream constant; **CA11 (water)** the water-gauges value of
    `agent/surfaces.py::SURFACE_PARQUET_LANES` (`git status` first: another session edits `agent/**`).
- [ ] Task: One adversarial reviewer (fresh context) refutes day semantics, per-tile independence,
      the digest-rewrite of approval changes, history depth, the registration, and whether any
      reader joins the calendar for pre-2000 days (A19). Fix batch, monitor sweep, and the verdict
      in `reviews.phase3`.
- [x] Task: **G2 (owner):** set `USGS_WATER_DATA_API_KEY` if P4 requires it.
      **G3 (owner go):** a commit enabling `water-gauges-daily` and its gap-fill, with sweep and
      receipt refresh, then push at a quiescent point. The legacy IV lane keeps serving. The
      calendar floor moves to 1990-09-30; its next version carries the earlier days (plus a one-off
      calendar export under this go only if A19's check found a reader that needs them).
      **Executed October 2, 2026:** no key was required by the successful source probes;
      owner-authorized G3 commit `696f1ae5` was deployed on all four expected services. Both modern
      water schedules are active and legacy serving remains unchanged. The bounded manual
      forward validation exposed a stream-subtype selection defect. Follow-up `211fc101`
      repaired it in production: fourteen days have complete source support and all physical
      rungs, fifty gauge/day comparisons pass, and the five-day forward/historical overlap
      agrees. The initial modern-stream availability index is published and verified.
      Historical completeness, scheduled observation and all other open G4 rows remain in
      `evidence/phase3.md`; no serving switch is implied.
- [ ] Task: **Validation rows** (there is no row compare across APIs):
  - 10 gauges × 5 days against the modern daily values, and against legacy `dv` while it exists;
  - forward/gap-fill equality on overlapping days;
  - the named-day check;
  - **history depth: the census holds no owed day in [1990-09-30 (or the A14-lowered floor), edge].**
- [ ] Task: **QA rows:** ≥72 h of forward turns with exit 0 and a correct S5 report; the re-pull
      progressing within budget.
- [ ] Task: **G4 (owner go), only when every validation row passes, history depth included:** flip
      the stream-name constant and the attribution (sweep + receipt + push), then pause the legacy
      `water-gauges-direct-forward` through the ledger verb. **CA18 (go text):** pause the legacy
      lane only after a legacy turn whose report shows its owed-claim retry drained; otherwise record
      the open claims under `layer=water-gauges/` for 7R recovery. Rollback: re-point and resume the
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
  - **CA14 (cleanup-lane amendment):** every shim created is listed in
    `services/agri-data-service/DEPRECATED_ALIASES.md`, removal condition "deleted at 7D-n of the
    supersession cleanup lane".
  - **Wave O:** `tests/test_no_raw_http_clients.py` re-points the crop-cover allow-list entry to
    the moved `pipeline/lanes/crop_cover/source.py` (never adds an entry).
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
  - **CA7 (`s-burn-severity`, cleanup-lane amendment):** implement the Friday
    capture/current_snapshot/stage successor inside `pipeline/lanes/burn_severity/**` and return a
    row naming its path; if the frozen contract cannot carry it, return a "no successor" row that
    `i-coordinator` routes to the owner (CQ-9) before G6. No receipt or descriptor change.
  - **Wave O conformance (spec FR-38; tripwire wave-o-10):** no `print`; logs through
    `foundation/observability`; fetches only through the provider client; no new raw-client
    allow-list entry. `s-crop-cover` moves crop-cover's two raw `httpx` clients to the provider
    client and removes its allow-list entry.
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
  5. **Cleanup-lane go text (spec §4.8.9):** **CA7:** when CA7 named a capture successor and the
     census shows `mtbs-forward` enabled, brake `plantgeo.executor.mtbs-forward`. **CA19:** one
     applied brake drill after the flip on watersheds and `watersheds:gap-fill` (brake, one tick
     with no dispatch, resume). Record open `:gap-repair` runs read-only; they are drained at
     G9V-1a, never disabled here (spec §18.4 SAF2-05).
- [ ] Task: **G7 (owner go; ≈1.75M Open-Meteo calls + POWER requests):** one commit flipping
      `gap_fill_enabled` on `meteorology-era5-settled` (the five-layer re-pull to 1984,
      newest-first, 8,000/turn), `shortwave-nasa-power-settled` (the mandatory re-grid) and
      `meteorology-ifs-provisional` (the historical-forecast bootstrap). If Phase 0 recorded a split,
      the re-pull's `gap_fill_max_weighted_calls` is lowered so it spans two months. Sweep + receipt,
      then push. Watch provider month-to-date daily.
- [ ] Verification: each flipped lane's first config turn has exit 0 and an S5 report; the re-pull is
      within budget. [checkpoint marker]

### Rollback (from G0/G3/G6 until that lane's 7Q-n push)

After 7Q-n, follow spec §4.8.6 rule 3 (revert 7Q-n before any executor flip); after that lane's
G9D-n go, fix forward. This section is marked HISTORICAL at G9S (spec CA16).

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
      the legacy climate streams stay readable. **Cleanup-lane amendments (spec §4.8.9): CA6** the
      soil-wetness entries of `layer-registry.ts::CLIMATE_SIGNAL_ICONS`,
      `src/__tests__/lib/cache/sync-index.test.ts` and
      `src/__tests__/services/environmental-metric-dispatch.test.ts`; **CA11 (climate)** the climate
      and soil-wetness values of `agent/surfaces.py::SURFACE_PARQUET_LANES`,
      `agent/selection_scope.py::support_lattice` (its `climate-field-` prefix rule re-pointed or
      confirmed for `meteorology-*`), `foundation/region/layer_availability.py` and the
      `tests/test_agent_parquet_tools.py` catalogue rows (`git status` first), plus every other
      soil-wetness or climate-field hit of a fresh bare-token `src/` grep. Web + Python sweep, push,
      then a browser check.
- [ ] Task: A failing lane rolls back, gets one fix batch and one sweep, and re-enters QA.
- [ ] Verification: both rows for every flipped lane; G8 done and browser-checked. [checkpoint marker]

## Phase 7: Supersession cleanup lane

Owner request 2026-09-26 (verbatim): "this lane should include a dedicated clean up lane for when
we are verified ready to supersede". Spec §4.8 defines **verified ready to supersede** (READY(L) =
RC-1–RC-8, the RETIRE-SAFE rows, WHOLE-PATH-READY), the four verbs, the amendments CA1–CA20 and the
coverage of the 81-item inventory; spec §18 records the request, the design rounds and the owner
questions CQ-1–CQ-12. Design record: `.omc/research/ingestion-grill-20260926/cleanup-lane-design.md`
(revision 2). Slices and file ownership: `metadata.json` → `partitions` (`c7-*`); tripwires c7-1–c7-16.
Wave O's legacy parts retire through these slices as listed in spec §4.9.5.

- **Order matters (review N8), both ways (tripwire c7-3).** The repair subsystem leaves the shared
  executor files first (7Q-1a); then a cohort's specs and ids leave them (7Q-n); only after the 7O-n
  observation do the cohort's modules go (7D-n).
- **The lane deletes no data** (c7-1): ledger names are soft-retired, never deleted; every purge is
  G9X, never scheduled.
- **Keep-list (owner decisions; c7-10, c7-12, spec §4.8.7):** the legacy `climate-field-*`,
  `soil-wetness-*` and `water-gauges` schema modules and history stay readable; the shadow lanes'
  legacy modules stay; soil wetness stays retired.
- **Production.** The owner runs every variable change. A ledger dry run or `--apply` is run by the
  owner, or by `i-coordinator` under a go that quotes the pinned command line (§7.5) and attaches the
  rolled-back dry-run output and its digest. `c7-*` sub-agents never touch production (c7-13).
- **Task ids below are local to their slice:** V = `c7-verbs`, R = `c7-readiness`, P =
  `i-coordinator` production rows, B = `c7-docs-banners`, RR = `c7-repair-retire`, Q =
  `c7-quarantine-1`, D = `c7-delete-1`, S = `c7-scaffold`, F = `c7-docs-final`, C = `i-coordinator`
  closure. They are unrelated to spec decisions S1–S19, probes P1–P5, owner decisions D1–D10 and
  flags F1–F7.
- Authors run no tests, lint or build; each returns grep output and predicted failures as rows.

### 7.0 Flow

```
G8 --> c7-verbs --------------------------------------------- G9.0 push
        '-> c7-readiness (grep) + i-coordinator T0 census (no prefix), claim census,
            branch/worktree census, orphan and caller censuses -- critic -- CQ-6 answered -- 7R
              |- G9V-1a  owner: REPAIR_INTERVAL=0 (permanent); applied brake drill; drain six :gap-repair
              |    7Q-1a  c7-repair-retire || c7-docs-banners -- sweep -- critic + /code-review high -- G9Q-1a push
              |- G9V-1b  owner: cohort-1 tokens out -- soak (one fire of every cohort lane and <L>:gap-fill)
              |    brake/drain water + mtbs-forward -- receipts after pending prefix empty
              |    7Q-1b  c7-quarantine-1 -- sweep -- critic + /code-review high -- G9Q-1b push
              |- 7O-1    max(3 d, one fire of every cohort-1 lane), zero triggers, daily census
              |- 7D-1    c7-delete-1 -- sweep -- /code-review high + /simplify + critic -- G9D-1 push -- G9R-1
              |- G9Q-2   brake climate (RC-6 green) -- G9V-2 -- soak -- claims empty -- drain -- receipts
              |    7Q-2  c7-quarantine-2 -- G9Q-2 push -- 7O-2
              |- 7D-2    c7-delete-2 -- G9D-2 push -- G9R-2
              '- 7S      c7-scaffold -> c7-docs-final -- one Python + web sweep -- G9S push -- T5 census (+7 d)
```

- **Pushes: 7** (G9.0, G9Q-1a, G9Q-1b, G9D-1, G9Q-2, G9D-2, G9S), each with one monitor sweep.
- **A 7D modified-files diff over 500 lines splits by lane group into two pushes;** the deletion
  manifest is reviewed separately (push-small).
- **Cohort 1:** the 10 class-A lanes, water gauges (only with the CQ-6 acknowledgement),
  `mtbs-forward` and the CQ-10 names. Static_lookup lanes without a config-path write (spec §4.8.3),
  and burn-severity under a CQ-9 carve-out, move to a later cohort. **Cohort 2:** climate.
- **Any non-default cohort cut at 7R is a partition revision.**

### 7.1 Slices

| slice | model | depends_on | lands in | relation to the former Phase 7 |
|---|---|---|---|---|
| `c7-verbs` | sonnet | `p6-climate-rebind` | G9.0 | new |
| `c7-readiness` | sonnet (grep-only; owns nothing) | `c7-verbs` | 7R rows | new |
| `c7-docs-banners` | haiku | `c7-readiness` | G9Q-1a (launched with `c7-repair-retire`; `i-coordinator` enforces) | new |
| `c7-repair-retire` | sonnet (opus critic) | `c7-readiness` | G9Q-1a | the repair half of `d7-legacy-shared` |
| `c7-quarantine-1` | sonnet (opus critic) | `c7-repair-retire` | G9Q-1b | the rest of `d7-legacy-shared` |
| `c7-delete-1` | sonnet | `c7-quarantine-1` | G9D-1 | `d7-legacy-lane-modules` (cohort 1) |
| `c7-quarantine-2` | sonnet | `c7-delete-1` | G9Q-2 | cohort 2 |
| `c7-delete-2` | sonnet | `c7-quarantine-2` | G9D-2 | cohort 2 |
| `c7-scaffold` | sonnet | `c7-delete-2` | G9S | new |
| `c7-docs-final` | sonnet | `c7-scaffold`, `c7-docs-banners` | G9S | absorbs the former Phase-7 closure (docs) |
| `i-coordinator` | opus | — | every gate | production rows, census, daily 7O re-check, claim recovery, memory, RUNBOOK, metadata, `tracks.md` |

### 7.2 Tasks

#### `c7-verbs` (G9.0; its own quiescent push after the G8 push)

- [ ] V1: `agri-service ops jobs-lane-census` (spec §4.8.8), with `--all`, the classification,
      `live_dispatch_reasons` and `unaudited_enabled_changes`.
- [ ] V2: `agri-service ops jobs-drain-definition` (spec §4.8.8).
- [ ] V3: `agri-service ops jobs-retire-definition` with `--restore` (spec §4.8.8).
- [ ] V4: `agri-service data parquet prefix-census` (spec §4.8.8), with the top-level listing and
      the `open-claim` class; `receipts/` (Wave O's monthly source-usage receipt, spec §4.9.2) is on
      the pinned known-infra list.
- [ ] V5 (TDD) tests:
  - the census is read-only (a static grep of its SQL);
  - the liveness predicate refuses on each reason, and **accepts a braked, token-removed definition
    whose spec still exists** and a **`:gap-repair` name while `REPAIR_BINDINGS` still holds its
    owner** (owner on `executor = "config"`, `REPAIR_INTERVAL` "0");
  - the drain cancels an **expired-lease** item and closes its attempt;
  - drained runs end with **`status = 'cancelled'`** and consistent counters;
  - a dry run leaves no row changed; `--apply` refuses a stale digest;
  - `--restore` reproduces the per-version state;
  - the incident-match rule is covered;
  - `prefix-census` makes no write call;
  - **`tests/interface/test_cleanup_command_lines.py` parses every §7.5 command line through
    CliRunner `--help`**;
  - the DB-gated `tests/test_definition_retirement_agri_db.py` (skipped by the sweep; it complements,
    never replaces, the production rolled-back dry run).
- [ ] V6: registrations and AGENTS.md entries. Command bodies live in `execution/lane_census.py`,
      `execution/definition_retirement.py` and `parquet_ops/prefix_census.py`. The CLI files only
      decorate a thin delegate and open no transaction (`CLI_ADAPTER_VIOLATIONS` unchanged). Each
      SQL file is loaded exactly once (`tests/test_sql_tree_conventions.py` rule d).

#### `c7-readiness` (7R; grep-only; writes nothing and touches no production)

- [ ] R1: re-verify every `c7-*` owns list at HEAD and re-run the owns-overlap proof over the whole
      slice set; produce the concrete `c7-docs-final` comment list minus every `c7-scaffold` path.
- [ ] R2: grep rows G-1, G-1b, G-7 (bare-token input rows), G-8, G-9, G-10, G-12, G-14 (§7.3); the
      cross-track grep; `ListAgents`.
- [ ] R3: test assignment. Repair-only cases go to `c7-repair-retire`; spec and fixture cases go to
      `c7-quarantine-n`; a file with both is a sequenced co-owner.
- [ ] R4: the CA12 kill-switch variable name, and the `f1-executor` breaker-hold fingerprint pattern
      for the incident-match rule (spec §4.8.8).
- [ ] R5 **orphan census:** build an import graph (AST, with a grep fallback) over the service's
      `src/agri_data_service/**`, `tests/**` and `scripts/**`. Record every module and top-level
      symbol whose importers all sit inside a cohort deletion set, and every env variable reachable
      only from those. Each hit gets a DEL or KEEP row; `c7-delete-1`'s CONDITIONAL owns become DEL
      only on a hit; new hits are a partition revision. **Re-run after each 7D.**
- [ ] R6 **branch and worktree census:** `git worktree list`, plus
      `git diff --name-only $(git merge-base main B) B` for each unmerged branch and each `archive/*`
      tag left by the 2026-09-27 worktree triage, checked against each 7Q-n and 7D-n path set. The
      unregistered `.claude/worktrees/hotfix-retention-order` gets a directory diff.
- [ ] R7 **launcher caller census:** local task `.Actions`; `rg` over the repo (docs, sql,
      AGENTS.md, skills, README); the memory directory. Terms:
      `durable-archive-backfill|fill-firms-gap|firms-archive-full|ingest-backfill`.

#### `i-coordinator` production rows (7R, and daily during each 7O)

- [ ] P1: the T0 census with **`--all`**: every `job_definition` name classified live-config,
      shadow, legacy-path-to-retire, unrelated or unknown. Record the exact `:gap-repair` names,
      per-version digests, and the `ACTIVE_LANES` and `REPAIR_INTERVAL` values.
- [ ] P2: RC-1–RC-5 rows, RETIRE-SAFE rows and terminal-day receipts. Record whether coverage owes
      days after a terminal day.
- [ ] P3: verify-only rows: R-08, R-09, R-11, R-14; service watch paths and config paths; local
      tasks; `to_regclass('geo.features')`; the 1,584 soil bound (CA15).
- [ ] P4: the cohort cut, with a reason row for each exclusion. **CQ-6 is answered before the cut.**
- [ ] P5: critic on the dossier, the CQ-10 classification and the R5 orphan rows
      (`reviews.phase7_readiness`).
- [ ] P6 **claim recovery:** for each frozen or cohort root with an open claim, run
      `agri-service data availability-reconcile-physical --lane <slug> --kind observed --start <d> --end <d>`
      as a dry run, then `--apply --expected-sha256 <sha> --expected-head-generation <key>` under a go.
- [ ] P7: before G9S, export each CQ-7 task's XML to `ops-archive/local-tasks/` in the object store.

#### `c7-docs-banners` (7Q-1a)

- [ ] B1: superseded banners only (no body rewrite) on `docs/layer-lane-standard.md`,
      `.claude/skills/agri-pipelines/SKILL.md`, `docs/runbooks/durable-backfill-lanes.md`,
      `docs/runbooks/usgs-sentinel-cleanup.md` ("superseded, never executed"),
      `conductor/code_styleguides/python.md` (its "Source-direct writer packages" rule: "superseded
      by the TOML/strategy/runner model (spec S14); rewritten at 7S") and
      `conductor/code_styleguides/federation.md`.

#### `c7-repair-retire` (7Q-1a; after the dossier critic verdict, G9V-1a and the drain of the six `:gap-repair` names)

- [ ] RR1: confirm from the post-G9V-1a census row that the six `:gap-repair` names are drained and
      `REPAIR_INTERVAL` reads "0". A `REPAIR_BINDINGS` lane rolled back to `executor = "legacy"`
      blocks this slice (partition revision).
- [ ] RR2: delete the legacy repair subsystem: `execution/gap_repair.py`,
      `execution/gap_repair_contract.py`, the `jobs-plan-gap-repair` registration, and in
      `execution/job_executor_service.py` `RepairAuthoringClock`, `_author_due_repairs`,
      `_plan_repair_runs`, `repair_lane_spec`, `REPAIR_INTERVAL_VARIABLE` and the repair work-item
      kind; `tests/execution/test_gap_repair.py`, `tests/execution/test_legacy_repair_exclusions.py`
      and the repair cases of the four executor tests. With them go Wave O's repair withholding,
      repair breaker and `executor_repair_authoring` wiring, the repair rules of
      `execution/lane_incidents.py` (and their `test_lane_incidents.py` cases) and
      `tests/execution/test_repair_withholding.py` (spec §4.9.5).
- [ ] RR3: drop the `repair_driven` branch of the liveness predicate.
- [ ] RR4: AGENTS.md repair passages (`execution/`, `interface/cli/`, `parquet_ops/`).
- [ ] RR5: G-3 must be zero after this push (except the env-vars "held at 0" row).

#### `c7-quarantine-1` (7Q-1b; after the G9Q-1a push, G9V-1b, the soak and the drain of water + `mtbs-forward`)

- [ ] Q1 (TDD): `tests/execution/test_config_dispatch_without_legacy_spec.py`: spec removed, token
      removed, **plus a config-marked in-flight item that still dispatches under the post-7Q
      `LANE_SPECS`**. Stop and escalate if the config path reads a legacy spec or token.
- [ ] Q2: confirm the RC-5(c) per-name rows.
- [ ] Q3: `execution/lane_specs.py`: delete the cohort `_spec` entries and imports; re-point
      `VEGETATION_DIRECT_WRITER_START_DAY` to its `p4-extract` home (never delete it); fix the
      promotion-lag docstring; keep ndvi-promotion and `_REFERENCE_DATA_SPECS`.
- [ ] Q4: `execution/lane_ids.py` cohort constants and their `__all__` entries.
- [ ] Q5: add the CQ-10 names to `RETIRABLE_DEFINITIONS` (after critic review).
- [ ] Q6: `pipeline/parquet/lane_registry.py` import verification and the stale `mtbs-forward`
      comment; never `*_basis` strings, never `forecast_module`.
- [ ] Q7: the `executor = "legacy"` guard, and the catalogue pin (`execution/lane_catalogue.py`).
- [ ] Q8: rebase fixtures onto `tests/execution/lane_fixtures.py`, including
      `tests/test_job_lane_control.py` and `tests/test_job_run_supersession.py` (fixtures only, never
      the CA2 behaviour); delete or rebase every spec-lookup test and every test that borrows a
      cohort spec as a fixture; re-point shim-path test imports.
- [ ] Q9: shrink the pins (`test_lane_cadence.py`, `test_lane_catalogue.py`, `test_lane_contract.py`,
      `test_job_executor_service.py`).
- [ ] Q10: conditional `retired_through` and `tests/parquet/test_retired_stream_marker.py` for
      water gauges, only if P2 shows owed days after the terminal day.
- [ ] Q11: AGENTS.md files.
- [ ] Q12: record the G-1 expectation after 7Q (hits only inside the 7D-1 set).
- [ ] Q13 (Wave O, spec §4.9.5): the cohort-1 legacy rows: `WRAPPER_EVIDENCE` and the R2 lane rows
      in `execution/exit_classes.py` and their `tests/execution/test_exit_classes.py` cases, the
      cohort-1 `PROGRESS_EVIDENCE` entries in `execution/lane_incidents.py`, and the soil legacy map
      in `execution/provider_budget.py`.

#### `c7-delete-1` (7D-1; after 7O-1 and a G9D-1 go that names the lanes)

- [ ] D1: G-1 and G-1b before; the hits must equal the deletion set plus the allow-list.
- [ ] D2: delete the cohort packages under `pipeline/direct/**`, their shims and their tests (by
      explicit name where globs miss: `tests/direct/test_vegetation_{rows,source,support}.py`,
      `test_mtbs_current_snapshot.py`, `test_mtbs_staging.py`); the wfigs fixture only if unused.
- [ ] D3: the water chain (`pipeline/direct/water_gauges.py`, `pipeline/parquet/water_gauges_forward.py`,
      `pipeline/validation/water_gauges.py`, `ingest/usgs_nwis.py`,
      `ingest/identity.py::build_streamflow_gauge_identity` and their tests), only with CQ-6
      acknowledged at 7R.
- [ ] D4: scripts: **re-point `scripts/build_era5_land_from_canonical_snapshot.py` and KEEP it**;
      the mtbs scripts per CA7 or CQ-9; the sensor correction per its RETIRE-SAFE row;
      `scripts/AGENTS.md`.
- [ ] D5: contract tests, and the two filesystem-pin edits under critic review
      (`tests/test_layer_import_contract.py`, one pinned scoped edit;
      `tests/foundation/test_geography_bounding_box.py`, the 7 deleted-path parametrisations).
- [ ] D6: `scripts/check.py::DIRECT_PACKAGES` (the harness exception: one inline
      `tests/scripts/test_check_batches.py` run).
- [ ] D7: the `ingest/firms.py` `pipeline.direct` refusal string and the `lane_registry.py`
      refusal-adapter text; served `*_basis` strings stay.
- [ ] D8: AGENTS.md cohort sections; `DEPRECATED_ALIASES.md` cohort shim rows move under a Deleted
      heading.
- [ ] D9: `docs/env-vars.md` rows and the root `.env.example` line, each after a
      **reachable-reader** proof (R5); `git status` first.
- [ ] D10: the CONDITIONAL orphan deletions (`ingest/wfigs.py`, `ingest/mtbs.py`, the
      `ingest/policy.py` and `ingest/sensors.py` symbols and their tests), only on R5 hits.
- [ ] D11: grep again (the result must be zero), and re-run R5.

#### `c7-quarantine-2` and `c7-delete-2` (cohort 2, climate)

- [ ] `c7-quarantine-2` (7Q-2; after climate RC-3, RC-6 green, the G9Q-2 brake, G9V-2, the soak and
      zero open claims under the 11 frozen roots): the climate equivalents of Q1–Q12, plus the lag
      pin (B-07); the climate halves of the shim-path and spec-lookup tests; conditional
      `retired_through` for `climate-field-*` and `soil-wetness-*`; Wave O's climate
      `PROGRESS_EVIDENCE` entry and climate fixture cases (spec §4.9.5).
- [ ] `c7-delete-2` (7D-2; after 7O-2 and a G9D-2 go): delete `pipeline/direct/climate/**` (with its
      shims) and `tests/direct/climate/**`; **re-point `scripts/build_nasa_power_from_canonical_snapshot.py`
      and KEEP it**; the D5–D9 equivalents for climate.

#### `c7-scaffold` (7S)

- [ ] S1: delete `pipeline/parquet/source_checkpoint.py` and its test after a zero-importer grep;
      the `source-response-checkpoints/v1/` objects stay.
- [ ] S2: the S16 switches per CQ-3; confirm zero readers of `MAX_LANES_PER_TICK` before its G9S
      unset.
- [ ] S3: the `executor` arm, field and allow-list parse per CQ-2 (default KEEP).
- [ ] S4: `.env.example` residue.
- [ ] S5 **dispositions table** in `lanes/AGENTS.md`:

  | artifact | disposition | basis |
  |---|---|---|
  | `--compare` | KEEP | cannot write |
  | S18 mirror | KEEP | a lazy `LANE_REGISTRY` is a follow-up |
  | CA12 kill-switch | KEEP | D1 |
  | S16 switches | per CQ-3 | |
  | `executor` arm, allow-list parse, `ACTIVE_LANES` | per CQ-2 (default KEEP) | |
  | `PLANTGEO_JOB_EXECUTOR_REPAIR_INTERVAL_SECONDS` | **KEEP at 0 permanently** | safety for old images (c7-5) |
  | `_unwritten_entries` legacy-key path | KEEP while retained history can carry it | |
  | `jobs-lane-census`, `data parquet prefix-census` | KEEP | read-only ops tools |
  | `jobs-drain-definition`, `jobs-retire-definition` | KEEP | `--restore` is the only way back for a retired name |
  | CA1 marker, `retired_through`, `lane_fixtures.py` | KEEP | |
  | `test_config_dispatch_without_legacy_spec.py` | RENAME to `test_config_lane_dispatch.py` (cp + rm) | |
  | `toggleLane` (`/admin/jobs`) | known bypass; follow-up to route it through the pinned verbs (`f1-executor` owns `jobs.ts`) | spec K35 |
  | `schema-baselines/20260909-prod-preserve-set.tar.gz` | KEEP; any restore after G9R must re-run `jobs-retire-definition --apply` for every name in the retired audit rows | COM2-06 |
  | `.mpg/*.json` palaces for legacy lanes | KEEP as history; `i-coordinator` adds one `legacy-path-superseded` stash per palace pointing at the terminal-days note (verify with `scrt --mp-list-search legacy`); prune scan-tagged stashes only | COM2-08 |
  | `.agri-local-runs/{nasa-firms-archive-walk,historical-nasa,mtbs,soil-wetness-root-zone-parquet-cutover,burn-severity-cutover-backup,locks}`, legacy `plans/` files | KEEP; deleted only if the CQ-7 answer names them | COM2-13 |
  | exported local-task XML | KEEP in the object store | SAF2-16 |
  | Wave O switches (`SOFT_FAILURE`, `CODE_PROBE_HOURS`, `UPSTREAM_TELEMETRY`, `LOG_ROUTING`) | KEEP (operational kill switches, not migration scaffolding) | spec §4.9.3 |
  | Wave O legacy-exit path (R1/R2/R4 in `exit_classes.py`, `LEGACY_LEVEL_OVERRIDES`, the unmarked-report fallback) | per CQ-2 (default KEEP with the legacy arm; S8) | spec §4.9.5 |

- [ ] S6: `ingest/validation/models.py::DEFAULT_STREAM_DEFINITIONS` `cadence_basis`: rewrite every
      string that names a deleted launcher, a retired definition or a schedule that differs from the
      landed TOML `forward_cron` (incl. fire-detections' `jobs-firms-archive`).
- [ ] S7: delete `durable-archive-backfill.sh`, `fill-firms-gap.sh` and `firms-archive-full.sh`
      **only after the R7 caller census is clean** (every caller deleted, owned, or allow-listed as
      history) and CQ-7 is answered.
- [ ] S8 (Wave O, spec §4.9.5; **only under the CQ-2 alternative**): delete the generic legacy-exit
      path (R1/R2/R4 over legacy prints) in `execution/exit_classes.py`, `LEGACY_LEVEL_OVERRIDES` in
      `foundation/observability/router.py` and the unmarked-report fallback in
      `execution/turn_reports.py`. Under the CQ-2 default they stay with the shadow lanes' legacy arm.

#### `c7-docs-final` (7S; over a concrete file list fixed at 7R; any delta is a partition revision)

- [ ] F1: `docs/layer-lane-standard.md` and both skills (`layer-lane-standard`, `agri-pipelines`)
      rewritten for the TOML/strategy/runner model.
- [ ] F2: `docs/env-vars.md`; the `REPAIR_INTERVAL` row reads "no reader; held at 0 so a rollback to
      a pre-cleanup image cannot re-arm legacy repair".
- [ ] F3: AGENTS.md files, including `ingest/AGENTS.md` (the whole cohort verb table),
      `src/lib/map/AGENTS.md` and `src/lib/server/services/AGENTS.md` (citation lines).
- [ ] F4: `docs/lanes/*.md` (lines citing deleted paths only; keep the weather-observations §5
      overload note).
- [ ] F5: runbook bodies.
- [ ] F6: a comment-only pass over the concrete list, including the three web files, the two tests
      and the `ingest/firms.py` `jobs-firms-archive` note.
- [ ] F7: `services/agri-data-service/README.md`.
- [ ] F8: infra docs (`infra/job-executor/AGENTS.md`, `infra/railway/README.md`).
- [ ] F9: the `pipeline/direct/__init__.py` docstring writer count.
- [ ] F10: rewrite the `conductor/code_styleguides/python.md` writer-package rule and the
      `federation.md` offender entry for the TOML/strategy/runner model.

#### `i-coordinator` closure

- [ ] C1: `evidence/phase7.md` §R, §C and §G, **plus the retrospective row**.
- [ ] C2: production steps per the G9 gates ("Owner gates" below).
- [ ] C3 memory: D-16/17/18; a new `plantgeo-legacy-history-terminal-days.md` (terminal days, the
      verbs, the `REPAIR_INTERVAL` permanent-0 rule, the preserve-set re-retire rule, the salvage
      rule); the **mechanical G-13 sweep**; the MEMORY.md lines that go false, including the
      FIRMS-cap line once `unwritten` surfaces; the `.mpg` stash notes.
- [ ] C4: RUNBOOK (cited by heading) and plan: collapse "Owner requests, 2026-09-26 evening (being
      designed)"; correct the "still in worktree" G0 patch bullet under "2026-09-26 — ingestion grill
      → config-driven ingestion track"; add the salvage standing rule (c7-15); the D-14 pointer; mark
      plan §Rollback and the migration tripwires HISTORICAL at G9S; **flip `conductor/tracks.md` to
      done at G9S**.
- [ ] C5: G0 scaffolding: verify only; KEEP the research directory.
- [ ] C6 follow-ups: id renames, 3DHP, the ML wind feature change, the FIRMS cap, a lazy
      `LANE_REGISTRY` so the S18 mirror becomes synthesis, routing `toggleLane` through the pinned
      verbs, the docs citing the dead `ingest-backfill` verb, a pointer-retire verb for R-08 if
      ever needed, and the dead `jobs_supersede_run` except-branch in
      `execution/job_run_supersession.py` (Wave O design; no c7 slice owns that file).
- [ ] Verification: the §7.6 closure checklist is fully ticked; no importer of a deleted module
      remains; every sweep green by gate rows; every verdict recorded. [checkpoint marker]

### 7.3 Greps

Use `rg -P`, or `git grep -nP` with `:(glob)` pathspecs. Default scope:
`services/agri-data-service/{src,tests,scripts}`. `P` is the cohort alternation with the c7-9
lookaheads.

| id | pattern / check | expected after 7Q-n | expected after 7D-n / 7S |
|---|---|---|---|
| G-1 | import forms of `pipeline.direct.(P)`, `water_gauges_forward`, `ingest[./]usgs_nwis`, `validation[./]water_gauges` | only inside the 7D-n set | zero, except the allow-list |
| G-1b | path literals `["']pipeline/direct/(P)` and `"direct"` segments | only in the deletion set and `c7-delete-1`'s pins | zero, except the `tmp_path` fixtures |
| G-2 | cohort `_DIRECT_LANE_ID`, `MTBS_FORWARD_LANE_ID`, `"mtbs-forward"` | zero | zero |
| G-3 | `RepairAuthoringClock\|REPAIR_BINDINGS\|REPAIR_LANE_IDS\|jobs[-_]plan[-_]gap[-_]repair\|REPAIR_INTERVAL\|gap_repair` over the scope **plus `src/**/AGENTS.md`** | zero after 7Q-1a, except the env-vars "held at 0" row | zero |
| G-4 | `build_streamflow_gauge_identity\|fetch_source_day_from_nwis\|FIRE_FORWARD_\|usgs_nwis\.py` | — | zero, except the frozen schema comments (allow-listed) |
| G-5 | `source_checkpoint` | — | zero after S1 |
| G-6 | each retired variable over the scope + `src/` + `infra/` + `docs/` + both `.env.example` files + skills | — | **no reachable reader** (R5) before its UNSET; declarations are residue |
| G-7 | **bare tokens** `climate[-_]field\|soil[-_]wetness\|water[-_]gauges(?![-_]daily)` over web `src/**`, `agri_data_service/**` outside the deletion set (HEAD and tree for `agent/**`) and `services/plantgeo-ml-service/src/**` | every hit classified (RC-6) | same |
| G-8 | `soil-wetness` in `src/` | zero after G8 | zero |
| G-9 | `pipeline[./]direct[./](P)` over docs, skills, `**/AGENTS.md` (web included), `infra/`, `services/agri-data-service/*.md`, **`conductor/**` excluding `tracks/**`** | — | zero after 7S, except the allow-list |
| G-10 | the keep-list present (spec §4.8.7 paths, `vegetation_type/**` if created, the builders) | present | present |
| G-11 | deleted names over `infra/`, both Dockerfiles, `.dockerignore`, `railway*.json` | — | zero |
| G-12 | `pipeline[/.]direct[/.](P)` over `agri_data_service/**/*.py`, `tests/**/*.py`, `scripts/*.py`, **`src/**/*.{ts,tsx}`** | — | zero after 7S, except the allow-list |
| G-13 | **mechanical union** of every deleted path and module name, retired definition name, deleted verb and launcher, retired variable and frozen stream slug, over the memory directory, RUNBOOK and plan | — | each memory hit marked stale or in the terminal-days note; RUNBOOK and plan clean |
| G-14 | `durable-archive-backfill\|fill-firms-gap\|firms-archive-full\|ingest-backfill` over the repo, memory and local task actions | — | zero after 7S, except the history allow-list (`routes/AGENTS.md`, the `sql/routes/ops_lane_landed_evidence.sql` header) and dead-verb follow-ups |

### 7.4 Sweep, receipt and reviews

- One monitor sweep per push (7 pushes); G9S includes the **web sweep**. The inline harness
  exception is `tests/scripts/test_check_batches.py`, once per 7D.
- **The ledger-verb SQL is proven by the production rolled-back dry run, which cannot be waived**
  (CQ-12). The DB-gated test runs in the real-DB recipe when it is available, as a supplement.
- **Must pass:** `tests/test_layer_import_contract.py` (unedited through Phase 6, then the single
  `c7-delete-1` edit); the FR-13 goldens; FR-1 and S18 parity; the forecast-module claim test;
  `tests/test_sql_tree_conventions.py`; `tests/interface/test_cleanup_command_lines.py`.

| stage | review | verdict key |
|---|---|---|
| G9.0 | `/code-review high` + `/security-review` | `reviews.phase7_verbs` |
| 7R | critic on the dossier, the CQ-10 classification and the R5 orphan rows | `reviews.phase7_readiness` |
| 7Q-1a | critic + `/code-review high` (the repair subsystem, its own review) | `reviews.phase7_q1a` |
| 7Q-1b, 7Q-2 | critic + `/code-review high` | `reviews.phase7_q1b`, `reviews.phase7_q2` |
| 7D-n | `/code-review high` + `/simplify` on the modified-files diff (≤ 500 lines, split otherwise), a deletion-manifest review, and a critic on the two pin edits | `reviews.phase7_d1`, `reviews.phase7_d2` |
| 7S | `/code-review high` + critic on the docs and styleguide rewrite | `reviews.phase7_s` |

### 7.5 Production enumeration (read-only unless marked)

Every command line here is pinned by `tests/interface/test_cleanup_command_lines.py`.

| surface | command | when | expected |
|---|---|---|---|
| ledger | `railway ssh --service plantgeo-job-executor -- agri-service ops jobs-lane-census --all --since <T0> --json` at T0, then `--prefix plantgeo.executor.` | T0; after each G9V, G9Q and G9R; each 7O day; T5 | history only grows; no run of a quarantined id after its G9Q; retired names disabled with their audit rows; `unaudited_enabled_changes` empty; the live-version digest unchanged since T0 |
| brake probe | `agri-service ops jobs-set-lane-enabled --definition plantgeo.executor.<L>[:gap-fill] --disabled --operator <who> --reason <why>` (no `--apply`) | after each G9V and push | a plan, never a refusal |
| **brake drill (applied, under a go)** | the same with `--apply`, one tick, then `--enabled --apply` | G9V-1a (and CA19 at G6) | no dispatch of watersheds or `watersheds:gap-fill` during the braked tick |
| supersede probe | `agri-service ops jobs-supersede-run --lane <config L> --run-id <id> --operator <who> ...` (dry run) | after G9V-n | resolves; "not held" is fine |
| executor | `/admin/jobs`; one tick of logs | after each push | fires exit 0; no missing module or retired id |
| variables | `railway variables --service <each>` | T0; after G9V, G9R, G9S | exactly the planned changes; the c7-11 set is present; `REPAIR_INTERVAL` = 0 |
| service config | root directory, config path, watch paths | T0; after each push | no watch path names a deleted path |
| objects | `agri-service data parquet prefix-census --top-level --json`, then `--layer <slug> --json` | T0..T5 | kept streams equal their receipts; **no open claim under a frozen root**; no unknown prefix; `source-response-checkpoints/v1/` ≥ T0 |
| coverage | `agri-service data parquet coverage` (whole warehouse; no options; 29 s timeout) | T0, end of 7O, T5 | live streams advance; frozen edges unchanged. On a timeout, retry once after 60 s; on a second timeout, use `prefix-census --layer` rows for that reading and record the substitution. |
| serving | `/api/ready`; `agri-service data parquet day --layer <frozen slug> --zoom <z> --day <terminal day>` | after each push | rows returned |
| services and tasks | Railway service list; `Get-ScheduledTask 'PlantGeo*'` including `.Actions` | T0, T5 | tasks per CQ-7; XML keys recorded |
| geo.features | `to_regclass('geo.features')` in a `READ ONLY` transaction | T0 | NULL |

### 7.6 Closure checklist

- [ ] Every class A–D and F subject has a READY or RETIRE-SAFE row and a cohort, with carve-outs
      visible. The CQ-6 answer is recorded at 7R. The dossier critic verdict is recorded.
- [ ] Verdicts are recorded for G9.0, G9V-1a, G9Q-1a, G9V-1b, G9Q-1b, each G9D and G9R, G9Q-2 and
      G9S.
- [ ] G-1 to G-14 are as expected. The sweep rows are green. The receipt is fresh.
- [ ] Deny-list closure (WP-8), and the R5 orphan census is clean after the last 7D.
- [ ] The T5 census meets §7.5. `REPAIR_INTERVAL` = 0. The CA12 kill-switch is present. Retired
      variables are absent.
- [ ] Objects: no deletions; no open claim under a frozen root; top-level prefixes classified.
- [ ] Dispositions cover the switches, this lane's artifacts, `toggleLane`, the preserve-set
      tarball, `.mpg`, `.agri-local-runs`, `plans` and the task XML.
- [ ] Docs, skills, styleguides, AGENTS.md (Python and web), README, infra docs and comments cite
      only live paths. RUNBOOK is lean. Plan §Rollback is HISTORICAL. Metadata is written.
      `tracks.md` is flipped. The retrospective row is written.
- [ ] Memory: the G-13 mechanical sweep is clean; the terminal-days note is written; the index is
      updated.
- [ ] The R6 branch and worktree census is re-run clean since the last G9D (WP-9).

### 7.7 Rolling back the cleanup itself

| stage | rollback | cost |
|---|---|---|
| G9.0 | revert | one deploy |
| G9V-1a | `REPAIR_INTERVAL` stays 0 by design. A drain's flags return with `--restore`. Cancelled items stay cancelled; a lane that needs them re-plans at its next bucket. | low |
| G9Q-1a | revert 7Q-1a (`REPAIR_INTERVAL` is 0, so repair stays off) | one deploy |
| G9V-1b | re-set the recorded `ACTIVE_LANES` | one executor redeploy |
| G9Q-n / 7O-n | spec §4.8.6 rule 3, including climate supersede-if-held | the lane-rollback class |
| dashboard rollback | pre-7Q-1a: safe (`REPAIR_INTERVAL` is 0). Pre-7Q-1b: re-set the recorded `ACTIVE_LANES` first. | one deploy |
| G9D-n | fix forward; a revert needs a new go | high, deliberately |
| G9R-n | `--restore` returns the recorded per-version state; resolved incidents stay resolved | low |
| G9S | revert; re-set values; re-register tasks from the exported XML | one deploy |
| data | nothing to roll back | — |

---

## Owner gates (nothing below happens without an explicit go)

| gate | step | effect |
|---|---|---|
| G0 | push the legacy soil cap (FR-24); after 24 h and the hole-count row, **the owner sets `OPEN_METEO_API_KEY`** | Redeploys the listed services, then the executor. Soil: ≤ 33 requests (≤ 1,602 weighted hard / 1,570 clean, ≤ 132 fetch attempts (≤ 1,584 HTTP requests incl. transport retries and redirect hops)) per run; interim ceiling ≤ 44,856/day on the paid key. Precondition: the owner confirms the key's tier covers the archive API. Any later G0 revert — including a dashboard rollback — requires clearing the key first. |
| GL-1 | push `o1-logging-core` after G0's 24 h observation, at a quiescent tree (launch included) and after the Railway docs read | Redeploys the listed services. Every first-party line, the web process's included, is one redacted JSON object. No variable is required. |
| GL-2 | push `o3-ingest-meter` | Every upstream send is counted per host; providers see the WQ-5 User-Agent; USGS tiles retry independently; the burn-severity capture uses the metered client. |
| GL-3 | push `o2a-exit-classes` + `o5a-executor-observability` | Child lines are routed, levelled and redacted; every attempt carries `turn_id`, usage and an observational `exit_class`. |
| GL-4 | push `o4-usage-report` | A read-only `agri-service ops jobs-usage-report`. |
| GL-5 | push `o2b-incidents` + `o5b-soft-failure` | A failure is recorded on an incident row and cannot fail a tick or another lane; holds are still released by an operator until G1. Phase-1 authoring may start after its 24 h observation. |
| G1 | push phases 1–2, dark | Redeploys the listed services. Concurrency 1. FR-8/9/10/11 and FR-21 (shortwave drop, climate + soil repair exclusions, climate lag, soil `"50 */6 * * *"`) take effect. Wave O at G1: holds probe themselves on the WQ-1/WQ-2 ladders; only the paid Open-Meteo cap is enforced (WQ-4); the monthly usage receipt starts (WQ-6). |
| G2 | **owner sets** `USGS_WATER_DATA_API_KEY` before G3 if P4 requires it | Redeploys the executor. |
| G3 | enable `water-gauges-daily` + its DV re-pull to 1990-09-30 | Writes a new stream in parallel with legacy; the calendar floor moves to 1990-09-30 (next calendar version, A19). |
| G4 | water serve switch + legacy water paused — **only after the history-depth row passes** | Users see daily means back to 1990-09-30. Phase 4 may start after this push. |
| G5 | push `p4-extract` + the swarm, dark | No new lane runs; the calendar floor moves to 1984-01-01 (next calendar version, A19). |
| G6 | compare runs → cut-over push (+ migrated gap-fill incl. vegetation 09-01..05 and soil ≤ 1,600/turn, + pruning, + concurrency 2 if the evidence holds) | 10 lanes on the runner; 5 climate lanes start without history. |
| G7 | climate gap-fill flips | ≈1.75M Open-Meteo calls over ~9 days (or two months if split); the POWER solar re-grid; the IFS bootstrap. |
| G8 | climate rebind + web deploy | Users see Open-Meteo climate, wind at 10 m, the smaller extent, no soil wetness. |
| G9 family | the supersession cleanup lane (Phase 7; spec §4.8): G9.0, 7R, G9V-1a, G9Q-1a, G9V-1b, G9Q-1b, G9D-n, G9R-n, G9Q-2, G9S; G9X is never scheduled | Table below. Deletes code only; soft-retires water gauges, climate, `mtbs-forward`, the six `:gap-repair` names and the CQ-10 names; deletes no data. |

### G9 family (the supersession cleanup lane)

The owner runs every variable change. A ledger dry run or `--apply` is run by the owner, or by
`i-coordinator` under a go that quotes the pinned command line (§7.5) and attaches the rolled-back
dry-run output and its digest. `c7-*` sub-agents never touch production.

| gate | step | who runs | production effect | reversible? |
|---|---|---|---|---|
| G9.0 | push `c7-verbs` after G8 | i-coordinator | redeploy | revert |
| 7R | dossier; T0 `--all` census; claim census and P6 recovery; R5, R6 and R7 censuses; **CQ-6 answered**; critic | i-coordinator; owner (recovery go) | recovery writes availability evidence only | pointer history is kept |
| **G9V-1a** | `REPAIR_INTERVAL=0` (permanent); **applied brake drill** on watersheds and `watersheds:gap-fill`; drain the six `:gap-repair` names | owner (variable, drill go); owner or i-coordinator (drain) | variable → redeploy; ledger | drill: resume; drain: `--restore` for the flags (cancelled items stay cancelled) |
| G9Q-1a | push 7Q-1a (`c7-repair-retire` + `c7-docs-banners`); T+1 census | i-coordinator | deploy | revert |
| **G9V-1b** | cohort-1 tokens out; **soak until every cohort lane and each `<L>:gap-fill` has fired once after the change** (exit 0, CA1 marker); supersede and brake probes | owner; i-coordinator (reads) | variable → redeploy | re-set the value |
| G9Q-1b | brake `mtbs-forward` if needed; drain water and `mtbs-forward`; water terminal receipt after its pending prefix is empty; push 7Q-1b; T+1 census | owner / i-coordinator | ledger, deploy | spec §4.8.6 rule 3 |
| G9D-n | **closes the rollback window** for the named lanes; push 7D-n after 7O-n | i-coordinator | deploy | only by a new go |
| G9R-n | retire the pinned names (n=1 adds the six repair names and the CQ-10 names); unset variables after the reachable-reader proof | owner / i-coordinator | ledger flags, variables | `--restore`; re-set values |
| G9Q-2 | brake climate (RC-6 green); G9V-2 + soak; claims empty under 11 roots; drain; receipts; push 7Q-2 | owner / i-coordinator | ledger, variables, deploy | spec §4.8.6 rule 3 |
| G9S | push 7S; unset `MAX_LANES_PER_TICK` and the S16 variables (CQ-3); export XML, then unregister the three tasks (CQ-7); T5 census; `tracks.md` | owner; i-coordinator | deploy, variables, local tasks | revert; re-set; re-register from XML |
| G9X | never scheduled: any ledger purge; any object, partition, checkpoint, calendar-version or pending-claim delete; any `geo.*` DELETE; deleting a Railway service, branch, worktree, `.agri-local-runs/` or `plans/` content; unregistering any other local task | — (a separate written owner decision naming the rows or resources) | — | no |

## Tripwires

`metadata.json` → `partitions.tripwires` holds the full list. These are the load-bearing ones:

- G0 lands before any Phase-1 authoring; the key is set only after G0's observation; clearing the key
  is mandatory before any G0 revert.
- A soil probe fault exits 0 (`unavailable`/`deferred`); only a thin fan-out or a changed support
  exits 1.
- A TOML change without a sweep and receipt refresh fails the image build; every gated push is made
  at a quiescent point in the one shared checkout (S17; no worktrees).
- Nothing shared lives in `pipeline/lanes/`; the contract is `pipeline/runner/contract.py` (S14);
  `tests/test_layer_import_contract.py` passes unedited through Phase 6 and is edited exactly once,
  by `c7-delete-1`, under critic review.
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

Wave O (Phase 0W and its G1 half; full text in `partitions.tripwires` wave-o-1–wave-o-11):

- **wave-o-1:** GL-1–GL-5 launch only after G0's 24 h observation and at a quiescent service tree
  (launch included, after the publication-debt merge); each GL push counts as a phase; no Phase-1
  authoring before GL-5 is observed.
- **wave-o-2:** a Wave O fault never fails a tick: every Wave O statement runs in a savepoint and a
  failing lane degrades to HEAD planning; one lane's planning fault never stops another.
- **wave-o-3:** telemetry fails open; a garbled switch resolves to the legacy state.
- **wave-o-4:** headers, bodies, URLs, SQL, parameters and locals are never logged; redaction stays on
  with `LOG_ROUTING=off`; a secret hit is a rollback trigger.
- **wave-o-5, wave-o-6:** only charged (metered or reported) spend stops forward; only the paid
  Open-Meteo monthly cap is enforced (WQ-4); metering and the report cover every host regardless.
- **wave-o-8:** a hold probe is one attempt, never more often than the lane's cadence; a lost probe
  never advances the rung; no deploy-time probe (WQ-7).
- **wave-o-10:** no new raw HTTP client outside `ingest/http.py` and the reasoned allow-list; no lane
  module prints.

Cleanup lane (Phase 7; full text in `partitions.tripwires` c7-1–c7-16):

- **c7-1:** the cleanup deletes no data row or object (ledger rows, checkpoints, partitions,
  availability generations and pending claims, calendar versions, `source-response-checkpoints/v1/`,
  `schema-baselines/`, the exported task XML); any purge is G9X.
- **c7-2:** drain and retire act on exact pinned names only, never by prefix, suffix or absence;
  `<lane>:gap-fill` and class-A forward names are never drained or retired.
- **c7-4:** before a 7Q-n push the census shows zero non-terminal work and live leases on pinned
  names, no unmarked or `gap-repair-command` items on the cohort's class-A names, no pending claim
  older than one fire interval, and the config-dispatch test green.
- **c7-5:** `PLANTGEO_JOB_EXECUTOR_REPAIR_INTERVAL_SECONDS` is `0` from G9V-1a and never unset.
- **c7-7:** `ACTIVE_LANES` token removal is its own owner step, followed by a soak, before the 7Q push.
- **c7-8:** the rollback window closes only by a G9D-n go naming the lanes; CQ-6 is answered at 7R.
- **c7-13:** dry runs execute and roll back; `--apply` is pinned to the dry-run digest; `c7-*`
  sub-agents never touch production.
- **c7-15:** after G9D-n, any salvage from a branch or worktree re-runs the greps first; hunks on
  the deleted legacy path are dropped, never re-applied; this lane deletes no branch or worktree.
