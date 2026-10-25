# `pipeline/lanes/land_context/` — the land-context layer package

Moved verbatim from `pipeline/direct/land_context/` by `p4-extract` (track `config_driven_ingestion_20260926`,
Phase 4): only import lines changed, and each legacy file is now a re-export shim listed in
`DEPRECATED_ALIASES.md` (deleted at 7D-n of the supersession cleanup lane). The rationale for each
rule still lives in each module's own docstring and in `pipeline/direct/land_context/AGENTS.md`.
The `s-land-context` swarm slice (this note) built `blm_arcgis.py` beside these modules.

| module | what |
|---|---|
| `products.py` | the BLM ArcGIS products, archive root and history floor |
| `source.py` | the bounded BLM capture and immutable replay |
| `source_protocol.py` | `LandContextSource` and `BLM_LAND_CONTEXT_SOURCE` for `pipeline/source_bindings.py` |
| `blm_arcgis.py` | `s-land-context`: the dark `land-context-blm-*` lanes' strategy (`land_context.blm_arcgis`) |

Imports: `foundation`, `ingest`, `warehouse`, `pipeline/parquet` and this package only (plus `pipeline/errors.py`, a residual the strategy should drop).
Never another layer's `pipeline/lanes/<layer>/` and never `pipeline/direct/**`
(`tests/test_direct_extraction_shims.py`, `tests/test_layer_import_contract.py`).
**Monkeypatch the module here, never its shim**: patching the shim rebinds only the shim's name.

`pipeline/direct/land_context/watermark.py` did NOT move: it was not in the task's move list, and
`lane_registry.py` still imports it from `pipeline/direct/` (a Phase-7 residual).

## `blm_arcgis.py` and the three dark TOMLs (`s-land-context`, class E shadow, spec.md §4.8.1)

`lanes/land-context-blm-{forward,reconcile,backfill}.toml` all ship `enabled = false` and
`strategy = "land_context.blm_arcgis"` (one shared `STRATEGY` instance, `BlmArcgisStrategy`). Three
files, not one, because `execution/lane_specs.py`'s `_REFERENCE_DATA_SPECS` already registers three
separate legacy lane ids for this source (`LAND_CONTEXT_FORWARD_LANE_ID`, `_RECONCILE_LANE_ID`,
`_BACKFILL_LANE_ID`, one per legacy `--mode`), and `tests/execution/test_lane_catalogue.py`'s pinned
`LEGACY_LANE_IDS` already names those exact three strings — the file stems match them on purpose.

**`executor = "legacy"`, not `"config"` — load-bearing.** `execution/lane_catalogue.py::build_lane_catalogue`
takes a lane id OFF the legacy path the instant a TOML with that id loads with `executor = "config"`,
*regardless of `enabled`* (it is filtered out of `legacy_specs` before `enabled` is ever read). Since
this source's class is "E: shadow … clock start … never (Q-21)" (spec.md §4.8.1) and the mapping from
the legacy `refresh`/`reconcile`/`backfill` modes onto the runner's `forward`/`gap-fill` turn modes is
explicitly "decided at activation" (spec.md §7, O7), `executor = "config"` here would be a silent,
premature cut-over with no reviewed behaviour behind it. `executor = "legacy"` keeps these three ids
exactly where `lane_specs.py` already has them; the TOML is inert configuration data until an
activation slice flips it (`execution/AGENTS.md` "Lane catalogue": "`executor = "legacy"` leaves the
lane where it was, and names a legacy spec or is quarantined").

**Stream ownership.** Only `land-context-blm-forward.toml` declares `[[streams]]` (`land-context-boundaries`,
`-offices`, `-contacts`, matching `pipeline/parquet/lane_registry.py`'s registration exactly); S18
forbids a slug on two lane files, and which of the three legacy duties would actually own writing is
part of the same deferred activation decision, so reconcile/backfill declare none.

**TIGERweb stays off this strategy.** `products.py::PRODUCTS` still admits `CENSUS_STATES`
(`tigerweb.geo.census.gov`), a different publisher with no provider file — `lanes/_providers/blm.toml`'s
own header says so. `blm_arcgis.PLANNED_PRODUCTS` is `(SURFACE_ORWA, SURFACE_IDAHO, FIELD_OFFICES)`
only; the TIGERweb state-context query remains a `pipeline/direct/land_context` residual, never a
guessed fourth provider file.

**`plan_requests`/`fetch` are real, `settle`/`rows` are not.** `plan_requests` builds the same
object-ID inventory ask `source.py::capture_product`'s first call does for each admitted service
(tested in `tests/lanes/land_context/test_blm_arcgis.py` against `products.py`'s own `where`/`envelope`
values, no restated literals); `fetch`'s `_object_ids` applies the same FOUR refusal shapes
`source.py` does, across TWO of its functions — `CaptureSession.query`'s JSON-readability, `"error"`
and `exceededTransferLimit` checks (`source.py:90-96`), then `object_ids`'s list-shape, duplicate and
record-ceiling checks (`source.py:99-105`) — restated rather than imported, same reason as above.
`settle`/`rows` raise `NotImplementedError` naming the deferred decision — a guessed stream/turn-mode
mapping would be unreviewed behaviour, which is worse than an honest gap given the method-presence-only
check `tests/lane_config/test_lane_toml_contract.py::lane_contract_violations` runs.

**`probe_edge` does not exist here either, and the loader's silence about that proves nothing.**
`pipeline/runner/turn.py`'s own `_validate` demands `EdgeProbingStrategy` for ANY `static_lookup` lane
unconditionally: `if lane.nature == "static_lookup" and not isinstance(ports.strategy,
EdgeProbingStrategy): raise TurnConfigurationError(...)` (`turn.py:277-278`). That is a DIFFERENT,
stricter gate than `foundation/lane_config/models.py::lane_requires_probe_edge`, which the loader and
`tests/lane_config/test_lane_toml_contract.py::lane_contract_violations` actually run, and which
returns `False` outright once `not provider.weighted` — true for the unweighted `blm` provider, so none of these three
TOMLs is ever flagged for a missing `probe_edge` today. Passing the contract test therefore proves
nothing about whether a real turn could run: flip any of these three TOMLs to `executor = "config"`
and the loader/contract test would still pass, then the FIRST real turn would fail closed with
`TurnConfigurationError`, because `BlmArcgisStrategy` implements no `probe_edge`. What this
snapshot-capture source's watermark even IS — it has no natural per-day structure for a probe window
to read — is bound up in the same deferred activation decision as `settle`/`rows` above, not a
separate gap to close in isolation.
