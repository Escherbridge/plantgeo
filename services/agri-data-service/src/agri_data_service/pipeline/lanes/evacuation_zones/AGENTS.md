# `pipeline/lanes/evacuation_zones/` — the evacuation-zones layer package

Moved verbatim from `pipeline/direct/evacuation_zones/` by `p4-extract` (track `config_driven_ingestion_20260926`,
Phase 4): only import lines changed, and each legacy file is now a re-export shim listed in
`DEPRECATED_ALIASES.md` (deleted at 7D-n of the supersession cleanup lane). The rationale for each
rule still lives in each module's own docstring and in `pipeline/direct/AGENTS.md`, "Evacuation zones".
The `s-evacuation-zones` swarm slice built the config-lane strategy (`oregon_oem.py`) beside these
modules; see "`oregon_oem.py` design notes" below for the design decisions unique to a
content-watermarked `static_lookup`, and `docs/lanes/evacuation-zones.md` section 8 for the
lane-level summary.

| module | what |
|---|---|
| `products.py` | identity constants and the Oregon-only coverage gate; deliberately NO edge to `LANE_REGISTRY` |
| `source.py` | the bounded Oregon OEM fetch (`fetch_evacuation_zones_snapshot`) |
| `support.py` | DuckDB geometry repair to WKB (`_shared/geometry.py`'s `"pass_through_flagged"` mode) |
| `rows.py` | the Arrow table, natural keys, content digests and part splitting |
| `watermark.py` | the `static_lookup` source watermark `lane_registry.py` imports |
| `oregon_oem.py` | the config-lane strategy (S14, `evacuation-zones-direct-forward.toml`); the only module here that imports `pipeline/runner/contract.py` |

Imports: `foundation`, `ingest`, `warehouse`, `pipeline/parquet` and this package only. `oregon_oem.py`
also imports `pipeline/runner/contract.py` (the strategy Protocols, per S14 the one runner module a
strategy may import) and never touches `pipeline/constants.py::LANE_BASE_ZOOM_TIER` — the generic
`ObjectStoreLaneWriter` picks the zoom tier, so the strategy has no reason to.
Never another layer's `pipeline/lanes/<layer>/` and never `pipeline/direct/**`
(`tests/test_direct_extraction_shims.py`, `tests/test_layer_import_contract.py`).
**Monkeypatch the module here, never its shim**: patching the shim rebinds only the shim's name.

`lane_registry.py` imports `watermark.py`, which reaches `products`, `rows`, `source` and `support`.
None of them may import `LANE_REGISTRY`; the registration stays in
`pipeline/direct/evacuation_zones/registration.py`.

## `oregon_oem.py` design notes

Moved here from that module's own docstrings (which now carry only a 1-2 line pointer to each
heading below).

### Why `probe_edge` does the whole statewide capture

Every other `static_lookup` this track ships (watersheds' `max(loaddate)`, fire-perimeters'
`editingInfo.lastEditDate`) reads one cheap metadata field as its watermark, then `fetch`s the full
population only once the runner decides a new version is owed. Oregon OEM publishes no such field:
`created_date` never moves when a level is raised, and `last_edited_date` is re-stamped on unchanged
areas every few minutes (`watermark.py` module docstring; `rows.py` module docstring). The only
honest change test is a CONTENT DIGEST of the whole captured population, which means deciding the
watermark here REQUIRES the full capture `fetch` would otherwise make. `probe_edge` therefore does
that capture, decides the day from it (sticky at the published day when the digest agrees, otherwise
today's UTC capture date -- the same rule `forward.py` inlines rather than `watermark.py`'s
`watermark_for_capture`, see "Why this diverges from `watermark.py`'s `day=None`" below), and caches
it on `self._captured` so `fetch` (called only when the runner decides a new version is owed) never
re-asks Oregon a second time for the same statewide state a few seconds later -- the exact "two
captures of a fast-moving multi-fire event minutes apart" race `forward.py`'s own docstring explains
why a version's publish attempts never refetch.

### Why `fetch` never calls `client.get()`

Oregon OEM's real contract is `source.py::fetch_evacuation_zones_snapshot`'s adaptive, byte- and
page-bounded, oversized-record-governed walk (`ingest/evacuation_zones.py::fetch_evacuation_zones`),
already GL-2 metered through `ingest/http.py::upstream_client` -- not a single `SourceRequest` the
generic `ProviderClient.get(endpoint, parameters)` one-logical-attempt shape can express.
`source.py`'s own module docstring names the reason not to re-derive that walk: "Re-implementing it
would fork the 64 MiB per-run transfer budget, the 20-page ceiling and the `INGEST_MAX_SOURCE_RECORDS`
record ceiling." This strategy therefore calls the landed, already-metered function directly. The
residual cost named in `lanes/evacuation-zones-direct-forward.toml`'s `[budget]` comment: the
runner's own per-turn request ledger (`weighted_calls`/`requests`/`fetch_attempts` in the S5 report)
does not see these sends, because they never pass through the `BudgetedClient` wrapping `client`.
Oregon OEM is unweighted and keyless (no dollar budget at risk); `source.py`'s own ceilings are the
real cap. `client` is accepted on every Protocol method (and briefly handed to a restored checkpoint
replay, which this strategy also never reaches -- see "Checkpoints" below) purely to satisfy the
`IngestStrategy`/`EdgeProbingStrategy` Protocol shapes; it is never called.

### Why this diverges from `watermark.py`'s `day=None`

