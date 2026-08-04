---
type: lane-brief
track: ingestion_warehouse_consolidation_20260803
lane: A
status: in-progress
depends_on: none
started_at: 2026-08-03
note: >-
  An earlier session set this to in-progress without landing anything —
  ingest/identity.py and tests/test_ingest_identity.py did not exist. Being
  executed for real as the blocking wave 0 of workflow wf_41869a8a-31f
  (see lane-D-python-ingest-modules.md §8).
---

# Lane A — The identity contract

Wave 0. Ships alone, before every other lane. Read
[`lanes/README.md`](README.md) first — its "Rules every lane inherits" apply here and are not
repeated below. Governing detail: [`plans/ingestion-warehouse-consolidation-2026-08-03.md`](../../../../plans/ingestion-warehouse-consolidation-2026-08-03.md)
§2.0 and §3; settled decisions in [`spec.md`](../spec.md).

---

## 1. Goal

When this lane is done there is exactly one place in the Python tree that builds a warehouse
identity string: `agri_data_service.ingest.identity`. It returns, per feature, a namespaced
`natural_key` of the form `'<producer>:<producer-local id>'` **and** the per-producer observation
timestamp that dates that feature's first geometry version. Every producer-local id it emits is
byte-identical to the string today's TypeScript job writes into `geo.features.properties->>'id'`,
proven by a golden-file test that runs with no database connection. A feature whose upstream
supplies no stable native key is **rejected with a typed error** — never given a synthesised key.
Lanes B (backfill dating) and D (six ingest modules) consume this module's key format and
timestamp rule; nothing else in the repo is allowed to re-derive an identity string.

---

## 2. Prerequisites

Lane A is wave 0 and depends on no other lane. Verify the environment instead.

| # | Check | Command (run from `services/agri-data-service`) | Expected |
|---|---|---|---|
| 1 | `uv` toolchain present | `uv --version` | `uv 0.9.x` or newer (verified: `uv 0.9.18`) |
| 2 | Deps installed | `uv sync --locked --all-extras` | exits 0 |
| 3 | The `ingest` package does **not** exist yet | `ls src/agri_data_service/` | no `ingest` entry — you are creating it |
| 4 | Baseline suite is green before you start | `uv run pytest -q` | passes; db-backed tests skip (do **not** set `AGRI_TEST_DATABASE_URL` for this lane) |
| 5 | The TypeScript you are porting is unchanged | `git -C ../.. log -1 --oneline -- src/lib/server/services/ingestion-jobs.ts` | `5bf03ec` or later; if a newer commit touched it, re-read §4's line references before trusting them |

No database is required for this lane at any point, except the optional read-only payload capture
in step 4.2.

---

## 3. Files you own

Exactly two, from [`lanes/README.md`](README.md) (§"File boundaries"):

| Path | State |
|---|---|
| `services/agri-data-service/src/agri_data_service/ingest/identity.py` | new |
| `services/agri-data-service/tests/test_ingest_identity.py` | new |

Plus two unavoidable package-creation files, because `ingest/` does not exist yet and the module
cannot be imported without them:

| Path | State | Note |
|---|---|---|
| `services/agri-data-service/src/agri_data_service/ingest/__init__.py` | new, **empty** | keep it empty. Lane D owns everything else under `ingest/` except `mtbs.py`, which is lane E's |
| `services/agri-data-service/src/agri_data_service/ingest/AGENTS.md` | new | house style puts rationale here, not in code comments. Write only the `identity.py` paragraph and leave the file open. Lane **D** is the only other wave-1 lane permitted to append; lane E hands its MTBS paragraph to D through the orchestrator (`README.md` §"Shared files that no single lane owns") |

**Touch nothing else.** Lanes B, D, E, F, G and H start the moment this lane lands and several of
them will already be running when you finish. In particular do **not** edit
`src/lib/server/services/ingestion-jobs.ts`, `src/lib/server/db/schema.ts`, `drizzle/**`,
`db/agri/**`, or any other file under `src/agri_data_service/`. If you believe you need something
outside the list, stop and report it rather than reaching across.

---

## 4. The work

### 4.1 Write `ingest/identity.py`

