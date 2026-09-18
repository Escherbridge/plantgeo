# `pipeline/direct/drought` — the drought lane

Direct-to-Parquet drought writer. The lane's nature, cadence and floor live in
`pipeline/parquet/lane_registry.py`'s `DROUGHT_STREAM` registration and are read through
`products.py`, never restated here.

## The source protocol

`source_protocol.py` is this layer's contract (`federation.md` §2, `layer-lanes.md` §1b): what a
drought source must declare (`source_slug`, `coverage`), which days it publishes (`release_days`)
and how one dated release is read (`fetch_release_day`). `usdm.py` is the pilot's one
implementation of it; the next region writes its own national drought monitor against the same
protocol and binds it in `foundation/region/pnw.json`'s sibling manifest, touching nothing in
`rows.py`, `adapter.py`, `forward.py`, `warehouse/schemas/drought.py` or `planes/drought.py`.

`UsdmDroughtSource` is a class holding the module's existing functions rather than the functions
alone, for two reasons. A binding has to be a **value** the manifest can resolve to and
`pipeline/source_bindings.py` can hold in a table. And `isinstance` against a `runtime_checkable`
Protocol is a conformance check a test can actually run; a module object would pass the same
attribute test while nothing in the type system ever looked at it.

### What is NOT normalized yet, stated rather than hidden

`DroughtReleaseDay.release` is typed `object | None` in the protocol because the payload
`usdm.py` returns is still `ingest.usdm.DroughtRelease` — USDM's own release shape, not a
source-agnostic drought record. `federation.md` §2 is explicit that layer logic consuming a
source's shape means "the normalization is incomplete", so this is named debt, not a design: the
normalized record belongs in `warehouse/schemas/drought.py`, and moving `rows.py` onto it is a
later push. Pretending the protocol were already source-agnostic by widening the annotation and
saying nothing would be the worse failure, because the next region's author would discover it only
after writing an implementation the lane cannot consume.

## Wave-4 deletion list

- `source.py` — a deprecation shim re-exporting `DroughtDaySource`, `DroughtSourceError` and
  `fetch_drought_day` from `usdm.py`. Delete once `adapter.py`, `forward.py` and
  `tests/direct/test_drought_adapter.py` import `usdm.py` directly.
