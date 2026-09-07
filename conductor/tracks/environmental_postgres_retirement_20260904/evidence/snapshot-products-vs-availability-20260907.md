---
type: evidence
---

# The 9 remaining withheld layers are snapshot products, and that subsystem is incompatible with the availability authority

Measured 2026-09-07, after all twelve availability generations published and the slider reached
14 served / 10 withheld.

## The finding

The 10 withheld rows are 1 `lane_never_written` (soil-survey, upstream key cap) and 9
`availability_unpublished`. **All 9 map to lanes in `SNAPSHOT_PRODUCTS`**, and snapshot products
carry no availability index by design.

```
SNAPSHOT_PRODUCTS layers (14):
  climate-field-air-temperature-{max,mean,min}, climate-field-dew-point,
  climate-field-relative-humidity, climate-field-wind-speed, soil-field-vpd,
  soil-temperature-{0-to-7cm,7-to-28cm,28-to-100cm,100-to-255cm},
  soil-wetness-{surface,root-zone,profile}

DEDICATED_SLIDER_PRODUCT_LAYERS (5):
  climate-field-precipitation, climate-field-shortwave-radiation,
  soil-field-moisture-{0-7cm,7-28cm,28-100cm}

OVERLAP: []          census lanes: 16          duplicate layers in census: []
```

## Why "just extend `DEDICATED_SLIDER_PRODUCT_LAYERS`" is wrong

That was the obvious fix and it is a trap. The two sets are **disjoint by construction**: the five
admitted lanes are not snapshot products at all. `registered_census_lanes()`
(`parquet_ops/coverage.py:97-122`) excludes `PRODUCT_BY_LAYER` and then adds back
`DEDICATED_SLIDER_PRODUCT_LAYERS`, so admitting a snapshot product would put it in the generic census
**while `build_snapshot_coverage` still emits its own row** — and `_build_coverage_payload` returns
`direct_rows + snapshot.lanes`.

Two rows for one layer is precisely the hazard `registered_census_lanes()`'s own docstring names:

> "The slider's capability rows are keyed by layer name and resolve to the FIRST match, so a second
> row per lane … would make which axis a layer draws depend on array order."

So the real question is not "which lanes get added to a tuple" but **"should these lanes be snapshot
products at all?"**

## What the live prefix actually holds — the discriminator is measurable

Census of `layer=<slug>/kind=observed/` for all 14:

| lane | days | span | full 4-rung |
|---|---|---|---|
| `climate-field-dew-point` | **16,654** | 1981-01-01 .. 2026-08-06, **contiguous** | 16,654 (100%) |
| `climate-field-relative-humidity` | **15,038** | 1981-01-01 .. 2022-03-05 (1-day gap) | 15,038 (100%) |
| `soil-field-vpd` | 448 | 2022-04-30 .. 2026-08-29 (sparse) | 448 (100%) |
| `soil-temperature-*` (×4) | 2 each | 2026-08-28 .. 2026-08-29 | 2 (100%) |
| `climate-field-air-temperature-*` (×3) | **0** | — | — |
| `climate-field-wind-speed` | **0** | — | — |
| `soil-wetness-*` (×3) | **0** | — | — |

Three groups, and the group decides the answer:

- **A — bootstrappable today.** `dew-point` and `relative-humidity` hold complete four-rung ladders
  over ~45 years AT THE LIVE PREFIX. Their axis is the live prefix; nothing is guessed. dew-point is
  perfectly contiguous across 16,654 days.
- **B — bootstrappable, honestly sparse.** `soil-field-vpd` and the four `soil-temperature-*`. The
  soil-temperature days are 2026-08-28/29 — written by `soil-era5-land-direct-forward` in the hours
  since it was activated, so this group is growing on its own.
- **C — NOT bootstrappable from the live prefix.** Seven lanes hold nothing there. Their history is
  in the frozen `snapshot=<id>/` root, which is disjoint from `layer=<slug>/kind=observed/`. Indexing
  the live prefix for these would index emptiness while coverage reports ~1,560 days from the
  snapshot root — an index that looks authoritative and describes nothing.

**This is why the admitted five were curated by hand, and why a blanket rule is unsafe.**

## The decision this leaves

Under `PARQUET_COVERAGE_AUTHORITY=availability` the slider demands an availability index, and snapshot
products cannot have one. So either:

1. **Move group A (and later B) out of `SNAPSHOT_PRODUCTS` into the census**, bootstrap them from the
   live prefix, and let group C keep the snapshot subsystem until its history is republished at the
   live prefix. Correct, and the per-lane evidence above is exactly what such a move needs.
2. **Teach the slider to accept a snapshot-coverage row under `availability` authority** — i.e. treat
   `build_snapshot_coverage` as an equally valid axis source rather than requiring an index.

Option 1 is a data-shaped change with a measured basis per lane. Option 2 is a contract change to the
gate itself and would need its own argument about what "availability" then guarantees. **Neither is a
tuple edit, and both are owner-level architecture calls.**

## Cost if option 1 is chosen

dew-point is 133,232 objects and relative-humidity 120,304 at the live prefix. The compiler's evidence
step runs ~150 objects/minute serially, so budget `evidence_count / 150` minutes per lane and run
lanes in parallel — the same shape as the twelve applied on 2026-09-07.
