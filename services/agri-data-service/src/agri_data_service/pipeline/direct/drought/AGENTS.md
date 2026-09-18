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

### The release payload, and what is still NOT normalized

**Retired 2026-09-18 (wave 5):** `DroughtReleaseDay.release` was typed `object | None`, which was
honest about the debt and useless to the type checker — `adapter.py` handed that `object` to
`drought_release_table` and `forward.py` read `.areas` off it, and both only failed under
`mypy --strict` once the type-only import was repointed at this protocol. It is now
`DroughtReleasePayload | None`, a `runtime_checkable` Protocol in `source_protocol.py` exposing
**exactly** the three members the lane reads and nothing else:

| member | type | read by |
|---|---|---|
| `release_day` | `date` | `rows.py` (the `valid_date` column and the `direct:` area id) |
| `source_url` | `str` | `rows.py` (the row's provenance column) |
| `areas` | `Sequence[DroughtAreaPayload]` | `rows.py`, `adapter.py`, `forward.py`'s evidence count |

`DroughtAreaPayload` is the same treatment one level down: `drought_monitor_category: int` and
`geometry: Mapping[str, object]` (WGS84 GeoJSON), which is all `support.py::repair_drought_areas_to_wkb`
touches. `rows.py` and `support.py` now take the payload protocols and no longer import
`ingest.usdm` at all, so lane logic names no source's type — `federation.md` §2's actual ask, which
a widened annotation with a comment was not.

`ingest.usdm.DroughtRelease` satisfies `DroughtReleasePayload` **structurally**; nothing inherits,
and `tests/foundation/test_source_protocols.py` proves it with `isinstance` on a fixture plus a
fabricated non-USDM record that also satisfies it. A second region's drought monitor is a
conformance check away from working, not a `rows.py` edit away.

**One calendar normalisation moved into the source**, per `federation.md` §2 ("units, datums and
calendars normalize at the source boundary"): USDM dates a release as an ISO `YYYY-MM-DD` *string*
(`valid_date`), and `rows.py` used to call `date.fromisoformat` on it — the lane parsing the
source's spelling. `ingest/usdm.py::DroughtRelease.release_day` now does that conversion, and
`valid_date` stays as USDM's own field for the fetch/parse path that speaks in it. Byte-identical
output: `_require_tuesday` already proved the string round-trips as canonical ISO.

**Still not normalized**, stated rather than hidden — nothing in the release payload itself, but
two source names survive elsewhere in this lane:

- `adapter.py` imports `ingest.usdm.usdm_source_url` to build the governed-absence marker's
  `requested_url` and the unsettled refusal's message. That is lane logic naming one source's
  module. The fix is a member on the source (the URL a day WOULD have been read from), not a cast;
  it is a separate push because it changes an absence marker's payload, which is stored evidence.
- `products.py` and `pipeline/validation/drought.py` import `ingest.usdm_history.usdm_release_weeks`
  directly, so the *calendar* half of the protocol (`release_days`) is bound-resolved only for the
  pull, not for the walk (W5-C's own residual note).

## Wave-4 deletion list

- `source.py` — a deprecation shim re-exporting `DroughtDaySource`, `DroughtSourceError` and
  `fetch_drought_day` from `usdm.py`. `forward.py` no longer imports it (2026-09-18, S5 fix): it
  resolves the fetch through `pipeline/source_bindings.py::resolve_drought_source()`, which reads
  the region's OWN binding per call rather than naming `usdm.py`, so a second region binding a
  different drought source changes this lane by editing a manifest, not this file. The shim's
  terminal step is therefore NOT "repoint every importer at `usdm.py` directly" — that would be
  the opposite of resolving through the binding — it is deleting the shim once
  `tests/direct/test_drought_adapter.py` is the last importer left, which it does not have to be
  bound-resolved (it constructs `DroughtDaySource` fixtures directly, never through the manifest).
