# Configure historical data coverage

Historical ingestion remains scheduled. A config-driven daily archive lane can stop upstream
backfill requests once its configured **data coverage** target is reached, while continuing
forward refresh and its existing revision checks. Every historical invocation reassesses the
target, so lost data or changed spatial support can resume work. This is available first on
`water-gauges-daily`; it is not a global switch for legacy writers.

## US starting point

1. Start with the existing `pnw` region manifest and set `PLANTGEO_REGION=pnw` on the Python
   service and `NEXT_PUBLIC_PLANTGEO_REGION=pnw` on the web build, keeping the server's region
   configuration aligned. This is the existing Pacific Northwest deployment footprint, not
   all-US coverage. A different footprint requires a validated region manifest, matching web
   registration, source bindings and any analysis lattices its lanes use. Merely changing a
   region string or API key cannot create those contracts.
2. Supply object-store and database configuration through the existing deployment setup.
   For water gauges, `USGS_WATER_DATA_API_KEY` is optional: anonymous access is supported;
   a key increases the provider's allowance. Other lanes may require credentials, and their
   provider configuration must reject missing required credentials rather than substitute a
   different source. Never place key values in lane TOML.
3. Edit `services/agri-data-service/lanes/water-gauges-daily.toml` to set a desired inclusive
   historical interval. The shipped policy retains its existing floor and aims for every day:

   ```toml
   [coverage_target]
   first_day = "1990-09-30"
   minimum_fraction = 1.0
   # Optional fixed inclusive end; omit to follow the historical edge.
   # last_day = "2025-12-31"
   ```

   Without `last_day`, the endpoint is the day before the forward recheck window, calculated
   from the configured publication lag and recheck duration. It never comes from stored data.
   A fixed end that is not yet historical reports that pending state. The configured interval
   must fit the 20,000-day census limit and cannot precede the lane or source's declared floor.
4. Keep `gap_fill_enabled=true`; leave forward cadence, budget, and revision windows independent.
   Existing executor activation/owner gates remain applicable. Config lanes use their TOML
   executor/enabled settings; the legacy `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` allowlist is not
   their activation switch. `PLANTGEO_JOB_EXECUTOR_STOPPED_LANES` stops a named owning lane and
   its historical child invocations on either path.
5. Read the structured `coverage_target` in a historical turn report. `target_reached` means
   its **before-turn** data fraction met the threshold. `target_unmet` means historical work
   remains. `no_fillable_gaps` means the data target is unmet but the census has no repairable
   hole, for example only absence markers remain. It must not be described as complete data.

## Inspect and run a new deployment

Follow [deployment setup](deployment.md) for the database, migrations and serving services. The
Python environment example is [services/agri-data-service/.env.example](../services/agri-data-service/.env.example).
For stored Parquet, supply `OBJECT_STORE_ENDPOINT_URL`, `OBJECT_STORE_BUCKET`,
`OBJECT_STORE_ACCESS_KEY_ID`, `OBJECT_STORE_SECRET_ACCESS_KEY`, and the bucket's signing
`OBJECT_STORE_REGION`. Keep a new deployment's bucket or `OBJECT_STORE_PREFIX` separate from
another deployment. Region selection does not itself isolate object-store keys.

From `services/agri-data-service`, install the locked environment and inspect the catalogue:

```powershell
uv sync --locked --all-extras
$env:PLANTGEO_REGION = 'pnw'
uv run --no-sync agri-service ops jobs-executor --inventory
```

The inventory is read-only. Resolve quarantined lanes and check each intended lane's executor,
enabled state and provider before starting the existing sole scheduler:

```powershell
uv run --no-sync agri-service ops jobs-executor
```

That command starts ongoing ingestion and can spend provider quota. Use it for the newly configured
deployment, not as a second ad-hoc writer against an existing production deployment. It uses the
durable ledger and lane leases. Inspect the historical child's structured turn report/log for
`coverage_target` and the forward child's newest accepted data. Do not interpret process health
or an empty map alone as target completion. The existing `--compare` runner option performs source
fetches and is not a free readiness check.

This first slice offers a configured PNW deployment, not a turnkey arbitrary-US-region generator.
US expansion still requires the manifest, source binding and web registration checks described
above; source availability and quotas can differ by region.

## What the percentage means

A data day counts only when every output stream has its full published zoom ladder and a
source-completeness proof matching the strategy's expected spatial support for the selected
region. A single row, partial station/tile response, unresolved identity, missing rung, or
unproven old publication cannot count as a complete day. The report records the region and
support digest. A strategy that cannot declare source support refuses this feature.

Governed absence is reported separately and does not count as data. Existing absence markers
retain their existing governance; this feature does not independently certify their contents.
Missing or unreadable census/completeness evidence cannot produce target success. A fraction
below one intentionally tolerates missing days, and the report continues to show those days.

Idling eliminates upstream historical fan-out, not executor or census/object-store work.
Forward refresh still runs. The water lane's existing 550-day revision rotation still detects
late corrections within that window; older corrections need an explicit revision policy or
reopened evidence. A target cannot detect unseen provider revisions outside the configured
revision horizon. Changing target dates never deletes historical objects or narrows serving
availability.

## Known weather-history limitation

`weather-observations` combines historical forecast-derived rows with a current-conditions
writer whose own history contract is provisional. A live 2018-01-01 query returned an Open-Meteo
Historical Forecast record captured in August 2026. Lowering the serving floor would risk hiding
real records. This change deliberately leaves those objects and
their serving metadata intact. A separate cohort/source-lineage audit must distinguish archived
weather from current polling before changing its repair obligations or history claims. The dark
weather config strategy also needs its same-day versus yesterday-window cutover resolved before
activation; this target feature does not authorize that migration.
