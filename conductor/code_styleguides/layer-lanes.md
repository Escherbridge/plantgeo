---
type: layer-lane-contract
---

# PlantGeo layer-lane contract

Adopted 2026-08-22 alongside the Parquet/DuckDB pivot (`conductor/RUNBOOK.md`
§0.23–§0.24). Inherits [`engineering-principles.md`](./engineering-principles.md)
and, for implementation detail, [`python.md`](./python.md) and
[`sql.md`](./sql.md).

**Corrected the same day, before any code was written against it.** The first
draft proposed a new top-level `lanes/<slug>/` package. That was wrong: this
repo already enforces a six-layer dependency lattice
(`services/agri-data-service/tests/test_layer_import_contract.py`), and a lane
package holding both `ingest.py` (needs `httpx`) and `forecast.py` (a `method`
module, where `httpx` is forbidden) is unclassifiable under it — it violates the
lattice by construction. The contract below expresses lanes **within** the
existing lattice instead. See RUNBOOK §0.24.8 for the full correction record.

It exists because eleven layers are being rebuilt **concurrently, by different
agents, into one object store**. Without a boundary enforceable by reading a
diff, concurrent lanes converge on shared helpers, then on shared files, and the
parallelism that justified the split disappears.

## 1. A lane is a vertical slice across the existing lattice, not a new package

The enforced lattice is `foundation → method → warehouse → pipeline → planes →
interface`, each layer forbidden from importing the ones after it. **A lane does
not get its own top-level directory.** It gets one file per layer it needs,
named by the layer slug taken verbatim from `geo.layers.name`:

| concern | lives in | why that layer |
|---|---|---|
| Parquet schema for the lane | `warehouse/schemas/<slug>.py` | schema is warehouse-layer truth |
| the 30-day Monte Carlo | `method/monte_carlo/<slug>.py` | **`method` forbids `sqlalchemy` and `httpx` — that enforced purity is exactly what a reproducible forecast needs** |
| source pull + Parquet write | `pipeline/lanes/<slug>.py` | pipeline owns IO and orchestration |
| validation vs the source system | `pipeline/validation/<slug>.py` | needs the network; cannot be `method` |
| the DuckDB/Polars serving read | `planes/<slug>.py` | planes is the serving layer |
| the why: source, cadence, horizon, known gaps | `AGENTS.md` in each directory | per the repo's directory-level docs rule |

The eleven original slugs: `soil-survey`, `fire-detections`, `vegetation`,
`burn-severity`, `evacuation-zones`, `interventions`, `fire-perimeters`,
`water-gauges`, `sensors`, `weather-observations`, `watersheds`. Since added:
`signal` and `drought` (both `agri.*` streams rather than `geo.layers` rows), and
`calendar` (§1a) — thirteen registered streams, with `interventions` staying in
Postgres per RUNBOOK §0.26.1.

- **A lane never imports another lane** — no `method/monte_carlo/sensors.py`
  importing `method/monte_carlo/vegetation.py`, and no cross-slug import at any
  layer. Shared needs move **down** the lattice into `foundation` or the layer's
  shared module, in their own commit, never as a drive-by.
- **A lane writes only under its own `layer=<slug>/` object prefix.** This is
  the only rule here whose breach corrupts *another agent's* output, and it will
  not surface as a test failure.
- **The lattice test is the enforcement, and it must be extended** — see §5. A
  lane that satisfies this document but fails
  `test_layer_import_contract.py` is wrong, not the test.

## 1a. Every lane declares a NATURE, and it is part of this contract

*Added 2026-08-22. Supersedes `window_kind`/`current_snapshot`, which no longer exist.*

`layer=<slug>/kind=…/year=/month=/day=` renders identically for every stream, so the layout
silently invited one reading — "the day this was observed" — onto lanes where nothing was
observed. A lane therefore **declares** what its partition day means:

| nature | the `day=` is | gap-fill | forecastable |
|---|---|---|---|
| `daily_series` | the observation day | every day in `[floor, today − lag]` | may be |
| `release_series` | the publication's own valid/issue date | cadence steps only, in that window | may be, where the release history supports it |
| `static_lookup` | a **version stamp** | one snapshot at the source watermark, or nothing at all | **never** |

- **A `static_lookup` is reference data with a version, not a measurement taken on a date.** A
  HUC12 boundary, an SSURGO delineation, an evacuation-area set, a date dimension. There is no
  per-day obligation for one to miss, and **the earlier claim that "a snapshot day the cron missed
  is lost, deliberately" is retracted** — it described a defect, not a trade.
- **A `static_lookup` declares a SOURCE WATERMARK** — the source's own "when did this last change".
  A partition dated at or after it means *current*; otherwise ONE snapshot is owed, **dated at the
  watermark**, never at the run date. It declares no publication lag: a version stamp is not
  settled by waiting.
