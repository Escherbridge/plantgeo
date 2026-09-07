---
type: evidence
---

# Why four registered lanes never reach `/api/v1/parquet/coverage`

Measured 2026-09-06 at commit `e1837d1`. Read-only throughout.

## The measured fact

`GET /api/v1/parquet/coverage` on `plantgeo-parquet-api` returns **112 lane-rungs across 28 layers**.
`LANE_REGISTRY` (`pipeline/parquet/lane_registry.py:1254`) registers **32** lanes. Absent entirely:
`calendar`, `signal`, `climate-field-dew-point`, `climate-field-relative-humidity`.

The discriminator that made this worth chasing: **`soil-survey` publishes zero days and still appears**
with empty ranges. So "absent" is not "empty" — they are different code paths with different failure
modes, and one of them is silent.

## 1. The predicate that drops them

Two disjoint coverage subsystems answer different lane sets.

**Generic lanes always emit a row, even at zero data.** `close_lane_coverage`
(`parquet_ops/coverage.py:169-172`):

```python
if not data_days:
    # Never written: `null` bounds, and no ranges. A slider must not mount an axis over a lane
    # whose span is a guess -- `soil-survey` has 238,986 source rows and 0 written objects.
    return _bounded(lane, tier=tier, earliest_day=None, latest_day=None, published_ranges=())
```

That is why `soil-survey` appears. **The four absent lanes never enter this path.**
`registered_census_lanes()` filters them out at `parquet_ops/coverage.py:114`:

```python
if registration.slug not in NON_SLIDER_REGISTERED_LAYERS and registration.slug not in PRODUCT_BY_LAYER
```

- `NON_SLIDER_REGISTERED_LAYERS: Final = frozenset({"calendar", "signal"})` (`coverage.py:60`) drops
  those two by name.
- `PRODUCT_BY_LAYER` (built at `parquet_ops/snapshot_products.py:320`) drops every registered
  **snapshot product** — which both climate-field lanes are (`snapshot_products.py:206-224`).

Snapshot products are supposed to get coverage from a *second* subsystem, `build_snapshot_coverage`,
merged back in `interface/http/parquet_routes.py:368-427`. **That is where the silence is**
(`snapshot_products.py:844-847`):

```python
for product, loaded in zip(SNAPSHOT_PRODUCTS, ordered_results, strict=True):
    if isinstance(loaded, SnapshotCoverageWithholding):
        withheld.append(loaded)
        continue
```

A `SnapshotCoverageWithholding` produces **no row at all** — only a warning log. A generic lane with
no data gets a row saying so; a snapshot product that faults vanishes from the wire.

## 2. Are any of these absences correct? Two of four.

**Yes, by design, for `calendar` and `signal`** — and the code says so rather than the name implying it:

- `calendar` — `lane_registry.py:532` resolves its watermark with the literal string
  *"pure computation, no source system"*; `lane_registry.py:1238` calls it "the ONE lane with no
  source system". It is a conformed dimension other lanes key to by value, not a renderable layer.
- `signal` — `conductor/code_styleguides/layer-lanes.md:44-47` classifies it as an `agri.*` stream
  rather than a `geo.layers` row; its adapter exports "one settled day of the governed signal plane
  across every analysis cell" (`lane_registry.py:555`). An analysis plane, not a slider layer.

**No, for the two climate-field lanes.** Nothing in code or docs declares their absence intentional.
They are registered `SnapshotProduct`s exactly like their six working siblings.

## 3. The live cause, confirmed today — and it is not what the code reading suggested

`plantgeo-parquet-api` logs, **2026-09-06T23:46:40.376Z**, verbatim:

```
[WARN] layer="climate-field-relative-humidity" code="census_budget_exhausted"
       reason="the coverage census exceeded its 30000-key aggregate listing budget; a partial
               census would report lanes it never reached as absent, so nothing is claimed at all"
       event="snapshot_coverage_withheld"
[WARN] layer="climate-field-dew-point"         code="census_budget_exhausted"   ... same reason
```

The load-bearing word is **`aggregate`**. The 30,000-key cap (`MAX_SNAPSHOT_KEYS`,
`snapshot_products.py:72`, enforced at `:358-360`) is spent across *all* snapshot products, not
per-lane. So these two are not withheld because their prefixes are unusually large — **they are
withheld because they are last, and the budget is already gone when the walk reaches them.** A static
code reading cannot distinguish those two explanations; this log line does.

Immediately above them in the same request, the same walk logs its own reason for listing at all:

```
[WARN] layer="soil-temperature-100-to-255cm" event="snapshot_forward_census_listing"
       reason="census_until_bootstrap proves this product's forward half by walking its live lane prefix"
```

That is `config.py:197`'s default (`parquet_coverage_authority = "census_until_bootstrap"`) forcing
every snapshot product's forward edge to be proven by a raw R2 listing of
`layer=<slug>/kind=observed/` (`snapshot_products.py:985-1008`, `foundation/parquet/paths.py:220-222`)
instead of one cheap pointer read.

## 4. The registration is not the defect

Field-by-field diff of both lanes' `LaneRegistration` against working sibling
`climate-field-wind-speed`: **no difference**. All eleven climate lanes are built from one
comprehension (`lane_registry.py:1195-1218`) with identically-defaulted `cadence_days`,
`forecast_module`, `watermark`, `writer_ceiling` (`lane_registry.py:183-192`).

Upstream is present and governed too: `T2MDEW` and `RH2M` are live entries in
`NASA_POWER_SIGNAL_SPECIFICATIONS` (`execution/weather_observations/nasa_power.py:54-64`), on equal
footing with the six working parameters. There is no missing-variable defect.

`climate-field-dew-point` also has a **verified build**: 691 objects, manifest sha256
`c2972ea61ebfb66a86fa1e834625fae163e5d0a0abfd39f8c701edca3e59b71a`, audited per
`conductor/tracks/postgres_shrink_ingest_repoint_20260825/spec.md:81-84`. So at least one of the two
demonstrably has data and is still invisible — which rules out "empty" as the explanation.

## 5. Consequence: A4 fixes this too

The withholding is a **direct consequence of `census_until_bootstrap`**. Flipping
`PARQUET_COVERAGE_AUTHORITY=availability` removes the listing walk that spends the budget, so the same
change that removes the measured 26.67 s cold coverage read also restores these two lanes. One
variable, three outcomes: startup cost, these two layers, and the census-vs-availability authority
itself.

## What is still not established

- Whether `climate-field-relative-humidity`'s historical breakdown was ever built. Its builder
  (`scripts/build_relative_humidity_from_canonical_snapshot.py:64-72`) pins `EXPECTED_DAYS = 1_560`,
  `EXPECTED_FIRST_DAY = 2022-04-30`, `EXPECTED_LAST_DAY = 2026-08-06` — matching its siblings exactly
  — but its `SnapshotProduct` carries no `expected_manifest_sha256` (`snapshot_products.py:205-212`)
  and no audit receipt exists in the repo. Settle it read-only with that script's `census` subcommand
  before bootstrapping it, or the bootstrap will faithfully index emptiness. (Note the two
  `climate-field-air-temperature-*` lanes also lack a pinned checksum and work fine, so the missing
  checksum is a data point, not a verdict.)
- The actual object count under each of the two live prefixes. With `aggregate` established as the
  budget semantics this matters much less than it did, but it would confirm the ordering explanation.
