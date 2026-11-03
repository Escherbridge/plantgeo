# Offline regional bundles

This increment prepares a reproducible **inactive** deployment. It does not complete the
multi-layer US bootstrap goal or authorize any custom source. Every requested core layer is
reported; unsupported sources and policies are never silently dropped. Water can currently
produce a real, disabled config-runner lane and provider TOML. Weather, FIRMS, soil, vegetation
and climate retain explicit implementation blockers. No command here opens a provider, database
or object-store connection, creates a namespace receipt, starts jobs or changes activation.

## Commands and exit status

From the service directory, use `agri-service data region-bootstrap --config deployment.toml
--output deployment` and `agri-service data region-preflight --bundle deployment`.
Both return 78 while custom production admission remains blocked. Compilation can succeed in
writing a useful bundle while reporting `production_ready: false`; exit zero would be misleading.
The output parent must exist. An identical existing bundle is accepted unchanged; differing or
foreign files refuse without modification. There is no force-overwrite option.

Input is bounded TOML, with unknown fields refused and public validation errors sanitized.
Only credential environment-variable names belong here. Display labels and region definitions
are public, so never put secrets in them. The source names accepted by the diagnostic catalogue
are `water/usgs-water-data`, `weather/open-meteo`, `fire/nasa-firms`,
`soil/open-meteo-era5-land`, `vegetation/sentinel2`, and `climate/nasa-power`.

```toml
schema_version = 1
deployment_id = "colorado-pilot"

[region]
slug = "colorado-pilot"
display_name = "Colorado pilot"
bbox = [-108.0, 38.0, -106.0, 40.0]
timezone = "America/Denver"
admin_codes = ["US-CO"]

[isolation]
database_name = "colorado-pilot"
dedicated_database = true

[layers.water]
source = "usgs-water-data"
temporal_mode = "daily_archive"
credential_env = "USGS_WATER_DATA_API_KEY"
require_credential = false
[layers.water.history]
first_day = "2020-01-01"
last_day = "2020-12-31"
minimum_fraction = 1.0
[layers.water.budget]
forward_max_weighted_calls = 32
gap_fill_max_weighted_calls = 112
max_concurrency = 2
turn_timeout_seconds = 1800

[layers.weather]
source = "open-meteo"
temporal_mode = "current"
[layers.weather.budget]
forward_max_weighted_calls = 10
gap_fill_max_weighted_calls = 0
max_concurrency = 1
turn_timeout_seconds = 300
```

Budget numbers illustrate syntax, not recommended costs. Bootstrap caps calls at 10,000 per
mode, concurrency at 16 and turn duration at 3,600 seconds. Existing LaneBudget and
LaneCoverageTarget validators still apply. Omitted history last_day means the declared settled
edge, not an inferred denominator. Fractions below one explicitly report intentional gaps.
Optional corrections accept `mode="coupled_forward"`, `window_days`, and `days_per_turn`;
these share the existing forward cap. Independent correction caps or repair-day budgets are
reported unimplemented and generate no water lane. Every generated schedule remains disabled;
readiness records intended historical acquisition separately from activation.

## Artifacts and preflight evidence

`region.json` records proposed bindings, not admitted sources. Both public and service manifests
are the exact same canonical JSON. `*.env.json` files are JSON maps, not shell scripts: apply
`service.env.json` with the relevant `reader.env.json` or `executor.env.json`, and apply
`web.env.json` to the web build/runtime. Empty values are placeholders, never copied credentials.
The bundle must be mounted/copied to `/app/regional-bundle`; the current Dockerfile does not
automatically include operator-generated files. Keep reader and executor database credentials
in their respective service roles. `AGRI_PARQUET_SERVICE_URL` is the web serving-plane URL.

`isolation.json` binds the exact geography-derived object prefix and the declared dedicated
database name. Changing history reuses geography and proposed support; changing regions changes
both. Water support describes actual request tiles and two completeness categories per tile,
not a discovered gauge inventory or measured coverage. Its proposal digest is not an admitted
publication proof. Work costs beyond those request units remain unestimated before admission.

Preflight validates file hashes, deterministic recompilation under current source templates,
actual lane/provider schemas, exact manifest/environment agreement and credential presence.
Checksums prove local consistency, not authenticity. Database URLs are parsed only to compare
their declared target name; isolation and authentication remain explicitly unverified. Optional
USGS credentials can report `anonymous_allowed`; required keys report `missing_required_credential`.
Nonempty values report `present_unverified`, never valid authentication. Online provider checks
are deliberately not implemented. Reports never contain environment values or DSNs.

Input and each artifact are capped at 65,536 bytes; aggregate output is capped at 512KiB and
32 files. Embedded canonical manifests also obey the runtime manifest limit. Reads reject
symlinks and bounded scans stop at 64 entries. State masks/presets and national partitions are
not implemented. US country/admin declarations are operator claims, not boundary certification;
wide or antimeridian footprints refuse supported acquisition rather than defaulting to PNW.

This package owns orchestration above foundation. Foundation imports no execution/pipeline code.
The remaining source-admission and direct-command gates must land before any activation output
can become executable, followed by non-PNW multi-layer and cross-region live evidence.


Validated provider endpoints remain immutable in ProviderConfig. Artifact rendering serializes
ordinary provider fields separately from each validated endpoint; it never weakens the model's
immutable mapping to make JSON serialization work. Input models retain runtime annotations for
their imported Pydantic submodels. The compiler is an execution domain subject to the existing
sibling-domain import isolation rule, not a shared-package exemption.