- **A watermark column must be a CHANGE event, never a poll clock.** A column a re-fetch of
  unchanged ground advances (`geo.geometry.last_confirmed_at`) launders the polling clock into the
  version stamp and restores the churn. Cite the columns on the watermark itself.
- **Only a `release_series` may declare a cadence above one day.** A daily series that skips days
  is either not daily or not a series. An *irregular* release series keeps cadence 1 and lets its
  own export record the non-release days as governed absences — a fixed step would walk past real
  releases.
- **`forecastable` is bounded by the nature and proven against the filesystem.** The nature is the
  ceiling; shipping `method/monte_carlo/<module>.py` is the claim. A lane claiming a horizon with no
  module, or a module with no claim, **fails a test** — §2's "declare `horizon: none` and ship no
  forecaster" is the same fact stated twice, and the two are not allowed to drift.
- **Report *current* distinctly from *not looked at*.** Both show zero missing days and they are
  different claims.

### The conformed calendar dimension

One shared `calendar` stream, nature `static_lookup`, so every lane's day arithmetic — above all
`as_of + 1..30` — resolves identically instead of twelve lanes re-deriving it. Lanes **key** their
own role-named date columns to it **by value**; no lane schema gains a foreign-key column, and the
observed / valid / available / warehouse-recorded clocks stay separate
(`docs/holonic-kimball-modeling.md`). It is pure computation, so its generator lives in
`foundation` and it must not pretend to be a database-backed lane.

Seasonality is carried as **cyclical `day_of_year_sin`/`day_of_year_cos`** plus the WMO
meteorological season (RUNBOOK §0.28.3). Time of day, astronomical season and daylight belong
elsewhere — a separate sub-daily dimension and a solar fact per `(cell, date)` — because crossing
them in multiplies the row count for nothing and daylight is not a function of the date alone.

## 2. Observed and forecast are separate streams that share one grain

This is the coupling the contract exists to enforce. Each lane produces **two**
object streams, siblings rather than variants:

```
layer=<slug>/kind=observed/year=YYYY/month=MM/day=DD/part-0.parquet
layer=<slug>/kind=forecast/year=YYYY/month=MM/day=DD/part-0.parquet
```

- **Identical grain, identical units, identical column names.** A forecast row
  and an observed row for the same cell-day differ in `kind` and in provenance,
  never in shape. A consumer that must reshape one to compare with the other
  means the lane has drifted, and the drift is the bug.
- **`kind` is a partition, not a column branch.** Future dates are served from
  `kind=forecast`; settled dates from `kind=observed`. **Never blend them in one
  file**, and never let a reader silently fall through from one to the other — a
  blended answer that cannot be traced to its kind is exactly the
  wrong-but-plausible output the engineering principles forbid.
- **The horizon is 30 days forward and is declared in the lane's `AGENTS.md`.** A
  lane that genuinely cannot forecast declares `horizon: none` and ships **no**
  `method/monte_carlo/<slug>.py`. An empty forecast module is worse than an
  absent one: it reads as unfinished work rather than a settled property.
- **When an observed day lands for a day previously forecast, delete that
  forecast partition — do not leave both.** Two answers for one day is how a
  contradiction starts circulating.

## 3. Monte Carlo forecasts carry their own provenance or they do not ship

Every row in a `kind=forecast` partition carries, without exception:

| column | why |
|---|---|
| `forecast_run_id` | ties the row to the run that made it |
| `random_seed` | the run is reproducible or it is not evidence |
| `ensemble_size` | a p50 from 20 draws is not a p50 from 2,000 |
| `horizon_days` | 1–30; how far out this row was projected |
| `issued_on` | the observed day the projection was made from |
| `quantile` *or* `draw_index` | which of the ensemble this row represents |

- **Seed the RNG explicitly and record the seed.** An unseeded Monte Carlo is
  irreproducible, and an irreproducible forecast cannot be validated against what
  actually happened.
- **Never let a forecast inherit an observed row's provenance.** Borrowing the
  observation's lineage makes a projection look like a measurement.
- **Quantiles come from the ensemble**, never from a distribution fitted after
  the fact. If the ensemble is too small to support the quantiles being
  published, publish fewer.
- `method/monte_carlo/vegetation_ndvi_forecast.py` already exists and predates
  this contract. **Bring it into conformance rather than writing a second
  vegetation forecaster beside it.**

## 4. Validation is part of the lane, not a separate project

`pipeline/validation/<slug>.py` reconciles what the lane *wrote* against what the
source system *holds* — not against the lane's own intermediate state, which only
proves the code agrees with itself.

- **Report an honest gap rather than a filled one.** A day the source cannot
  serve is a governed absence and is recorded as one, never interpolated into
  existence.
- **A gap is discoverable by listing objects, not scanning them.** The
  `year=/month=/day=` striation exists so a missing day is a missing path. Gap
  detection that opens files has misused the layout.
- **Failures name the day, the lane, and the source response.** "N rows
  mismatched" is not actionable.

### 4a. Availability is a lane artifact, not a query-time census

