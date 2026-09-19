# planes (L4)

The serving surface: bounded readers over published artifacts and forecast partitions, and the
blueprint mounted at `/api/v1/ml`. Named `planes` rather than `serving` to match the lattice
agri-data-service already enforces, so one lattice vocabulary covers both services.

Import rules: may import `foundation`, `method`, `warehouse` and `pipeline`; may NOT import
`interface`. DuckDB is reached through `pipeline/duckdb_session.py` and the bucket through
`pipeline/object_store.py`; nothing here constructs a boto3 client of its own.

## The modules

| module | what it owns |
|---|---|
| `refusals.py` | one stable code per reason a read states nothing, and the error that carries it |
| `wire.py` | the claim block, the serving-path vocabulary, and the cell renderer |
| `query_models.py` | every query parameter, validated once before any read |
| `partition_reads.py` | the bounded day read: an explicit key list, a box, a row cap |
| `availability_reads.py` | the pointer and its generation: what a lane actually PUBLISHES |
| `fire_risk_reads.py`, `analog_reads.py`, `forecast_reads.py`, `artifact_reads.py` | the four FR-8 reads |
| `routes.py` | the blueprint, the serving pool, and the status each refusal code earns |

## Every answer carries its claim, and so does every refusal

`artifact_sha256`, `issued_on`, `claim_tier: evaluation_only` and the models' own
`EVALUATION_DISCLAIMER` ride on EVERY body, including refusals. A refusal that dropped the tier
would be the one payload a caller could quote without the disclaimer attached, and a screenshot of
a refusal is as quotable as a screenshot of a number. `artifact_sha256` is null only beside an
`artifact_absent_reason`, because a null with no reason reads as an oversight rather than a fact:
`no_artifact_published` and `lane_is_not_model_backed` are different claims about the same field.

## Branch on `outcome`, NEVER on the HTTP status alone

**This is the strongest rule on this plane, and a client that breaks it will report absences as
successes.** Every body -- answered, absent or refused -- carries a top-level `outcome`:

| `outcome` | what it means | status | `error` |
|---|---|---|---|
| `content` | the payload carries rows or a value | 200 | `null` |
| `absent` | the warehouse holds nothing for this question | **200** | set |
| `refused` | serving, or the request, was at fault | 4xx/5xx | set |

A content absence is a 200 CARRYING an `error` object, by the sibling convention that a statement
about the warehouse is not a transport failure. So `response.ok` is true for `absent`, and a client
that checks only the status reads "nothing was ever written for this day" as a successful answer
and draws it as data. Branch on `outcome` (or, equivalently, on the presence of the `error` key);
never on the status by itself. `outcome` is ADDITIVE: nothing that already rode on a body moved.

The claim block (`artifact_sha256`, `artifact_absent_reason`, `issued_on`, `claim_tier`,
`disclaimer`) is present on EVERY body without exception, and `wire.answer`/`wire.refusal` are the
only two renderers, so a new route cannot forget either field.

## The `artifacts` kind vocabulary, verbatim

`GET /api/v1/ml/artifacts/<kind>` accepts exactly `analog-ensemble` and `fire-risk`, spelled here
so a client never guesses `fire_risk` or `analog_ensemble`. Hyphenated like every platform slug
since 2026-09-19; the old underscore spelling answers `artifact_kind_unknown` and names these two.

## Which refusals are a 200

A code in `CONTENT_REFUSAL_CODES` is a statement about the WAREHOUSE (`partition_day_not_written`,
`cell_not_covered`, `availability_unpublished`), and is answered 200 with `error` set: the question
has an answer, and it is "nothing was written". Everything else is about SERVING and carries a
transport status from `REFUSAL_HTTP_STATUS`. Nothing leaves as a 500: `upstream-fault.ts` on the web
side classifies `status >= 500` as transient and retries, which would re-ask an identical question
of a process that would fail it identically.

## Two serving paths, because two lanes mean two different things by "tomorrow"

`serving_path` is stated on the wire so the web picks its day-axis without inferring one from a lane
slug it would then have to keep in sync with this service.

- `forecast_kind`: the future lives under `layer=<slug>/kind=forecast`, one partition per VALID day.
  `fire-risk`, `signal`, `vegetation`, `fire-detections` and the rest of the dispatch map.
- `release_series`: the future lives INSIDE one `kind=observed` ISSUE-day file as `valid_time` rows.
  `weather-forecast` alone, by the `layer-lanes.md` section 2 carve-out amended 2026-09-19: a
  provider run is deterministic, so a `kind=forecast` partition would have to invent the
  `random_seed` and `ensemble_size` that stream requires. The ABSENCE of a `kind=forecast` partition
  is this lane's normal state and never a refusal.

**One rule answers "which root holds this lane's future": `warehouse/lanes.forecast_root_kind`.**
The path is decided from the LANE and the availability root follows from the path, in that order.
Production `96831d8b` answered `availability_unpublished` for a healthy `weather-forecast` because
the pointer was looked up first, under the reserved `kind=forecast` root the carve-out says is
never written. Every reader (`forecast_reads`, `fire_risk_reads`, `analog_reads`) and every writer
(`forecast_lane_bootstrap`, `fire_risk_daily`, `predict_daily`, `forecast_lane_rungs`,
`warehouse/weather_forecast`) asks that helper; no module spells `"forecast"` as a kind any more.
`read_lane_availability` takes `kind` as a REQUIRED keyword for the same reason, and every
availability refusal names the kind it ACTUALLY consulted, so a message can never send an operator
looking for a pointer nothing publishes.

## `published_horizon_days` is measured, never declared

`source_ceiling - issue_day` is the DECLARED horizon and is the same number every day, so it can
never report a run that fell short. The field is measured instead:

