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

### Path templates

An endpoint may declare `path_template` beside its fixed `path` when the upstream resource is a
per-request PATH segment (a USDM release `/data/json/usdm_{release}.json`; a FIRMS window
`/api/area/csv/{api_key}/{source}/{area}/{day_range}`). Syntax: literal `[A-Za-z0-9/_.-]` plus
`{name}` placeholders, `name` a lowercase identifier. The template must extend `path` (so `path`
stays the endpoint's stable prefix for anything that reads it), name at least one placeholder,
name none twice, and carry no empty or dot segment; any other `{`/`}` is an unclosed or stray
brace. Every one of these is refused at LOAD (`PathTemplateError`, a `ValueError`, so the provider
file lands in `provider_failures` and only its own lanes quarantine), never discovered as a
malformed URL at send time.

There is no separate path-parameter seam: a strategy puts the values in `SourceRequest.parameters`
under the placeholder names, and `ProviderEndpoint.split_parameters` routes them into the path and
the rest into the query. That is deliberate: `pipeline/runner/budget.py` (`request_key`,
`BudgetedClient`) and `checkpoints.py::_ReplayClient` key a logical request on
`(endpoint, parameters)`, so two releases stay two requests and a replay matches its send with no
change to the `ProviderClient.get(endpoint, parameters)` Protocol or either wrapper. At request time
`fill_path_template` refuses a missing value, and an empty, `.`, `..`-bearing, `/`- or
backslash-bearing value; it URL-path-escapes the rest with RFC 3986 `pchar` minus `/` (FIRMS' bbox
commas survive; `?` and `#` are escaped, so a value can never open a query or fragment). Refusals
name the placeholder, never the value. A templated endpoint sends no bare `?` when it has no query,
so a USDM URL equals `ingest/usdm.py::usdm_source_url` byte for byte.

**`{api_key}` is reserved** and only the provider client fills it, from `api_key_env`. It needs
`api_key_transport = "path"` (no `api_key_parameter`/`api_key_header`), and it pairs one-to-one
with the endpoint's `customer_host` opt-in: a `{api_key}` template without `customer_host`, a
`customer_host` without `{api_key}`, or `optional_api_key` under path transport (an optional key
cannot fill a required segment) all fail the load. `customer_host` may equal `host`: it is the
REQUIRED-key opt-in, which is FIRMS' shape (one host, key mandatory), so an empty key is FR-2's
named refusal. An endpoint with neither keeps the m3 request-time refusal
(`ingest/provider_client.py`), extended from query to path transport.

Lazy by construction: nothing reads a file until it is called (the manifest-moves-must-be-lazy
rule; `LANE_REGISTRY` and friends are read at import, so nothing there may call this at import).

- **S8.** A lane that fails any rule lands in `quarantined` with every reason; the rest load. Only a
  missing directory raises (`LaneDirectoryError`): that is a packaging fault for every lane, and it
  must be loud (CA4) rather than look like an empty catalogue.
- **`volatile_columns`** (per `[[streams]]`): capture-time columns G6 `--compare` ignores. Checked only
  when the caller injects `stream_contract` (production does; `foundation` cannot import the warehouse
  itself) — see `pipeline/runner/AGENTS.md` "Volatile columns".
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