Every `daily_series` and `release_series` publishes a compact immutable
`availability/generation=<content-sha>/availability.parquet` and advances a checksum-bound
`availability/_LATEST.json` last. The Parquet schema has one row per `(day, rung)` with lane and
product identity, temporal nature, `published|governed_absence`, row count, source receipt,
terminal receipt key/SHA, data/completion receipts, nullable absence reason, source ceiling and
publication timestamp. File metadata and pointer bind the authoritative ordered `required_rungs`;
a selectable day is their intersection, not the observed union.

- A one-time bootstrap may enumerate verified manifests/checkpoints. It writes an immutable receipt
  containing their keys/SHAs, the source inventory root and required rungs; generation zero and all
  successors bind the receipt key/SHA in file metadata and `_LATEST.json`.
- Normal ingestion reads the previous generation, adds or replaces only terminal outcomes, writes
  and verifies a new generation, then conditionally advances the pointer.
- A pointer race is retried from the winning generation; it never overwrites another writer's
  advancement or makes partially published data selectable.
- Request paths do one pointer GET and one Parquet GET. They never list historical prefixes, scan
  data parts, query PostgreSQL or silently invoke the bootstrap census.
- Missing, stale, malformed or checksum-invalid availability fails closed. Independent audit jobs
  may re-list history; browser/API requests may not.

This index is publication state, not a substitute for per-request served-row coverage. The slider
axis comes from availability; the rendered collection still reports its own rows and spatial
coverage.

## 5. ML stays where it already is, and the lattice does not yet separate it

**ML is at `method/ml/` (10 modules) and does not move.** An earlier draft of
RUNBOOK §0.24.5 said it moves to a new top-level `ml/`; that was wrong and is
corrected. It is expected to leave for a separate **Mojo service** eventually —
which is a reason to keep its boundary sharp, not to relocate it first.

**The gap that must be closed:** `method/monte_carlo/` and `method/ml/` are both
inside `method`, so the existing lattice test does **not** stop one importing the
other. The owner's requirement — that the data rebuild not entangle with the ML
runtime migration — needs an explicit new rule in
`tests/test_layer_import_contract.py`:

- `method.monte_carlo` may not import `method.ml`
- `method.ml` may not import `method.monte_carlo`

Until that rule exists, this boundary is convention only. **Adding it is a
prerequisite for wave 2**, not a nice-to-have. *(Landed 2026-08-22 as
`SUBPACKAGE_FORBIDDEN_IMPORTS`.)*

### 5a. Domain packages in `ingest/` and `execution/` — enforced 2026-08-22

Neither directory is one of the six lattice layers, so **the lattice test never
policed them and never has**. RUNBOOK §0.25.1 decisions 1 and 2 put producers
under `<parent>/<domain>/` with shared primitives at `<parent>/` root, which
created a boundary with no enforcement at all. `test_layer_import_contract.py`
now carries `test_domain_packages_do_not_import_each_other`:

- **No `ingest.<domain>` or `execution.<domain>` may import a sibling domain.**
- **Default-deny.** Every subpackage of `ingest/` or `execution/` counts as a
  domain unless it is declared in `DOMAIN_PARENT_SHARED_SUBPACKAGES`. A domain
  added later is policed the day it lands, with nothing to remember to register
  — the opposite bias from an allow-list, whose forgotten entry is silently
  unenforced.
- **Relative imports are resolved before the check.** `from ..sibling import x`
  parses to a bare module name that no absolute-prefix match would ever catch.
- A rule that can only pass vacuously is not a rule: a synthetic two-domain
  fixture proves the check actually fires, in both import forms.

**Making the boundary true costs an extraction first.** `weather-observations`
could not move until four value types left `historical_backfill.py`, because
five sibling domains imported them from there. **The shared half moves down;
the dependents never move sideways.**

**Monte Carlo forecasting is not ML** under this split: it is a per-lane
statistical projection with declared provenance, and it belongs to the lane it
forecasts.

## 6. Review checklist

A lane change is not done until each is true:

- [ ] `test_layer_import_contract.py` passes, including the new §5 rule.
- [ ] The lane declares a §1a nature, and it matches what the source actually is.
- [ ] A `static_lookup` declares a source watermark built from CHANGE columns, not a poll clock.
- [ ] `forecastable` and the presence of `method/monte_carlo/<module>.py` agree.
- [ ] No cross-slug import at any layer.
- [ ] No write outside the lane's own `layer=<slug>/` prefix.
- [ ] `kind=observed` and `kind=forecast` share grain, units, and column names.
- [ ] Every forecast row carries all six provenance columns from §3.
- [ ] The RNG seed is explicit and recorded.
- [ ] The lane's `AGENTS.md` states source system, cadence, horizon, known gaps.
- [ ] Validation compares against the **source system**, not local state.
- [ ] Absences are recorded as governed absences, never interpolated.
- [ ] Nothing new was added under a top-level `lanes/` package — it does not exist.
