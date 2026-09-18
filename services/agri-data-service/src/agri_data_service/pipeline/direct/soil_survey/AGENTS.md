# `pipeline/direct/soil_survey` — the soil-survey layer's source protocol

This package holds a contract and a binding, **not a writer**. It is deliberately separate from
`pipeline/direct/soil/`, which is a different layer despite the similar name: that one is the
ERA5-Land soil *field* (moisture, temperature, VPD) writer fed by Open-Meteo. `soil-survey` is
USDA SSURGO map-unit delineations — reference geometry with a version stamp
(`warehouse/schemas/soil_survey.py`, `planes/soil_survey.py`, `docs/lanes/soil-survey.md`).
Conflating the two is the single easiest mistake to make in this tree.

## The source protocol

`source_protocol.py` states what any region's soil-survey source must declare (`source_slug`,
`coverage`), how its own change watermark is read (`source_vintage_watermark`) and how a release is
pulled (`fetch_release`) — `federation.md` §2, `layer-lanes.md` §1b.

The shape is built around `layer-lanes.md` §1a's `static_lookup` nature rather than around a day:

- `fetch_release` takes **no day**. A release is the whole published survey at one vintage, not a
  day's observations, so a per-day pull would be a category error the partition layout happily
  hides.
- `SoilSurveyRelease.vintage_day` is a **version stamp**, and the protocol's docstring says so at
  the field, because a partition dated at the run date is exactly the defect §1a retracts an
  earlier ruling over.
- `source_vintage_watermark` must return a **change event**, never a poll clock. A column a re-fetch
  of unchanged ground advances launders polling into the version stamp.

## Why the pull raises

`ssurgo.py` declares the coverage claim the PNW manifest's `soil-survey` → `ssurgo` binding needs,
and raises `SsurgoPullRetiredError` from both pull methods. The SSURGO ingest module was retired in
the 2026-09 Postgres cleanup and no source-direct lane replaced it;
`pipeline/parquet/lane_registry.py` already refuses this lane's retired database watermark with
"publish SSURGO through its source-direct Parquet lane".

Raising beats the two alternatives. Returning an empty release would be a *claim* that SSURGO
published nothing, which this tree has not observed — the distinction `layer-lanes.md` §1a draws
between "current" and "not looked at". Omitting the binding entirely would leave the region check
in `foundation/region/bindings.py` unable to see any claim for `ssurgo`, so it would pass vacuously
for the one layer whose absence most needs to be visible.

Turning that absence into a governed serving answer (`absence_reason = source_unbound_for_region`
through the availability index, slider catalogue, legends and agent tools) is **wave-4 work**, not
this package's. Nothing calls these methods today, so nothing changed behaviourally.

## No shim here

Unlike `drought/` and `burn_severity/`, this package renames nothing: there was no module to move.
It therefore contributes nothing to the wave-4 deletion list.
