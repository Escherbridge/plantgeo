# `foundation/parquet` — the frozen object-key layout

## Responsibility
Compute, parse, and diff the partition paths of the object-store Parquet warehouse. Pure
mechanism: no I/O, no clients, no domain knowledge of any layer. Stream **S0** owns this file;
sixteen downstream streams read it and none of them redefine it.

## The layout, and it is frozen

```
layer=<slug>/kind=observed|forecast/year=YYYY/month=MM/day=DD/part-0.parquet
```

Confirms RUNBOOK §0.23.6's layout assumption with `kind=` inserted per
`code_styleguides/layer-lanes.md` §2. Every component is Hive-style `name=value`, which is what
lets DuckDB and Polars prune on `layer`, `kind`, `year`, `month` and `day` without being told the
partitioning scheme.

- **`kind` is a partition, not a column branch.** Observed and forecast are sibling streams at
  identical grain. A reader that blends them cannot say which it answered from, and per
  `engineering-principles.md` a wrong-but-plausible answer is worse than an honest gap.
- **`part-N.parquet` allows a day to spill across files.** Gap detection lists the day directory,
  so any number of parts still reads as one present day. `part-0` is the norm; index 0-9999.
- **Paths here are always *relative*.** A bucket-root prefix (`OBJECT_STORE_PREFIX`) lives outside
  this layout and is applied by `pipeline/parquet/objectstore.py`. Nothing in this module knows a
  bucket exists.
- **Separators are normalised on parse** (`\` becomes `/`) because a local staging mirror on
  Windows is a real producer path, and a backslash key would otherwise parse as absent.

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
upstream response, recorded-at, run id — all mandatory); `partition_day_statuses` classifies
each day as `data` / `absent` / `conflict` / `missing`, and `missing_partition_days` reports
only `missing`. A `conflict` day (data AND marker) is never produced by the write path — only a
manual admin action can create or resolve one. The zero-row write refusal stays: an empty
Parquet file is never the absence mechanism.

## Static layers use the same layout
RUNBOOK §0.23.6 assumed static layers (`soil-survey`, `watersheds`, `evacuation-zones`) would get
"one file per layer, no day striation". **They do not.** A static layer writes a single dated
partition on its release day. One layout keeps every generic reader, lister and gap detector
working across all eleven lanes; a second layout would fork all three. The assumption's own stated
reversal cost — "re-partition that layer alone" — is what makes reversing this cheap if a static
layer ever proves it needs to.

## Bounds
`MAX_PART_INDEX` (9,999), `MIN/MAX_PARTITION_YEAR` (four-digit rendering), `MAX_GAP_WINDOW_DAYS`
(20,000). Each exists because its wrong value is silently plausible: `year=0999` renders,
a backwards window returns an empty tuple that looks like "no gaps", and an unbounded window
allocates a date per day forever.
