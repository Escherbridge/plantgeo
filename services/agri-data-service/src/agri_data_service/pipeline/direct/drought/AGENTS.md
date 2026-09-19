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

`DroughtAreaPayload` is the same treatment one level down: `drought_intensity_class: int` and
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

**Still not normalized**, stated rather than hidden. The earlier claim that *nothing* in the
release payload is un-normalised was wrong, and STYLE-REVIEW-W5 S1 is the correction:

- `drought_intensity_class` is a five-step `0..4` scale, and those five steps are USDM's. The
  member no longer carries the source's NAME (it was `drought_monitor_category`), and the protocol
  docstring now declares the scale as THE LAYER's with the mapping obligation on the source
  implementation — but a national monitor publishing three or six classes still has to squeeze onto
  five, which is a real conversion debt of the same family as `acres`. It clears when a source with
  a different class count is actually bound and the scale becomes either a declared per-source
  ladder or a continuous intensity with a class derived from it; until then the mapping is written
  in the source, never in the lane.

- **`dm_category`, the persisted spelling, is the same institution's name abbreviated.** The rename
  stopped at the in-memory member: `dm_category` is still the Parquet column
  (`warehouse/schemas/drought.py:62`), half the lane's grain tuple (`DROUGHT_GRAIN`, `:29`), the
  plane's answer field (`planes/drought.py`), a column in the agent tool's list (`agent/tools.py`)
  and the tRPC reader's zod key (`src/lib/server/services/parquet-trpc-readers/drought.ts`), and
  `rows.py:48` is the one line where it meets `drought_intensity_class`. It has not moved because a
  column rename is a Parquet schema change over every written partition plus a wire-contract change,
  where the in-memory member was free; `rows.py:48` now carries that reason inline. It clears under
  the same condition `acres` does -- the next schema version that rewrites this lane's partitions --
  and the layer contract, not the column, is authoritative meanwhile (STYLE-REVIEW-W6 S4,
  BACKLOG N34).

Two source names also survive elsewhere in this lane:

- `adapter.py` imports `ingest.usdm.usdm_source_url` to build the governed-absence marker's
  `requested_url` and the unsettled refusal's message. That is lane logic naming one source's
  module. The fix is a member on the source (the URL a day WOULD have been read from), not a cast;
  it is a separate push because it changes an absence marker's payload, which is stored evidence.
- `products.py` and `pipeline/validation/drought.py` import `ingest.usdm_history.usdm_release_weeks`
  directly, so the *calendar* half of the protocol (`release_days`) is bound-resolved only for the
  pull, not for the walk (W5-C's own residual note).

(`support.py:130`'s refusal used to say "USDM drought class D{n}" -- a source's name inside lane
logic, in the same file whose lane "no longer imports `ingest.usdm` at all". It now says "drought
intensity class D{n}", the layer's own words. `ingest/identity.py`'s surviving
`drought_monitor_category` and its `MIN_`/`MAX_DROUGHT_MONITOR_CATEGORY` constants have no
production caller at all and are dead code, not rename debt.)

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
