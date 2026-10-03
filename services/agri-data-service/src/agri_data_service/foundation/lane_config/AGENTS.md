# `foundation/lane_config`

Typed configuration for config-driven ingestion (spec §4.1). Authoring rules for the TOML files are
in `services/agri-data-service/lanes/AGENTS.md`; this file explains the code.

## Why it sits in `foundation` (ruled exception)

Spec §4.1 places the schema here so every layer that needs a lane's facts — the runner
(`pipeline/runner/`), the executor (`execution/`), serving (`interface/`) — can import it without a
lattice violation. Like `foundation/region/`, it fails admission criterion 4 (a domain noun) and
does I/O (the loader reads files), and is a ruled exception on the same grounds. It imports only
`pydantic`, stdlib and other `foundation` packages (`region`, `parquet`), satisfying criteria 1–2 and
`tests/test_layer_import_contract.py`.

## Modules

- `models.py` — frozen, `extra="forbid"` Pydantic models. `ProviderConfig` (endpoints with free and
  customer hosts, `api_key_env` NAME, optional `time_standard`, `[weight]` iff `weighted`, optional
  `[budget]`); `LaneConfig` with `[source]`, `[schedule]`, `[days]`, `[budget]`, `[pruning]`,
  `[[streams]]`. Single-file invariants live here as validators; `lane_requires_probe_edge` is the
  S6/S19 predicate.
- `cron.py` — the in-house 5-field UTC grammar (S9): `parse_cron` expands each field to the set of
  values it fires on. It has no clock. `execution/cron_schedule.py` (`f1-executor`) owns next-fire
  and due computation and should build on `CronExpression` rather than re-parse, so the executor and
  the loader can never disagree about what a cron string means.
- `loader.py` — `load_lane_configs(directory, region) -> LaneConfigSet`, plus
  `default_lanes_directory()`.

## Loader

Provider endpoints may declare `optional_api_key` only without a customer host, with
`api_key_env` and the key's name for the provider's `api_key_transport`: `api_key_parameter` for
`query` (the default; restricted to the redactor's `apikey` and `api_key` spellings) or
`api_key_header` for `header` (restricted to `X-Api-Key`). Exactly one name matches the transport:
a header transport refuses `api_key_parameter`, and a query transport refuses `api_key_header`. The
transport is data so a provider file, not a host check in `ingest/provider_client.py`, decides where
a key travels (post-push review M2). This makes optional same-host authentication explicit without
weakening the required customer-host key rule. The endpoint opt-in scopes credential use; other
endpoints on the provider remain anonymous.

Lazy by construction: nothing reads a file until it is called (the manifest-moves-must-be-lazy
rule; `LANE_REGISTRY` and friends are read at import, so nothing there may call this at import).

- **S8.** A lane that fails any rule lands in `quarantined` with every reason; the rest load. Only a
  missing directory raises (`LaneDirectoryError`): that is a packaging fault for every lane, and it
  must be loud (CA4) rather than look like an empty catalogue.
- **Identity is the file stem.** A declared `id` must equal it, which makes ids unique and keys a
  broken file's quarantine under its own name, never under a sibling's id.
- **Cross-file rules**, in order: provider/endpoint existence, lattice-in-region, coverage contains
  region (via `foundation/region/source_coverage.py::SourceCoverageClaim`); `conflicts_with`
  symmetry (a partner whose own file failed to parse is already quarantined and does not cascade);
  one owner per stream slug; then `inputs` resolution by Kahn's algorithm — a lane whose inputs are
  missing or quarantined is quarantined, and whatever never resolves is a cycle or downstream of one.
  **An asymmetric `conflicts_with` quarantines only the declaring lane** (`_asymmetric_conflicts`,
  S8): stopping the declarer alone already keeps the pair from co-running, so the partner, whose own
  file is fine, keeps running (review finding 4, f1-config; pinned by
  `test_loader.py::test_conflicts_with_must_be_declared_on_both_sides`). **A duplicate stream slug
  quarantines every claimant** (`_duplicate_streams`): no claimant is the declarer, and any one left
  running could write the stream, so the loader refuses to pick an owner.
- A provider file that fails quarantines only the lanes on it.
- **Strategy resolution (S14) and `probe_edge` are not checked here**: `foundation` may not import
  `pipeline`. `LaneConfig.strategy_module` states the module; the contract test and
  `pipeline/runner/resolve.py` import it.

## Directory setting

`PLANTGEO_LANES_DIRECTORY` overrides; unset, the directory is `lanes/` beside `src/`, computed from
this file's path. That is `services/agri-data-service/lanes` in the repo, `/app/lanes` in the
service image and `/app/agri-service/lanes` in the executor image, because both images install the
project editable from `src/` and COPY `lanes/` next to it.
`tests/lane_config/test_image_plumbing.py` pins those COPY lines.
