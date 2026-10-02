# `lanes/` — lane configuration as data

Config-driven ingestion (track `config_driven_ingestion_20260926`, spec §4.1, owner decision D1):
**one TOML per lane**, typed and validated at startup and by the contract test. Config holds DATA;
the strategy module a lane names holds BEHAVIOUR. Schema and loader:
`src/agri_data_service/foundation/lane_config/` (see its `AGENTS.md`).

## Layout

| path | what | loaded as |
|---|---|---|
| `<lane-id>.toml` | one lane; the file stem IS the lane id | `LaneConfig` |
| `_providers/<provider-id>.toml` | facts every lane on one upstream shares (spec S3) | `ProviderConfig` |
| `AGENTS.md` | this file; not loaded | — |

Only top-level `*.toml` files are lanes. `_providers/` holds `open-meteo.toml` (archive, forecast and
historical-forecast, each with a keyed customer host; weighted; the paid monthly budget),
`nasa-power.toml` (keyless, unweighted, `time_standard = "UTC"`) and `usgs-water-data.toml` (optional key,
unweighted; the `daily` and `monitoring-locations` collections; `water-gauges-daily`, spec §7a).

## Rules a lane file must satisfy

The loader quarantines a lane that breaks any of these; it never stops the process (spec S8). The
contract test `tests/lane_config/test_lane_toml_contract.py` fails the sweep when anything in this
tree is quarantined.

- `id` equals the file stem, so ids are unique by construction.
- `strategy = "<layer>.<source>"` resolves to `agri_data_service.pipeline.lanes.<layer>.<source>`,
  attribute `STRATEGY` (S14). There is no registry to edit.
- `grid` names an analysis lattice in the active region manifest (`analysis-0p25` in the pilot),
  never literal numbers. A region without that lattice quarantines the lane there, not elsewhere.
- `[source]` names a provider file and one of its endpoints; its `coverage` (with ISO country codes
  when regional) must contain the region. No bbox, envelope or lat/lon anywhere in a lane file —
  footprints live in the region manifest.
- `absence_recheck_days > publication_lag_days`.
- `revision_window_days` (optional) `> absence_recheck_days`: each forward turn also re-asks one
  `revision_days_per_turn` block of published days that far behind the edge, for revisions that land
  after the forward window (USGS approvals). Cost: one more set of units per forward turn, so size
  `forward_max_weighted_calls` for both. See `pipeline/runner/AGENTS.md` "Windows".
- **Gap-fill and pruning ship off (S12).** Turning one on is a flip inside a named owner gate: set
  `gap_fill_enabled = true` with `gap_fill_enabled_at_gate = "G6"` (or `[pruning] enabled` with
  `enabled_at_gate`), and add the lane id to the pinned set in the contract test in the same commit.
  A rollback is the one field back to `false`.
- A settled (`partial_day = "refuse"`) lane on a weighted provider whose forward cron fires more than
  once a UTC day must have an async `probe_edge` on its strategy (S6, S19). `write_and_recheck`
  lanes are exempt.
- `inputs` (transforms only) exist and form a DAG; `conflicts_with` is declared on both sides; every
  `[[streams]]` slug belongs to exactly one lane (S18).
- Crons are 5-field numeric UTC: `*`, `a`, `a-b`, `*/n`, `a/n`, `a-b/n`, lists. No names or macros.

## Provider files

- `api_key_env` holds the NAME of an environment variable ending in `_KEY`, never a key. The owner
  sets the variable; a customer host is only declared on a provider that names one.
- `api_key_parameter` names an already-redacted query spelling (`apikey` or `api_key`). An endpoint
  opts into an optional same-host key with `optional_api_key = true`, requiring both declarations.
  Missing or blank keys remain anonymous; customer hosts still require their key and cannot opt in.
  USGS reads `USGS_WATER_DATA_API_KEY` for both collections. The owner supplies its value; no key is
  provisioned by this change. Official contract: https://api.waterdata.usgs.gov/docs/ogcapi/keys/.
- Every host must resolve in `foundation/observability/usage.py::provider_for_host`, or the usage
  report shows `provider None` for it; `tests/lane_config/test_provider_hosts.py` enforces this. Add
  the host rule in the same commit as the provider file.
- **Budget (WQ-4):** only the paid monthly Open-Meteo quota is enforced: 5,000,000 weighted calls,
  gap-fill admitted while charged + suspect + the turn cap stays at or under 60 %, forward stopped at
  95 % of charged spend. The quota is charged only through customer hosts (`charged_pool`). Windowed
  pools and reserves were declined; the schema refuses them.
- The weight rule (`locations × models × max(1, days/14) × max(1, variables/10)`, spec §6.3) prices a
  request the same way `usage.open_meteo_request_weight`/`open_meteo_weight_for_url` do for
  `locations`, `days` and `variables`; the host test pins that. **The `models` factor** is priced by
  `open_meteo_weight_for_url` alone: `open_meteo_request_weight` keeps its pinned three-argument
  signature (it is byte-identical to G0's soil request builder, which never sends more than one
  model), and `open_meteo_weight_for_url` multiplies that pinned weight by the URL's own `models=`
  item count instead — so `counts_models = true` here now agrees with the meter end to end,
  including for a real customer-host URL carrying `models=a,b` (review finding 3, RESOLVED by
  `f1-providers`; `tests/lane_config/test_provider_hosts.py`'s `models=2` case and
  `tests/foundation/observability/test_usage.py::test_open_meteo_weight_table`'s `models=` rows both
  pin it).
- NASA POWER is asked for UTC days only: an LST day shifts every daily value by the local offset,
  which reads as a data regression that is not one.

## Every change here needs a sweep

`lanes` is in `scripts/quality_receipt.py::DIGEST_DIRECTORIES`, and both images COPY this directory
into their receipt stage and beside `src/` in their runtime stage (S13, CA4). So every TOML edit —
a rollback flip included — needs the monitor's sweep and a receipt refresh before an image builds.