`watermark.py::watermark_for_capture` answers an EMPTY capture with `day=None` ("source_empty"),
because that module backs the legacy, absence-blind `resolve_static_lane` census, which predates
`Absent` settlements and treats a watermark with no day as a failed read to retry (its own docstring,
point 1). This strategy is the WRITE path, and the runner's `Absent` Settlement (`contract.py`) is
exactly the governed-absence machinery a quiet statewide day needs: a day the capture returns zero
areas for is a VALUED day (today's UTC capture date, or the published day when it repeats a quiet
spell already recorded), never a blind one. `forward.py` already made this same choice inline
(`version_day = source.fetched_at.date()` unconditionally, never routed through
`watermark_for_capture`); this module restates that choice because
`pipeline/lanes/evacuation_zones/**` may not import `pipeline/direct/**` (this package's `AGENTS.md`).

### How the quiet-day and ladder-absence semantics land on THIS runner's absence machinery

`settle()` returns `Absent(reason=..., proof=...)` when the capture holds zero areas; it does nothing
else. The ladder write (every zoom tier, coarse rungs first, base last) is
`writer.py::ObjectStoreLaneWriter.write_absence`
-> `pipeline/parquet/derivation.py::govern_day_absent`, not this package -- the generic runner writes
the WHOLE ladder for every `Absent` settlement, so this strategy needs no `_ABSENCE_LADDER_TIERS`
loop of its own (contrast `pipeline/direct/evacuation_zones/adapter.py::_govern_quiet_day`, which has
to do that by hand because the legacy driver has no generic absence writer). The "same-day
stand-down" refusal (`test_evacuation_zones_direct_adapter.py::
test_a_same_day_stand_down_is_refused_rather_than_deleting_a_published_snapshot`) is likewise generic:
`writer.py::_decide_unverified_rewrite`'s `Absent` branch refuses whenever `census.status(stream, day)
== "data"`, returning `"data_never_retracted_by_absence"` and never writing -- this strategy does not
re-implement that pre-flight at all; `turn.py::_Turn._write_absence` logs
`plantgeo_lane_turn_absence_over_data_refused` for exactly that case. A quiet day that repeats an
already-governed absence reads `"absence_unchanged"` the same way, also for free.

### Checkpoints

This strategy's `SourceRequest` carries no `requested_parameters`, so `SourceResponse.checkpoint_eligible`
is always `False` (`contract.py`: `bool(requested) and requested <= complete_parameters`, and
`bool(frozenset())` is `False`). `checkpoints.py::TurnCheckpoints.retain` therefore never persists a
capture, and `.restore` never replays one -- deliberately: a replayed capture would resurrect a stale
digest decision for a lane whose only correctness property is "the version decided this turn is the
state this turn actually saw."

### `OregonOemEvacuationZonesStrategy`: NOT frozen, and why

`_captured` is deliberately mutable, per-turn cache state, the one escape this strategy makes from
"thin strategy, fat runner" (D2); "Why `probe_edge` does the whole statewide capture" above states
exactly why it is structurally required rather than an optimisation. `store_factory` defaults to the
real bucket and exists so a test can hand this strategy an `ObjectStore(RecordingBackend())` without
monkeypatching a classmethod.

SAFETY DEPENDS ON ONE UNSTATED-ELSEWHERE INVARIANT this class does not itself enforce: one turn runs
in its own subprocess, so a fresh `python -m agri_data_service.pipeline.runner` invocation means a
fresh module import and a fresh `STRATEGY` instance, never a `_captured` left over from an earlier
turn (`execution/job_executor_service.py:5064`'s `asyncio.create_subprocess_exec`, spawning the
per-turn CLI call `execution/AGENTS.md:359` names). A future binding that ran turns inside one
long-lived process would need to reset `_captured` between them, which nothing here does.

### `_watermark_day`: sticky rule and clock-skew guard

Sticky at the published day when nothing changed; otherwise today's UTC capture date -- the rule
`forward.py` inlines (module docstring, "Why this diverges from `watermark.py`'s `day=None`" above),
restated here because that module is off-limits to import from this package.

CLOCK-SKEW GUARD, restated from `forward.py:252-261` (also off-limits to import): a published day
AHEAD of this capture's own day means some version was stamped ahead of this capture -- a skewed
host clock, or a hand-written future version -- and a day behind it would be invisible to every
later turn, which would then re-decide the SAME behind-schedule day and rewrite nothing, forever
(`served >= watermark` reading "current" with no calendar day ever catching up). Checked
UNCONDITIONALLY, before the sticky comparison, exactly as `forward.py` orders it.

The raise is NOT caught by `probe_edge`'s own `except (EvacuationZonesSourceError,
EvacuationZonesTruncatedError, EvacuationZonesWatermarkError)` -- `OregonOemStrategyError` is none of
those -- so it propagates out of `probe_edge` to `turn.py::_Turn._spend_probe`'s generic `except
Exception` handler. `fetch.py::classify_fetch_error` has no typed case for it, so it falls through to
`"strategy"`, which `_spend_probe` maps to `status="unavailable"` (never the `"config"` branch that
re-raises and ends the turn). That is a SOFTER outcome than legacy's uncaught
`DirectEvacuationZonesError` (which failed the whole process): today is reported
`unwritten`/`upstream_unavailable` and the turn exits 0, to be retried next tick -- and it will raise
again every tick until an operator fixes the host clock or retracts the future version, which is the
same pair of remedies the message below names.