- on `forecast_kind`, from the current availability generation's rows in the `published` terminal
  state. A run that forecast twelve of thirty horizons and absented the other eighteen reads 12,
  because a governed absence is a published DECISION and not a published day.
- on `release_series`, from the newest `valid_time` in the issue file the generation points at,
  converted to a UTC date explicitly.

`issue_day` comes from the generation too, and differently per path: a forecast generation's days
are the days it forecast, so the issue day is the day before the earliest of them; a release
generation's days are ISSUES, so the newest published one IS the issue day.

## Why a point read costs two queries

A bounded box narrows the scan, then a distance ordering picks one cell inside it, then an equality
select on that exact recorded position returns the cell's rows. One query with an unbounded
`ORDER BY` would sort the whole day to find one cell. The equality match is on the position the
partition RECORDED, never on the requested coordinates, so the answer names the cell it came from.

`NEAREST_CELL_SEARCH_DEGREES` is 0.25, the coarsest base grain this service reads. A wider radius
would answer a viewport from a cell the caller never asked about; a point outside the box is
refused with `cell_not_covered` rather than answered from the nearest lane.

## The valid-day filter converts to UTC explicitly

`CAST(valid_time AT TIME ZONE 'UTC' AS DATE)`, never a bare cast: a session zone is one edit away
from moving rows onto the neighbouring calendar day, which once moved 6,279 of 16,743 water-gauge
rows. The harness opens sessions without pinning a zone precisely so this stays true under one.

## Every day is judged against the POINTER, not against a listing

FR-4a: writing a partition does not publish it. So `forecast_reads`, `fire_risk_reads` and
`analog_reads` all resolve `read_lane_availability` and check `covers()` BEFORE the day's prefix is
listed. A lane with no pointer answers `availability_unpublished` even where complete partitions sit
under its prefix, and an analog window reports each uncovered horizon as an unreadable day rather
than refusing the horizons that were published.

## The nearest cell is chosen on the lattice, not in the engine

SQL supplies the CANDIDATE SET only -- a bounded box, `DISTINCT`, capped at
`MAX_NEAREST_CANDIDATES` -- and the choice among candidates is made in whole micro-degrees through
`foundation/lattice.py`, the one flooring rule this warehouse writes its cell origins with. An
`ORDER BY` on float subtraction inside the engine is a second, differently-rounded opinion about the
same distance, and the memory note "Polars division is frame-length dependent" is what that costs.

## A deterministic lane wears the POINT label

`layer-lanes.md` section 3, amended 2026-09-19: a model emitting one calibrated value per cell-day
writes `quantile = "point"` and `ensemble_size = 1`. `fire-risk` is that lane, so its schema carries
`quantile` as a STRING (`DETERMINISTIC_PROVENANCE_FIELDS` in `warehouse/streams.py`) while the drawn
lanes keep the float fraction. `fire_risk_reads` selects on the point label and `forecast_reads`
renders whatever the column holds. **Wire change against `bc08eca9`**: a client reading
`quantile == 0.5` off a fire-risk row now reads `"point"`.

## Nothing in a public body names an object key or a variable

Refusal messages carry a typed code and a shape, never the key that held it and never the names of
the environment variables a deployment is missing -- both are operator facts and are LOGGED. The
answered payload still names `pointer_key` and `generation_key`: those are the provenance a caller
asked for and can verify, which is a different thing from leaking the layout inside an error.

## Bounds, and what each one is for

`MAX_DAY_KEYS` (256) bounds a day's listing, `MAX_POINT_ROWS` (500) and `MAX_SERIES_ROWS` (2,000)
bound an answer, `MAX_NEAREST_CANDIDATES` (4,096) bounds the box a point read sorts,
`MAX_LISTED_ARTIFACTS` (25), `MAX_ARTIFACT_BYTES` and `MAX_LISTING_BYTES` bound the listing in
count, per object and in total, and `MAX_SERVED_GENERATION_BYTES` (16 MB) bounds the generation a
request will download -- measured with a `head`, never taken from the pointer's own
`generation_bytes`, because the pointer is the document under suspicion. None of these is a tuning
knob: each one is a shape a healthy lane never reaches, so crossing it is a fault an operator must
see rather than a slow read.

`MAX_CONCURRENT_READS` (4) is the WORKER COUNT of `ServingPool`, and reads past it are refused
rather than queued. Each slot holds its own memory-capped DuckDB session, so a queue would let the
process exceed the sum of the ceilings it was given. The pool is the ceiling because a semaphore
released when the awaiting coroutine unwinds bounds how many callers are WAITING: under a timeout
that is not how many sessions are open. `bounded_read` therefore stops waiting, calls
`DuckDbSession.interrupt()` through `ReadCancellation`, and only then answers `read_timed_out`.

## A plane holds a facade, never a writer

`open_store()` hands out a `ReadOnlyObjectStoreView`: list, size, fetch, and no `put` or `delete` to
reach at all. The serving session is opened with `serving=True`, which sets
`disabled_filesystems='LocalFileSystem'` and then `lock_configuration=true` -- in that order,
because the lock is one-way and nothing a query reaches afterwards can undo the filesystem ban.

## Testing

The route tests await the handlers directly: the Sanic test client needs `sanic-testing`, which is
not a dependency, and the handlers read nothing from a request but its query string. Everything
under the transport is real -- the object store, the DuckDB session, the partition grammar and the
availability documents -- so a test that passes has exercised the same code a deployed worker runs.
`tests/serving_harness.py` owns the fixture; `tests/test_route_annotations.py` owns the rule that
every handler annotation resolves at RUNTIME, which is what killed deployment `c125e2c2`.
