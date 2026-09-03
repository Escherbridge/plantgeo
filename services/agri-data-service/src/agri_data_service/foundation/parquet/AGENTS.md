# `foundation/parquet` — the object-key layout

## Responsibility
Compute, parse, and diff the partition paths of the object-store Parquet warehouse. Pure
mechanism: no I/O, no clients, no domain knowledge of any layer. Stream **S0** owns this file;
sixteen downstream streams read it and none of them redefine it.

## The layout

```
layer=<slug>/kind=observed|forecast/zoom=NN/year=YYYY/month=MM/day=DD/part-0.parquet
layer=<slug>/kind=observed|forecast/zoom=NN/year=YYYY/month=MM/day=DD/absent.json
```

Confirms RUNBOOK §0.23.6's layout assumption with `kind=` inserted per
`code_styleguides/layer-lanes.md` §2, and `zoom=` inserted by owner decision 2026-08-23. Every
component is Hive-style `name=value`, which is what lets DuckDB and Polars prune on `layer`, `kind`,
`zoom`, `year`, `month` and `day` without being told the partitioning scheme. There are exactly
**three** object kinds: a part file, an absence marker, and a completion marker.

- **`part-N.parquet`** — the data, one or more per day (index 0-9999).
- **`absent.json`** — a governed absence: the day is deliberately empty because the upstream source
  genuinely cannot serve it. Evidence payload (reason, upstream response, recorded-at, run id) lives
  in `absence.py`; gap detection classifies by key alone and stays a listing.
- **`_complete.json`** — the export finished and all parts of this day are present. Written LAST,
  after the prune. A day holding parts WITHOUT this marker is a release whose upload was killed
  part-way through; its parts are real but they are a prefix of the day, not the day itself.

**Why a three-object layout when two-objects was a deliberate design rule (RUNBOOK §0.23)?** A day
that cannot say whether it finished is worse than a layout with three kinds. Parts upload one at a
time, so a container killed mid-export leaves a prefix behind. Every part of that prefix is NEW, so
freshness signals read at-or-after the source watermark and the day wrongly resolves as `current` —
half a release published as a finished one. Deploys replacing a mid-tick container is the normal
case in this environment (§0.34.1). The cheaper alternative — a rule in `resolve_static_lane` that a
day whose last export `raised` must be re-exported — was rejected because it only fires when a
failure was RECORDED, and a container replaced mid-write records nothing.

- **`kind` is a partition, not a column branch.** Observed and forecast are sibling streams at
  identical grain. A reader that blends them cannot say which it answered from, and per
  `engineering-principles.md` a wrong-but-plausible answer is worse than an honest gap.
- **`zoom` sits ABOVE `year`, and it is an axis, not a version.** The DAY remains the version stamp;
  every tier of one day describes that same day at a different resolution. Above the date components
  a reader prunes a whole tier by directory before opening a byte, and serving resolves to a path
  template instead of a scan — which is the entire point of the placement. `zoom_prefix` is the
  listing that owns one tier of one stream.
- **Zoom is required on every builder, never defaulted.** A default writes four tiers into one
  prefix and the mistake stays invisible until serving reads the wrong resolution, long after the
  run that caused it. A key without `zoom=` is not of this layout and does not parse; the objects
  written before the axis existed are discarded and re-drained, never migrated.
- **Paths here are always *relative*.** A bucket-root prefix (`OBJECT_STORE_PREFIX`) lives outside
  this layout and is applied by `pipeline/parquet/objectstore.py`. Nothing in this module knows a
  bucket exists.