Pure functions only. No I/O, no SQLAlchemy import, no `agri_data_service.db` import — the golden
test must be able to run before any lane has a database. Follow the module conventions already in
`src/agri_data_service/execution/source_ingestion.py`: `from __future__ import annotations` at the
top (`source_ingestion.py:3`), a one-line module docstring, frozen pydantic/dataclass models.

**The result type.**

```python
@dataclass(frozen=True, slots=True)
class FeatureIdentity:
    """One producer's identity for one feature, plus the timestamp that dates its first geometry version."""

    producer: str                 # namespace token, e.g. 'firms'
    producer_local_id: str        # byte-identical to the TypeScript featureId it replaces
    observed_at: datetime | None  # tz-aware UTC; None means '-infinity' (see §4.1 note)

    @property
    def natural_key(self) -> str:
        return f"{self.producer}:{self.producer_local_id}"
```

`observed_at is None` is the contract's representation of the plan's `'-infinity'` fallback (plan
§2.0 backfill table, lines 282-290). Lane B writes `'-infinity'` into
`geo.geometry.version_valid_from` for it. Do not invent a sentinel datetime; do not fall back to
`now()`.

**Producer tokens.** Validate against the same pattern the warehouse already uses for governed
source keys, `^[a-z0-9][a-z0-9-]{1,98}$` (`execution/source_ingestion.py:47`), and reject a
`natural_key` longer than 255 characters — that is the width of `geo.geometry.natural_key` in the
plan's DDL (plan §2.0, line 127).

| Producer token | Producer-local id | TypeScript source | `observed_at` |
|---|---|---|---|
| `firms` | `satellite:acqDate:acqTime:lat(4dp):lon(4dp)` | `ingestion-jobs.ts:100-115` (call site `:146-149`) | `acqDate` + `acqTime` parsed as UTC, per `environmental-time.ts:15-50` |
| `usgs-nwis` | `<siteNo>:<updatedAt>` | `ingestion-jobs.ts:189` | `updatedAt`, verbatim from `usgs-water.ts:183` |
| `open-meteo` | `lat(4dp):lon(4dp):<observedAt>` | `ingestion-jobs.ts:294` | `observedAt` (`weather.ts:74`) |
| `wfigs` | `<uniqueFireIdentifier>` | `ingestion-jobs.ts:334` | `properties.polygonDateTime` where present (`ingestion-jobs.ts:340`), else `None` |
| `usdm` | `<validDate>:<dmCategory>` | `drought-ingestion.ts:55-60`; uniqueness index at `src/lib/server/db/schema.ts:313-316` | release `validDate` (a Tuesday), 00:00 UTC |
| `mtbs` | `<Fire_ID>` | `src/lib/server/services/mtbs.ts:48,80` | `None` — version on the annual release identifier, not `Ig_Date` (spec D3) |

> **Correction to the plan text.** The plan cites the WFIGS key at `ingestion-jobs.ts:335`; the
> `featureId` assignment is actually at **`:334`** (`:335` is the `properties` object that follows).
> Same behaviour, corrected line.

> **Correction to the plan's backfill SQL.** Plan §2.0 line 266 builds the backfill key as
> `l.name || ':' || (f.properties ->> 'id')` — i.e. namespaced by *layer name*
> (`fire-detections`, `water-gauges`, `weather-observations`, `fire-perimeters`), while
> line 210 and line 474 specify *producer* names (`firms:…`, `mtbs:<Fire_ID>`). These disagree.
> See §7 Q1 — resolve it in this lane, because lane B's backfill cannot start until you have.

**Public functions** (full-word names, one-line doc-comment each, rationale to `ingest/AGENTS.md`):

```
build_firms_identity(properties, coordinates)            -> FeatureIdentity
build_streamflow_gauge_identity(gauge)                    -> FeatureIdentity
build_weather_observation_identity(latitude, longitude, observation) -> FeatureIdentity
build_fire_perimeter_identity(perimeter)                  -> FeatureIdentity
build_drought_area_identity(valid_date, drought_monitor_category) -> FeatureIdentity
build_burn_severity_identity(properties)                  -> FeatureIdentity   # MTBS
format_coordinate(value)                                  -> str  # JS toFixed(4) replica
format_javascript_timestamp(moment)                       -> str  # JS toISOString replica
PRODUCER_BY_LAYER_NAME: Mapping[str, str]                       # for lane B's backfill
```

