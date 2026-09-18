# `pipeline/direct/burn_severity` — the burn-severity lane

Direct-to-Parquet burn-severity writer. The capture, snapshot and replay rules for this lane are
argued in `pipeline/direct/AGENTS.md` ("MTBS immutable current captures"); this file covers only
the source protocol the federation standard adds.

## The source protocol

`source_protocol.py` is this layer's contract (`federation.md` §2, `layer-lanes.md` §1b): what a
burn-severity source must declare (`source_slug`, `coverage`), which days it has governed evidence
for (`release_days`, `ignition_years_by_release_day`) and how one release day is read
(`fetch_release_day`). `mtbs.py` is the pilot's one implementation; another region binds its own
national burn-severity programme without `rows.py`, `adapter.py`, `forward.py`,
`warehouse/schemas/burn_severity.py` or `planes/burn_severity.py` learning a source name.

### Why this layer's pull signature differs from drought's

`fetch_release_day` takes an **ignition-year cohort** and a **bounding box**, which the drought
protocol has neither of. That is not an MTBS leak: a burn-severity release publishes a fire year,
not a calendar day, so "which day" alone does not identify what to pull — `products.py`'s
release-day-to-cohort mapping is layer truth, not source truth, and any national burn programme
publishes on the same shape. The envelope is the region manifest's, passed in by the caller
(`layer-lanes.md` §1b), which is exactly why it is a parameter here and not a module constant.

`release_days()` takes no window, unlike drought's calendar walk: the governed set grows only when
a human dates a fire year's completion, never through the passage of time, so returning it entire
is honest and cheap. See `products.py::governed_release_days` for the full argument.

`BoundingBox` is re-declared in `source_protocol.py` rather than imported from `ingest/mtbs.py`,
which also defines it. A layer contract naming one source's module would be the source-name branch
the standard forbids, one level up. The two aliases collapse when the shared alias moves down into
`foundation`, which is wave-4 work.

### What is NOT normalized yet

`BurnSeverityReleaseDay.records` is typed `tuple[object, ...]`: what `mtbs.py` returns is still
`ingest.mtbs.MtbsBurnSeverityRecord`, MTBS's own record shape. Same named debt as the drought
lane's `release` field — the normalized record belongs in `warehouse/schemas/burn_severity.py` and
moving `rows.py` onto it is a later push.

## Wave-4 deletion list

- `source.py` — a deprecation shim re-exporting `BurnSeverityDaySource`, `BurnSeverityFetchError`
  and `fetch_burn_severity_release_day` from `mtbs.py`. `forward.py` no longer imports it
  (2026-09-18, S5 fix): it resolves the fetch through
  `pipeline/source_bindings.py::resolve_burn_severity_source()`, which reads the region's OWN
  binding per call rather than naming `mtbs.py`, so a second region binding a different
  burn-severity source changes this lane by editing a manifest, not this file. The shim's terminal
  step is therefore NOT "repoint every importer at `mtbs.py` directly" — that would be the
  opposite of resolving through the binding — it is deleting the shim once
  `tests/direct/test_burn_severity_direct_adapter.py` is the last importer left, which it does not
  have to be bound-resolved (it constructs `BurnSeverityDaySource` fixtures directly, never
  through the manifest).