- **Separators are normalised on parse** (`\` becomes `/`) because a local staging mirror on
  Windows is a real producer path, and a backslash key would otherwise parse as absent.

## `zoom.py` — one ladder, for every layer
`ZOOM_TIERS = (0, 5, 9, 13)`, named by each rung's **minimum zoom** because an adjective
(`coarse`, `detail`) stops being true the moment the ladder changes. `serving_zoom_tier` maps an
arbitrary requested zoom to the rung at or below it — z11 is served by the z9 tier, the most
detailed geometry actually published at or under the request; rounding up would answer a z11
viewport with z13 bytes nobody budgeted for. `zoom_tier_span` gives the inclusive request range a
rung answers, and the four spans tile `0..22` (`MAX_REQUEST_ZOOM`, what Martin's composite
advertises) with no gap and no overlap.

**The ladder is uniform on purpose.** The PostGIS era cut per-layer breakpoints — watersheds at
4/6/8/10/12 (`drizzle/0023_watershed_zoom_generalization.sql:149-155`), strategy recommendations at
6/11 (`drizzle/0028_strategy_recommendations_real_geometry.sql:376,393`) — and no reader could name
a path until it knew its layer's private table. Do not build per-layer tier tables here.

`ZoomTierError` is the module's own error, matching `CalendarError` / `LaneContractError` /
`GovernedAbsenceError`; it is **not** a `PartitionPathError`, so a caller wanting both catches
`ValueError`. `zoom=` renders zero-padded to two digits (`zoom=00`, `zoom=05`) for the same reason
`month=` does: unpadded, `zoom=13` sorts between `zoom=0` and `zoom=5` and a lexicographic tier walk
silently runs out of order.

**Gap detection is per tier.** `partition_day_statuses` and `missing_partition_days` take a `zoom`
and ignore keys of any other tier. A day published at z0 says nothing about whether z13 was written,
and blending them reports coverage over a real gap.

## Why gap detection is a listing, never a scan
`missing_partition_days` diffs an expected date range against the days recoverable from a set of
keys. It never opens a file. That property is the reason the striation exists (RUNBOOK §0.23.4
decision 5, owner verbatim) and `layer-lanes.md` §4 makes it binding: *"Gap detection that opens
files has misused the layout."* Callers get keys from
`ObjectStore.list_partition_keys`, which already narrows by year and month so a listing over
1,560 days is bounded.

**Governed absences are a marker object, settled 2026-08-22 (RUNBOOK §0.25.3).** A day upstream
genuinely cannot serve gets `absent.json` at the day's partition path — classifiable by key
alone, so gap detection stays a listing. `absence.py` owns the evidence payload (reason,
upstream response, recorded-at, run id — all mandatory). The zero-row write refusal stays: an
empty Parquet file is never the absence mechanism.

**Completion markers are the third object kind (§0.34.1).** `partition_day_statuses` now
classifies each day as one of FIVE statuses:

- **`data`** — ≥1 part file AND a completion marker. A finished release; gap detection counts
  this as covered.
- **`incomplete`** — part files but NO completion marker. A release whose upload was killed
  part-way through. The parts are real but they are a prefix of the day, not the complete day.
  Gap detection counts this as unfilled (distinct from `missing`: a day nobody wrote at all).
- **`absent`** — a governed-absence marker. Gap detection counts this as covered.
- **`conflict`** — data and a governed absence together. Never produced by the write path; only a
  manual admin action can create or resolve one. Not counted as covered.
- **`missing`** — nothing at all. Unfilled.

`missing_partition_days` reports only `missing` (what a human operator needs to see).
`unfilled_partition_days` reports `missing` + `incomplete` (what the driver fills). A `conflict`
day is never produced by the write path — only a manual admin action can create or resolve one.

## The zero-part receipt: a rung that generalised to nothing
`PartitionCompletion` refuses `part_count <= 0` **except** when the receipt sets `derived_empty`,
and that exception exists to close one specific hole. A coarse rung whose derivation drops every
base row — an unlocated gauge, a feature under the tier's area floor — is retracted by
`pipeline/parquet/derivation.py::_retract_tier`. Before this field the retraction left the rung with
no parts and no marker, so the day could never present the complete rungs ladder
`pipeline/parquet/availability_index.py` demands and was **permanently unindexable** while looking
perfectly healthy to the base-tier census. See `pipeline/parquet/AGENTS.md`, "Why an emptied rung
strands its day".

Three properties make the zero-part receipt safe to admit:

- **It says which claim it is making.** `derived_empty: true` means *the source had rows and this
  rung kept none of them*; `absent.json` means *the source had nothing*. Two different statements
  keep their two different vocabularies, which was the original refusal's whole point.
- **It is written only when true**, so every marker already in the bucket re-serializes to the exact
  bytes it was read from. `availability_index._verify_completion_object` compares those bytes before
  it will bind a marker to an availability row; an always-emitted `derived_empty: false` would have
  failed that check for every day the warehouse holds. `from_json_bytes` therefore refuses an
  explicit `false` rather than tolerating it.
- **The base rung may never carry one.** `objectstore.write_completion_marker` refuses a zero-part
  receipt at `BASE_ZOOM_TIER`: an empty base day is a governed absence, and nothing else.

`partition_day_statuses` is deliberately **not** taught about it. That function classifies one tier
from keys alone and cannot see inside a marker, so a marker with no parts stays `missing` there —
correct at the base rung, where it is the residue of deleted parts. The derived rungs are classified
by `pipeline/parquet/drain.py::build_lane_ladder_census` and `gap_fill`'s ladder census, which
already ask only "did this rung assert it finished", and by `parquet_ops/serving.py::day_status_sets`,
which serves a completed-but-partless **derived** rung as a published day holding zero rows.

## Static layers use the same layout — but their `day=` means something else
RUNBOOK §0.23.6 assumed static layers (`soil-survey`, `watersheds`, `evacuation-zones`) would get
"one file per layer, no day striation". **They do not.** A static layer writes a single dated
partition per version. One layout keeps every generic reader, lister and gap detector working
across all thirteen lanes; a second layout would fork all three.

**The same path renders for all of them, and that is exactly what invited the defect this
directory now guards against.** For a series lane `day=` is an observation or release date; for a
static lane it is a **version stamp**. `lane_contract.py` makes that difference declarable rather
than assumed:

- `LaneNature` — `daily_series` / `release_series` / `static_lookup`, with
  `nature_has_time_axis`, `nature_permits_forecast` (a static lookup **never** may) and
  `nature_permits_cadence` (only a release series has a rhythm to step over).
- `SourceWatermark` — a source's own "when did this last change", plus the columns that produced
  it. An uncited version stamp reads as a measurement and cannot be re-derived, so `basis` is
  mandatory.
- `resolve_static_lane` — the whole watermark rule: a version at or after the watermark is
  `current`; otherwise ONE snapshot is owed, **dated at the watermark**, never at a run date. A
  watermark nobody read is `watermark_unread`, which is a different claim from `current`.
- `newest_covered_day` — the static-lane equivalent of `partition_day_statuses`, still a listing.

## `calendar.py` — the conformed date dimension's generator
Pure computation from a date range: one row per civil day carrying year, quarter, month,
day-of-month, day-of-year, ISO year/week/weekday, month start/end flags, the WMO meteorological
season, and **cyclical `day_of_year_sin`/`day_of_year_cos`**. It lives here because it has **no
source system** — there is no session to hand it and no query it could get wrong.

The cyclical pair is RUNBOOK §0.28.3's requirement, not decoration: a raw `day_of_year` puts
31 December 364 units from 1 January, which a model reads as maximally dissimilar when they are one
day apart. The phase divides by the day's **own** year length, so a leap year does not drift the
cycle. **Time of day, astronomical season and daylight are deliberately absent** — §0.28.3 puts the
first two in a separate dimension (crossing 96 rows into 10,000 days multiplies to millions for
nothing) and daylight in a solar fact per `(cell, date)`, because it depends on latitude too.

A version stamped `D` covers `[floor, D + CALENDAR_VERSION_FORWARD_DAYS]` (800) and must reach
`today + CALENDAR_REQUIRED_FORWARD_DAYS` (400), so a 30-day horizon from any as-of date always
resolves and the dimension regenerates roughly annually rather than daily. Covering exactly the
requirement would make it stale the next morning — the churn the static nature exists to remove.

**No business calendar.** No fiscal years, no holidays, no trading days: unsourced policy in a
dimension every lane keys to is worse than no dimension. It is also **not** a `dim_time` collapsing
the clocks — `docs/holonic-kimball-modeling.md` keeps observed / valid / available /
warehouse-recorded as separate role-named facts, and lanes key their own role-named date columns to
this one **by value**. No lane schema gains a foreign-key column for it.

## Bounds
`MAX_PART_INDEX` (9,999), `MIN/MAX_PARTITION_YEAR` (four-digit rendering), `MAX_GAP_WINDOW_DAYS`
(20,000). Each exists because its wrong value is silently plausible: `year=0999` renders,
a backwards window returns an empty tuple that looks like "no gaps", and an unbounded window
allocates a date per day forever.