Plus one typed error the whole contract turns on:

```python
class MissingNativeKeyError(ValueError):
    """Raised when an upstream record supplies no stable native key; never synthesise one."""
```

**The rejection rule is the point of this module (plan §2.0, line 296).** Raise
`MissingNativeKeyError` — do not fall back to coordinates, a payload hash, a UUID, or the
wall clock — when:

1. FIRMS: `satellite`, `acqDate` or `acqTime` is absent/blank, **or** either coordinate is
   missing. The TypeScript silently emits an empty segment here (see trap T3).
2. USGS gauges: `siteNo` is blank, **or** `updatedAt` was not supplied by upstream. See trap T4 —
   this is a deliberate behaviour change from the TypeScript.
3. Open-Meteo: `observedAt` absent.
4. WFIGS: `uniqueFireIdentifier` absent/blank.
5. MTBS: `Fire_ID` absent/blank. `Fire_ID` is the model for "name your native key" that every
   Phase 5 source must follow (plan §2.0 line 296).

`observed_at is None` is legal (WFIGS without `polygonDateTime`, MTBS). A **missing key** is not.
Keep the two concepts apart in the code and in the tests.

### 4.2 Capture the golden payloads

You need, per producer, a recorded upstream payload and the exact key string the TypeScript
produced from it. Two sources, in preference order.

**Preferred — the production database already holds both.** `geo.features.properties` is the raw
upstream payload and `properties->>'id'` is the byte-exact TypeScript output, for ~15 000 rows
across four layers. This is authoritative in a way a hand-written fixture is not: it is what is
actually stored and what dedupe actually compares (`src/lib/server/services/ingest.ts:56-65`).

Read-only, deterministic (no `ORDER BY random()` — the fixture must be reproducible), one layer at
a time. `psql` options go **before** the connection string:

```powershell
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" --csv --tuples-only `
  -c "SELECT f.properties->>'id', f.properties::text
      FROM geo.features f JOIN geo.layers l ON l.id = f.layer_id
      WHERE l.name = 'fire-detections'
      ORDER BY f.properties->>'id'
      LIMIT 25" `
  "$env:PLANTGEO_READONLY_URL"
