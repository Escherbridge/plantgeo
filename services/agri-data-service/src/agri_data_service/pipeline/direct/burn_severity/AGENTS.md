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

### The record payload, and what is still NOT normalized

**Retired 2026-09-18 (wave 5):** `BurnSeverityReleaseDay.records` was typed `tuple[object, ...]`,
which was honest about the debt and useless to the type checker — `adapter.py` handed that tuple to
`burn_severity_release_day_table`, whose signature named `ingest.mtbs.MtbsBurnSeverityRecord`, and
it only failed under `mypy --strict` once the type-only import was repointed at this protocol. It
is now `Sequence[BurnSeverityRecordPayload]`, a `runtime_checkable` Protocol in `source_protocol.py`
exposing **exactly** the fourteen members `rows.py` writes and `adapter.py`/`forward.py` count, and
nothing else: `producer_local_id`, `natural_key`, `release_identifier`, `mapping_revision`,
`ignition_year`, `ignition_date`, `data_available_at`, `fire_name`, `fire_type`, `assessment_type`,
`acres`, `severity_class`, `severity_thresholds` and `geometry`. MTBS's `producer` and `geom_kind`
are deliberately absent: nothing in the lane reads them, and a member nothing reads is a member the
next region's implementer has to fake.

`BurnSeverityThresholdsPayload` is the same treatment one level down — the seven `int | None` dNBR
thresholds `rows.py` flattens into columns. `rows.py` now takes the payload protocol and no longer
imports `ingest.mtbs` at all, and `support.py`'s repair takes `Mapping[str, object]` geometry rather
than `dict`, matching what the contract publishes.

`ingest.mtbs.MtbsBurnSeverityRecord` satisfies `BurnSeverityRecordPayload` **structurally**; nothing
inherits, and `tests/foundation/test_source_protocols.py` proves it with `isinstance` on a fixture
plus a fabricated non-MTBS record that also satisfies it.

**Still not normalized, stated rather than hidden:**

- **`acres` is not SI.** `federation.md` §2 asks for SI at the source boundary, and burned area
  crosses it in US survey acres because `warehouse/schemas/burn_severity.py` pins a column literally
  named `acres` that the serving plane, the agent tool and the web legend all read. Converting is a
  schema migration plus a client contract change, not a source-implementation fix, so the protocol
  states the unit in the member's own docstring instead of pretending. A metric national programme
  binding this layer today must convert m² → acres inside its own implementation; that is the wrong
  direction and it is written down here so the next author does not discover it by reading rows.
- **`ignition_date` carries no timezone.** It is the source's own fire-calendar date, which is a
  local civil date for every programme that publishes one. `data_available_at` IS UTC
  (`ingest/mtbs.py::build_mtbs_snapshot_record` refuses a non-zero offset), so the provenance clock
  is normalized and the fire calendar is not.
- **`severity_class` is typed `str | None`, not an enum.** The warehouse schema stores it as a
  nullable string (`warehouse/schemas/burn_severity.py`), so the layer's contract is a string; MTBS
  narrows it to its own `Literal` internally, which the read-only property permits.
- **The calendar half of the protocol is not bound-resolved.** `release_days()` /
  `ignition_years_by_release_day()` are still read from `products.py`, which imports `ingest.mtbs`
  directly; only `fetch_release_day` resolves through the region binding (W5-C's own residual note).

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
