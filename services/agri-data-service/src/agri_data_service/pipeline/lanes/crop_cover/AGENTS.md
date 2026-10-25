# `pipeline/lanes/crop_cover/` — the crop-cover layer package

Moved verbatim from `pipeline/direct/crop_cover/` by `p4-extract` (track `config_driven_ingestion_20260926`,
Phase 4): only import lines changed, and each legacy file is now a re-export shim listed in
`DEPRECATED_ALIASES.md` (deleted at 7D-n of the supersession cleanup lane). The rationale for each
rule still lives in each module's own docstring and in `pipeline/direct/crop_cover/AGENTS.md`.
The `s-crop-cover` swarm slice built the config-lane strategy beside these modules (`usda_cdl.py`)
and, as its own Wave O item, moved `source.py`'s two raw `httpx.Client` sends onto the metered
factory; see that module's own history for the "why" rather than restating it here.

| module | what |
|---|---|
| `products.py` | the CDL service, grids, crop codes and the region's capture envelope |
| `source.py` | the bounded CDL capture, sent through `ingest/http.py`'s metered `upstream_sync_client` (Wave O; no raw-client allow-list entry) |
| `source_protocol.py` | `CropCoverSource` and `USDA_CROP_COVER_SOURCE` for `pipeline/source_bindings.py` |
| `usda_cdl.py` | the `crop_cover.usda_cdl` config-lane strategy (`crop-cover-usda-maintain.toml`, dark); proves edition identity only, never a tile |

Imports: `foundation`, `ingest`, `warehouse`, `pipeline/parquet` and this package only (plus `pipeline/errors.py`, a residual the strategy should drop).
Never another layer's `pipeline/lanes/<layer>/` and never `pipeline/direct/**`
(`tests/test_direct_extraction_shims.py`, `tests/test_layer_import_contract.py`).
**Monkeypatch the module here, never its shim**: patching the shim rebinds only the shim's name.

## `usda_cdl.py` and the dark TOML (`s-crop-cover`)

`lanes/crop-cover-usda-maintain.toml` ships `enabled = false` and `strategy = "crop_cover.usda_cdl"`
(one shared `STRATEGY` instance). It proves only **edition identity** (spec §7's settle semantics
for this lane) from the USDA catalogue query `source.py::capture()` already makes once per year;
the bulk multi-gigabyte tile capture stays `capture()`'s own job, run today only by the legacy
`pipeline/direct/crop_cover/forward.py` `maintain` operator.

**`executor = "legacy"`, not `"config"` — load-bearing.** `execution/lane_catalogue.py::build_lane_catalogue`
takes a lane id OFF the legacy path the instant a TOML with that id loads with `executor = "config"`,
*regardless of `enabled`* (it is filtered out of `legacy_specs` before `enabled` is ever read:
`lane_catalogue.py:292-304`). This id is a REAL legacy lane — `execution/lane_ids.py:26`'s
`CROP_COVER_MAINTENANCE_LANE_ID = "crop-cover-usda-maintain"` names a dispatchable spec in
`execution/lane_specs.py:859-884` (command, `0 10 * * *` daily cadence, `timeout_seconds = 3600 +
COMMAND_CLEANUP_MARGIN_SECONDS = 3900`), and `tests/execution/test_lane_catalogue.py`'s pinned
`LEGACY_LANE_IDS` names this exact string — so `executor = "config"` here would silently de-path the
live legacy maintain cron with nothing behind it (this TOML ships `enabled = false`): the exact
failure mode, not a hypothetical. `executor = "legacy"` keeps this id exactly where `lane_specs.py`
already has it; the TOML is inert configuration data until an activation slice flips it
(`execution/AGENTS.md` "Lane catalogue": "`executor = "legacy"` leaves the lane where it was, and
names a legacy spec or is quarantined", `execution/AGENTS.md:349-351`). Same mechanism, same note
shape as `pipeline/lanes/land_context/AGENTS.md`'s own "`executor = "legacy"`, not `"config"` —
load-bearing" paragraph for its three legacy lane ids.

**`_runner_scratch_dir()` answers "where", never "how".** It names a portable (never a literal
`/tmp`) local scratch root for a future activation's capture wiring, but does not — and does not
attempt to — answer how a config-lane `fetch()` would drive the existing 14,400-second / 4-GiB
regional tile capture (`source.py::CaptureConfig`, `MAX_CAPTURE_SECONDS`, `MAX_CAPTURE_BYTES`) inside
this TOML's own `turn_timeout_seconds = 300`; that mismatch is itself part of the deferred
activation decision, not something this strategy resolves.