```

Repeat for `water-gauges`, `weather-observations`, `fire-perimeters`. **SELECT only** — this lane
writes nothing to any database, ever. Never let the production URL reach a test runner: copy the
rows out, then unset the variable.

**Fallback — synthesise from the TypeScript itself.** If no populated database is reachable, run
the TS builders directly and record their output. `src/__tests__/services/ingestion-jobs.test.ts:5-19`
mocks every fetcher, so you can feed a literal payload through `runFireIngestionJob` and read the
`featureId` off the `ingestFeatures` mock. Do this only if the database route is unavailable; it
proves the port against the current code rather than against stored history.

For USDM and MTBS there is no `geo.features` row (USDM lands in `geo.drought_areas`, MTBS is not
ingested yet). Construct those two fixtures by hand from `drought-ingestion.ts:55-60` and
`mtbs.ts:48,80` respectively.

**Where the fixtures live:** inline, as module-level literals in
`tests/test_ingest_identity.py`. That keeps them inside the lane's declared boundary. A separate
`tests/fixtures/` file would widen the boundary and is not worth the collision risk — see §7 Q2.

### 4.3 Write `tests/test_ingest_identity.py`

The gate, per plan §3 verification step 1 (line 520): **this test must pass before any Python
ingest code is allowed to open a database connection.**

Required cases:

1. **Pinned key strings.** For every recorded payload, assert the exact `producer_local_id`
   equals the recorded TypeScript `properties->>'id'`, and assert the full `natural_key` string
   literally. Assert the whole string, not a prefix or a regex — a regex is exactly what would let
   a `toFixed` drift through.
2. **Float formatting, pinned independently.** Table-driven over `format_coordinate` with the
   tie values in trap T1. This must fail loudly if someone later "simplifies" it to an f-string.
3. **Timestamp formatting, pinned independently.** `format_javascript_timestamp` over the cases in
   trap T2.
4. **Rejection.** Each of the five conditions in §4.1 raises `MissingNativeKeyError`. Assert on the
   exception type, not the message.
5. **`observed_at`.** Per producer, assert the exact tz-aware UTC datetime, and assert `None` for
   WFIGS-without-`polygonDateTime` and for MTBS. This is what lane B's backfill dates v1 rows
   from; if it is wrong the entire slider history is wrong and the map still looks fine.
6. **Namespace separation.** Two producers carrying the same producer-local id must produce
   different `natural_key`s. This is risk 1 (plan line 785) in one assertion.
7. **Length and pattern guards.** An over-255-character key and a malformed producer token both
   raise.

The test must not request `agri_db_dsn`, `agri_db_connection` or `agri_db_async_dsn` — those
fixtures mark a test `agri_db` (`tests/conftest.py:147-154`) and a marked test that skips while
`AGRI_TEST_DATABASE_URL` is set fails the whole session (`conftest.py:156-177`).

### 4.4 Write `ingest/AGENTS.md`

One paragraph, matching the density of `src/agri_data_service/execution/AGENTS.md`: what
`identity.py` is (the single definition of a warehouse identity string), what it deliberately is
not (a fetcher, a validator, a database writer), why the producer namespace is a correctness
requirement rather than hygiene under a Type-2 dimension, why it rejects rather than synthesises,
and why coordinate formatting goes through `Decimal` instead of an f-string. Put the rationale
here; the code carries one-line doc-comments only.

---

## 5. Traps

Lane-specific. The generic rules are in [`lanes/README.md`](README.md) §"Rules every lane inherits".

### T1 — `toFixed(4)` and `f"{x:.4f}"` genuinely disagree. **This is the lane's headline risk.**

Not theoretical. Measured on this machine:

| value | JS `.toFixed(4)` | Python `f"{v:.4f}"` | `Decimal(v).quantize(Decimal("0.0001"), ROUND_HALF_UP)` |
|---|---|---|---|
| `0.15625` | `0.1563` | **`0.1562`** ✗ | `0.1563` ✓ |
| `-0.15625` | `-0.1563` | **`-0.1562`** ✗ | `-0.1563` ✓ |
| `45.15625` | `45.1563` | **`45.1562`** ✗ | `45.1563` ✓ |
| `-119.15625` | `-119.1563` | **`-119.1562`** ✗ | `-119.1563` ✓ |
| `1.00005` | `1.0001` | `1.0001` ✓ | `1.0001` ✓ |
| `-0.0` | **`0.0000`** | `-0.0000` ✗ | `-0.0000` ✗ |

`toFixed` rounds ties **away from zero** on the double's exact value; Python's f-string rounds ties
**to even**. They diverge on every value that is an exact tie at four decimal places — i.e. every
dyadic rational ending in `.…5` at the fifth decimal. `boundedSamplePoints`
(`ingestion-jobs.ts:233-261`) computes weather sample longitudes by division, so dyadic values are
reachable in the one producer that also puts coordinates in its key.

**Implementation:** `Decimal(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)` —
`Decimal(float)` takes the exact binary value, and `decimal`'s `ROUND_HALF_UP` means "ties away
from zero", which is `toFixed`'s rule. Then **special-case negative zero**: JS `(-0).toFixed(4)` is
`"0.0000"`, `Decimal` gives `"-0.0000"`. Normalise `-0.0` to `0.0` before quantising.

Used in the `firms` key (`ingestion-jobs.ts:112-113`) and the `open-meteo` key (`:294`).

### T2 — JS `toISOString()` and Python `datetime.isoformat()` do not produce the same string

Open-Meteo's `observedAt` is minted as `new Date(c.time * 1_000).toISOString()`
(`src/lib/server/services/weather.ts:74`), which is always `YYYY-MM-DDTHH:MM:SS.mmmZ` — three
millisecond digits, always present, terminal `Z`. Python's `datetime.isoformat()` emits
`+00:00` and **omits** the fractional part when microseconds are zero. If you round-trip a
timestamp through Python and re-emit it, the key changes and every row duplicates.

Two consequences:

- `format_javascript_timestamp` must force three digits and a literal `Z`.
- Better still: for `open-meteo`, `usgs-nwis` and `wfigs`, **do not re-emit at all**. Take the
  upstream string verbatim into the key and parse a *separate* copy for `observed_at`. The USGS
  case makes this mandatory: `updatedAt` is NWIS's `dateTime` passed straight through
  (`usgs-water.ts:183`), a local-offset string such as `2026-08-03T09:15:00.000-07:00`. Normalising
  it to UTC would change the key.

### T3 — FIRMS `acqTime` is used **raw** in the key but **zero-padded** for the timestamp

`firmsObservationId` interpolates `properties.acqTime` with no normalisation
(`ingestion-jobs.ts:111`), while `parseFirmsObservationTime` pads it to four digits before parsing
(`environmental-time.ts:26-31`). So an upstream `acqTime` of `36` yields key segment `36` and
timestamp `00:36Z`. Pad for `observed_at`; never pad for the key.

Also at `ingestion-jobs.ts:112-113`: `coordinates[1]?.toFixed(4)` uses optional chaining, and
`Array.join` renders `undefined` as an empty string — so a coordinate-less FIRMS feature currently
produces a key with empty trailing segments (`N21:2026-08-03:0142::`). Do **not** replicate that.
Raise `MissingNativeKeyError` instead; a degenerate key is a synthesised key wearing a real one's
shape, which is precisely what §4.1's rejection rule exists to stop.

### T4 — The USGS gauge key can contain the wall clock, which is a synthesised key

`usgs-water.ts:183`: `const updatedAt = latest?.dateTime ?? new Date().toISOString();`. A gauge
reporting no values gets an `updatedAt` of *now*, which then goes verbatim into the featureId
(`ingestion-jobs.ts:189`). Today that mints a brand-new `geo.features` row on every single run for
every silent gauge. Under a Type-2 dimension it would mint a brand-new geometry version chain each
hour.

**Reject it.** `build_streamflow_gauge_identity` raises `MissingNativeKeyError` when upstream did
not supply `dateTime`. This deliberately diverges from the TypeScript, and it is the one place
where "byte-identical to the TS" does not apply — the TS output is non-deterministic there and
therefore not golden-testable at all. Record the divergence in `ingest/AGENTS.md`.

### T5 — Under Type-2 a wrong key does not duplicate a row, it fabricates a history

Risk 1 (plan line 785) and `lanes/README.md` §"The one real hazard". `natural_key` no longer
carries a bare global UNIQUE: it is `UNIQUE (natural_key, version_valid_from)` plus a partial
unique on `natural_key WHERE version_valid_to IS NULL` (plan §2.0, lines 140, 153-154). Its meaning
changed from "this row is unique" to "these rows are the same place over time". Two producers
colliding on an unnamespaced id are therefore **interleaved into one version chain**, producing a
plausible-looking history that is fiction — strictly harder to detect than a duplicate. The
producer namespace is a correctness requirement. Test case 6 in §4.3 is what proves it.

### T6 — Never derive `observed_at` from a write timestamp

`geo.features.created_at` is "last touched", not "first seen": the refresh-in-place path rewrites
it (`src/lib/server/services/ingest.ts:107-122`), so all rows read as created today. If you capture
fixtures from the database (§4.2), the payload's own fields are the only honest timestamp source —
`created_at` and `updated_at` are not, and neither is `now()`. Risk 2c, plan line 789.

### T7 — Lint and typing are strict, and CI does not run tests

`ruff.toml` enables `ANN` (every parameter and return annotated), `DTZ` (no naive datetimes — use
`datetime(..., tzinfo=UTC)`), `PT`, `PL`, `RUF` and more, at `line-length = 120`. `mypy.ini` is
`strict = true`. And the Docker `checks` stage runs `ruff format --check`, `ruff check` and `mypy`
only — **no tests run in CI** (track plan, "Environment: the regeneration path"). Nothing automated
will catch a broken golden test. Run it yourself.

---

## 6. Definition of done

Run these from `services/agri-data-service`, once, at the end. Do **not** set
`AGRI_TEST_DATABASE_URL` — this lane needs no database, and setting it turns any db-backed skip
into a session failure (`tests/conftest.py:156-177`).

| # | Command | Proof of success |
|---|---|---|
| 1 | `uv run pytest tests/test_ingest_identity.py -q` | all pass, **0 skipped** |
| 2 | `uv run pytest -q` | no regression against the §2 step-4 baseline; same pass count plus your new tests |
| 3 | `uv run ruff format --check src/ tests/` | `N files already formatted` |
| 4 | `uv run ruff check src/ tests/` | `All checks passed!` |
| 5 | `uv run mypy src/` | `Success: no issues found` |
| 6 | `uv run python -c "from agri_data_service.ingest import identity; print(identity.build_burn_severity_identity({'Fire_ID': 'ID4315711583020210714'}).natural_key)"` | prints `mtbs:ID4315711583020210714` — proves the module imports with no database and no config |
| 7 | `git status --short` | exactly the four files in §3, no others |

Then report to the orchestrator, in the handoff message and **not** in a new file:

- the final `natural_key` format string, verbatim, per producer;
- the resolution of §7 Q1 (producer token vs. layer name), because lane B is blocked on it;
- the `observed_at` rule per producer, and which producers legitimately return `None`;
- the T4 divergence from the TypeScript, so lane D does not "fix" it back.

---

## 7. Open questions

**Q1 — Is the namespace the producer or the layer name? Blocking for lane B.**
The plan contradicts itself: the backfill SQL uses `l.name` (plan §2.0 line 266) while the key
format and the MTBS row use producer tokens (`firms:…` at line 210, `mtbs:<Fire_ID>` at line 474).
*Recommendation:* **producer tokens** — `firms`, `usgs-nwis`, `open-meteo`, `wfigs`, `usdm`, `mtbs`.
Layer names are presentation-layer and renameable; a producer is the thing that actually owns an id
space, and `geo.geometry.producer` (plan §2.0 line 137) is documented as answering "which ingest
owns this row" without parsing the key, which only holds if the token is the producer. Export
`PRODUCER_BY_LAYER_NAME` from `identity.py` so lane B substitutes it in the backfill `INSERT`
instead of `l.name`. Tell lane B explicitly; do not assume it reads the plan's SQL literally.

**Q2 — Inline fixtures or a `tests/fixtures/` file?**
The lane's declared boundary is two files. *Recommendation:* inline module-level literals in
`tests/test_ingest_identity.py`. If the captured payloads exceed roughly 400 lines, escalate to the
orchestrator for a boundary amendment rather than silently creating a third path — lanes D and E
also write under `tests/`.

**Q3 — How is `observed_at is None` (`'-infinity'`) carried across the lane boundary?**
`FeatureIdentity.observed_at` is a Python `datetime | None`, but lane B writes SQL. *Recommendation:*
`None` maps to the literal `'-infinity'::timestamptz`, per plan §2.0 line 287-290, and lane A states
that in `ingest/AGENTS.md` so lane B does not reinvent it. Do not add a `-infinity` sentinel
datetime to the dataclass; `datetime.min` is a real, comparable date and would silently sort as
year 1 rather than as "before all time".

**Q4 — Does `identity.py` also own the change-detection rule and circuit-breaker threshold?**
Plan §2.0 says each producer declares its versioning rule and threshold "in its ingest module",
next to its `data_available_at` rule — which is lane D's territory, not this one. *Recommendation:*
**no.** Keep `identity.py` to identity plus the observation timestamp. Export the producer token
constants so lane D can key its rule table off them, and stop there. Scope creep here delays every
other lane.

**Q5 — MTBS `observed_at`.**
Spec D3 pins `data_available_at` to the annual release date and forbids `Ig_Date`, and plan line
559 versions MTBS on the annual release identifier. But no MTBS ingest exists yet
(`mtbs.ts` is a read-through ArcGIS proxy, `src/lib/server/services/mtbs.ts:48`), so there is no
release identifier to read. *Recommendation:* return `observed_at = None` for MTBS in this lane and
let lane E supply the release date when it builds the real ingest. Note it in `ingest/AGENTS.md` as
a known gap rather than guessing a date — a guessed release date is exactly the ~18-month leak D3
exists to prevent.
