# Ingest modules

`identity.py` is the single definition of a warehouse identity string: it maps one upstream record to a `FeatureIdentity` carrying a producer token, a producer-local id byte-identical to the `featureId` the TypeScript job writes into `geo.features.properties->>'id'`, and the observation timestamp that dates that feature's first geometry version. It is deliberately not a fetcher, a payload validator, a change-detection or circuit-breaker rule, or a database writer; it imports no SQLAlchemy, no config, and no `agri_data_service.db`, so its golden test runs before any lane owns a database connection. The namespace is the producer token (`firms`, `usgs-nwis`, `open-meteo`, `wfigs`, `usdm`, `mtbs`), never the layer name, and `PRODUCER_BY_LAYER_NAME` exists so the backfill substitutes a producer for `l.name` rather than namespacing by a renameable presentation label. Under a Type-2 dimension that namespace is a correctness requirement rather than hygiene: `natural_key` no longer means "this row is unique" but "these rows are the same place over time", so two producers colliding on an unnamespaced id are interleaved into one version chain and fabricate a plausible history, which is strictly harder to detect than a duplicate row. For the same reason the module rejects rather than synthesises — `MissingNativeKeyError` is raised whenever an upstream record supplies no stable native key, and it never falls back to coordinates, a payload hash, a UUID, or the wall clock, because a degenerate key is a synthesised key wearing a real one's shape. `observed_at is None` is a different thing entirely and is legal: it is the contract's representation of `'-infinity'::timestamptz` for `geo.geometry.version_valid_from`, so a consumer writing SQL emits that literal and never a sentinel datetime, `datetime.min`, or `now()`.

Know what the golden test does and does not prove. The `TYPESCRIPT_*` tables in `tests/test_ingest_identity.py` were **derived from the TypeScript, not captured from a database**: the lane brief's §4.2 preferred route — reading `geo.features.properties->>'id'` back out of production — was unavailable because `PLANTGEO_READONLY_URL` is unset and no populated database is reachable, so §4.2's fallback route was used. Each expected id was produced by executing a character-for-character transcription of `firmsObservationId` (`ingestion-jobs.ts:100-115`), `ingestion-jobs.ts:189`, `:294`, `:334` and `environmental-time.ts:6-50` under Node v24.13.0, then cross-checked against `Number.prototype.toFixed(4)` and `Date.prototype.toISOString()` directly. That pins the port against the *current* TypeScript's logic, not against stored history, and the upstream string shapes the fixtures assume — the NWIS `updatedAt` millisecond-and-offset spelling, the `boundedSamplePoints` grid coordinates — are assumptions the test cannot falsify. **A real §4.2 production capture is still outstanding**; run it once a populated database is reachable, and until then treat stored history as the authority whenever a downstream key mismatch appears. Two of the FIRMS rows are exact ties at the fifth decimal that round in *opposite* directions — `-113.26495` toward zero, `-113.26685` away — because the double nearest each literal falls on a different side of the tie. That is why `format_javascript_fixed` must build its `Decimal` from the `float` (the exact binary value ECMA-262 rounds) and never from `repr(value)`, which re-rounds the shortest round-trip string and so re-keys every coordinate whose double sits just below its midpoint; it is also why a regex or prefix assertion is useless here and every test pins the whole `natural_key` string. FIRMS honours an explicitly zoned `observedAt` before deriving one from `acqDate`/`acqTime`, matching `parseFirmsObservationTime`, so re-reading a stored feature and re-ingesting a fresh payload date the same version identically; an `observedAt` carrying no zone is ignored rather than trusted, exactly as `parseZonedObservationTime` returns null and the caller falls through.

`build_drought_area_identity` takes the release date as either the upstream `YYYY-MM-DD` text or the `date` that `geo.drought_areas.valid_date` stores, and always keys the canonical `date.isoformat()` form, so a consumer reading the column back cannot re-derive a differently-spelled key. It pins the drought class to D0-D4 to match upstream's `z.number().int().min(0).max(4)` (`usdm-drought.ts:46`) and rejects `True`, which would otherwise key as `1`. It deliberately does not enforce the Tuesday rule — that belongs to the fetcher (`usdm-drought.ts:117`), not to the identity contract. Keep this module to identity plus the observation timestamp: per-producer change-detection rules, circuit-breaker thresholds and `data_available_at` rules live next to each producer's own ingest module, and the exported producer tokens exist so those modules can key their rule tables off them.

Coordinate formatting goes through `Decimal(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)` with `-0.0` normalised to `0.0` first, never an f-string: JavaScript's `toFixed` rounds ties away from zero on the double's exact binary value while `f"{v:.4f}"` rounds ties to even, and they disagree on every dyadic tie at the fifth decimal — `boundedSamplePoints` computes weather sample coordinates by division, so those ties are reachable in the one producer that also puts coordinates in its key. Timestamps that appear inside a key (`usgs-nwis`, `open-meteo`) are taken verbatim from upstream and parsed into a separate copy for `observed_at`, because NWIS supplies a local-offset string and normalising it to UTC would change the key; FIRMS `acqTime` is likewise used raw in the key and only zero-padded for the timestamp, so an upstream `36` yields key segment `36` and `00:36Z`. On the USGS wall-clock fallback, read the "usgs_nwis.py" section below before changing anything: `build_streamflow_gauge_identity` raises `MissingNativeKeyError` when `updatedAt` is absent, but that is a guard against *silently* synthesising a key inside the identity layer, **not** a decision to drop the gauge — the fetcher supplies the fallback explicitly and the owner's ruling is to keep the TypeScript's behaviour. Never call the builder with a `now()` value that upstream did not supply without recording it as such. Known gap: MTBS returns `observed_at=None` because no annual release identifier is readable yet — `mtbs.ts` is still a read-through ArcGIS proxy — and the module refuses to guess one from `Ig_Date`, which is exactly the ~18-month availability leak spec D3 exists to prevent; the MTBS ingest owner supplies the real release date.

`require_local_source_loader_database_url` (`config.py`) is now a two-line resolver, not a gate. The 2026-08-08 owner ruling (recorded in `20260808_0019`) retired the role family and then the DSN enforcement built on it, so the host/port allowlist (`_INGEST_SOURCE_LOADER_ALLOWED_TARGETS`), the `plantgeo`/`plantgeo_*` database-name guard, the scheme and empty-query/fragment guards, the login assertion, and the "`DATABASE_URL` is never a loader fallback" refusal are **all deleted**. `LOCAL_SOURCE_LOADER_DATABASE_URL` is an optional override: set it to target a database other than `DATABASE_URL`, leave it unset to use `DATABASE_URL`, or set both to the same string — all three are legal, and a blank or whitespace-only value counts as unset rather than as a DSN. Two errors survive: having neither variable, and a DSN that is not a complete `postgresql+asyncpg://` URL (`Settings._require_complete_database_url`, the single parser shared with the profile DSNs). *Which* database is right is the operator's job now; nothing in config will catch a wrong-but-well-formed one. `ingest_session()` (`db/engine.py`) is the zero-argument entry point every ingest job module should import: it calls `require_local_source_loader_database_url()` and hands the DSN to the existing `local_source_loader_session()` one-connection pool, so it invents no new pooling or transaction pattern.

Consequences for whoever wires the cron container next. The Railway target no longer has to be recorded in Python at all — if `switchback.proxy.rlwy.net:37967` rotates, only the service's environment changes, and nothing in this repository needs editing. The container may now set `DATABASE_URL` alone and every `ingest-*`/`jobs-*` verb works; setting `LOCAL_SOURCE_LOADER_DATABASE_URL` as well is still supported and is what the deployed cron does. Its `SERVICE_PROFILE` should stay unset (defaults to `combined_local`): neither `require_local_source_loader_database_url()` nor `ingest_session()` reads `service_profile` at all, and `combined_local` is the only profile that tolerates `DATABASE_URL` in the environment without `config.py`'s model validator raising (`enforce_local_phase_one` blocks `DATABASE_URL` only for `receiver_writer`/`published_reader`). A dedicated `ingest_cron` service profile was considered and deliberately not added: `app.py`'s `profile_blueprints` dict and `health.py`'s readiness checks are keyed on the full `Literal`, so adding a value without updating those call sites would leave a `KeyError` trap if the cron container's profile were ever pointed at the Sanic HTTP entrypoint by mistake.

`redis>=5.0,<6` was added to `pyproject.toml`'s runtime dependencies for the shared writer's per-written-row realtime publish (trap T8); `uv lock` resolved `redis==5.3.1` deterministically, pulling in `pyjwt` as its one transitive dependency.

## writer.py

The writer is the whole port of `src/lib/server/services/ingest.ts`: resolve the layer, take an advisory lock, insert what is new, refresh in place what genuinely changed, publish one realtime message per written row. Each of those five steps carries a decision that looks wrong until you know why.

**The refresh diff ignores geometry on purpose, and its asymmetry is load-bearing.** `_REFRESH_FEATURES` compares `(properties - 'geometry' - 'geometry_repaired') IS DISTINCT FROM (pending.next_properties::jsonb - 'geometry')` — the stored side strips two keys, the candidate side strips one. This is a literal transcription of `ingest.ts:116-117` and must not be tidied into a symmetric strip. The reason the stored side cannot be compared whole is `drizzle/0004_repair_ingested_geometries.sql:45-49`: a trigger rewrites `properties.geometry` through `ST_AsGeoJSON` and may add `properties.geometry_repaired` on **every** write, so the stored copy is never byte-equal to the raw upstream text a candidate carries. A whole-payload comparison would therefore report "changed" for every row on every hourly run — endless rewrite churn plus a realtime storm on a map that has nothing new to draw. The diff instead keys off the producer's own scalar revision fields, which is a sound change signal for these feeds because WFIGS advances `polygonDateTime` and `percentContained` whenever a perimeter is redrawn; an accepted update still writes the new geometry through. The reason the candidate side strips only `geometry` is that a freshly built candidate never carries `geometry_repaired` — stripping it there would change nothing today and would silently mask the difference on the day a producer starts emitting that key. Plan §2.0 reuses this same finding as the Type-2 change-detection rule, so a change here is a change to the dimension's history semantics. The statement now lives in `sql/ingest/refresh_features.sql`, whose walkthrough carries this asymmetry argument in full; the constant in `writer.py` is only the loader plus its `bindparams` chain.

**`geo.features.created_at` is "last touched", not "first seen".** The refresh path rewrites the row, so nothing may derive a first-observation time, a `data_available_at`, or time-slider depth from that column. The observation timestamp lives in the identity contract (`FeatureIdentity.observed_at`), which is the only trustworthy source for when a record was actually observed.

**Dedupe is entirely `properties->>'id'`.** The single database-side guard is `features_layer_external_id_unique` on `(layer_id, properties->>'id')` (`src/lib/server/db/schema.ts:180-183`). It stops a *duplicate* key and does exactly nothing about a *changed* one, which is why every identity string is built by `identity.py` and never by a local f-string: a formatting drift of one digit does not raise, it silently forks a new row and keeps forking one every hour. The advisory lock (`pg_advisory_xact_lock` over the sorted `layer_id:external_id` keys) exists so two runs cannot interleave on one layer; taking the keys in sorted order is what keeps two concurrent batches from deadlocking against each other.

**Realtime publish is part of the contract, not a nicety.** One publish per *written* row to `layer:<name>`, carrying the GeoJSON `Feature` shape the TypeScript emitted, because that message is how the map learns to invalidate. Dropping it costs live invalidation and the map goes stale until a full reload. The channel is validated against `^layer:[a-z0-9-]{1,100}$` before any write is accepted, matching `ingest.ts:145`. Publishing is best-effort by design, mirroring `realtime.ts:55-62`: a Redis failure must never roll back a durable write, so `RealtimePublisher` marks itself unavailable on the first `OSError` and counts every subsequent drop rather than raising. `delivered`/`dropped` are the operator's signal that Redis was down for a run — a silent zero-publish run is the failure mode to watch for.

**Session ownership.** The writer takes an `AsyncSession` and never opens one. `ingest_session()` in `db/engine.py` is the single entry point; an earlier draft of this file defined a second, competing `ingest_session()` that called a `settings.require_ingest_database_url()` which does not exist — it was removed rather than reimplemented, because the owner's ruling was to widen the existing loader validator instead of introducing a new DSN setting.

## http.py

A port of `src/lib/server/http/bounded-upstream.ts` and its bounds: a byte cap enforced *before* the body is read (via `content-length`) and again *while* streaming, plus a per-request timeout. The typed error split is the load-bearing part. `UpstreamHttpError` carries the status, `UpstreamPayloadError` means the body was oversized, absent, mistyped or unparseable, and the two must stay distinguishable because USDM treats a `404` as "this release is not published yet" and walks back to the previous candidate date, while a garbled body is a genuine failure. Collapsing them into one error type turns a normal mid-week USDM run into a red cron.

`fetch_bounded` never raises on a non-2xx status and never raises on an unreadable body; it reports the body failure as `payload_error` instead. That ordering matters: `fetch_bounded_json` raises the *status* failure before the *body* failure, so a `429` or a `5xx` that answers with a huge HTML error page still surfaces as an `UpstreamHttpError` and stays reachable for retry and backoff logic, exactly as `bounded-upstream.ts:164-165` does. `UpstreamTransportError`'s message deliberately carries only the exception class name and never the URL, because FIRMS request URLs embed an API key.

## policy.py

The bounded-ingestion contract from `ingestion-jobs.ts:70-94`: `INGEST_BBOX` is `west,south,east,north`, spans are capped at 30° of longitude and 20° of latitude, and anything malformed or oversized raises. **An unset bbox is `skipped`, not `failed`** — no bbox configured is a deployment that has not been pointed at a region yet, not a broken upstream, and turning it into a failure would make every unconfigured environment's cron run red.

Every environment variable here is read **at call time, not at import** (`ingestion-jobs.ts:29-39, 41-51`). This is deliberate and must survive refactoring: the cron container is long-lived, and a module-level constant would pin the bbox and the record caps to whatever the environment held at import, so an operator changing `INGEST_BBOX` would see no effect until the container restarted. Module-level `Final` constants here are limits and defaults, never resolved environment values.

`PACIFIC_NORTHWEST_COVERAGE_BBOX` (`-125,42,-111,49`) is the canonical coverage box, rehomed here from `src/__tests__/services/ingestion-jobs.test.ts:3` when that test was deleted; plan §4 cites it for lanes E and F. `javascript_number` and `format_javascript_number` exist because the bbox string is re-joined after parsing and must round-trip the way `Number()` and `Array.prototype.join` do — Python's `float()` accepts digit separators that JavaScript rejects, and `str(2.0)` is `"2.0"` where JavaScript emits `"2"`.

## usgs_nwis.py

Ports `getStreamflowGauges` (`usgs-water.ts:163-209`) and `runWaterDroughtIngestionJob` (`ingestion-jobs.ts:171-208`) as `run_water_ingestion_job`, CLI verb `ingest-streamflow`, layer `water-gauges`. The bbox tiling (`tile_bbox`), the four-degree-square NWIS tile cap, the four-way bounded concurrent tile fetch (one tile failure propagates rather than yielding partial coverage, matching `fetchTiledTimeSeries`'s comment at `usgs-water.ts:99`), and condition/trend classification are literal ports. `format_tile_ordinate` reproduces `parseFloat(value.toFixed(6))` through `identity.py`'s `format_javascript_fixed` and `policy.py`'s `format_javascript_number` rather than an f-string, for the same tie-rounding reason `identity.py`'s own coordinate formatter exists. `WATER_GAUGES_LAYER_ID` is read at call time here (`resolve_water_gauges_layer_name`), not as a module-level constant the way `ingestion-jobs.ts:16-17` reads it once at import — a small, deliberate widening of the call-time-env-read principle this module's `policy.py` paragraph already documents, so a cron container changing the layer name mid-life does not need a restart.

**T5 — the wall-clock `updatedAt` fallback is deliberate, not a bug, and it is ported as-is.** `usgs-water.ts:183` reads `latest?.dateTime ?? new Date().toISOString()`: a gauge with no current timeseries reading gets a wall-clock reading time, embedded directly in the feature id (`ingestion-jobs.ts:189`, `${siteNo}:${updatedAt}`). `parse_gauge` reproduces this literally through `format_javascript_timestamp` — never `datetime.isoformat()`, which emits a differently-shaped string and would re-key every fallback gauge on top of the fallback itself. **Consequence, stated plainly: a gauge reporting no current reading mints a brand-new feature id every ingestion run, so the `water-gauges` layer grows every hour by design.** This is an explicit owner ruling (full entry in "Deliberate deviations" below); skipping such gauges was considered and rejected because it would silently drop live stations from the map. `parse_gauge` flags every such record with `updatedAtIsWallClock: True` — an operator-only field that `build_gauge_write` strips before the record reaches `geo.features.properties` — and `run_water_ingestion_job` counts them into `details.wall_clock_identities` on the job result. **That per-run count is the metric to watch**: a rising count is the early warning that `water-gauges` is growing faster than expected and the trigger for revisiting this ruling.

**The `-999999` missing-value sentinel is dropped on BOTH paths, and the forward path shipped without that guard for months.** NWIS writes `-999999` in place of a discharge it does not have, as an ordinary numeric string. `parse_daily_value_series` has always refused it; `parse_gauge` did not, so `readings[-1]` parsed straight through into `flowCfs` and the live 30-minute cron wrote it as a real measurement. Measured read-only against production 2026-08-07: **680 rows in `water-gauges` carry `flowCfs = -999999`, every one of them `published` and geometry-linked, across 27 distinct sites, and 669 of the 680 were written in the six days to 2026-08-07** (35/167/82/136/141/108 on 08-02 through 08-07) — the remaining 11 are a one-row-per-quarter trickle back to 2024-04-05. The rate exploded because it tracks how many gauges are seasonally out of service, not how long the bug had existed. Both parsers now go through one predicate, `is_missing_value_sentinel`, over the one constant `NWIS_MISSING_VALUE`; it tests the sentinel's **value**, never its sign, because `validation.py`'s `USGS_NO_DATA_SENTINEL` records genuine reverse flow down to −172,000 cfs at these gauges and a "negative means missing" guard would delete real measurements. The corrective SQL for the 680 stored rows is proposed, unexecuted, in `docs/runbooks/usgs-sentinel-cleanup.md`.

**What "drop" means on the forward path, and why it is not T5.** The archive drops a DAY; the forward path asks "what is it now" and keeps one reading per gauge, so "drop" had to be decided rather than copied. Measured live 2026-08-07 against tile `-125,46,-121,49`: **all 194 series carried exactly one reading**, because `STREAMFLOW_QUERY_TEMPLATE` pins no `period` and NWIS then returns only the newest value — so there is no earlier reading in the same response to fall back to, and a sentinel is always the whole answer. **The gauge is therefore not reported for that tick: `parse_gauge` returns `None` and no row is written.** Three alternatives were rejected. Writing `flowCfs: null` at the sentinel's real timestamp is the fabricated-observation-of-an-absence the archive path already refuses, and unlike T5 it would not even churn ids, so it would quietly become permanent. Routing it through T5's wall-clock fallback would do that *and* mint a fresh feature id every 30 minutes. Keeping the sentinel is the bug. Dropping is also not the "silently drop live stations from the map" that T5's owner ruling rejected: each reading is its own `geo.features` row keyed `{siteNo}:{updatedAt}`, so a skipped tick deletes nothing — the gauge stays drawn at its last real reading under that reading's own real timestamp, which is exactly how every other gauge behaves between 30-minute ticks. The 11-of-194 sentinel series measured that day were qualified `Ssn` (parameter monitored seasonally), i.e. stations that are legitimately not measuring, not stations going missing. `parse_gauge` filters sentinels out of the readings list and takes the latest survivor rather than testing `readings[-1]`, which is dead-but-deliberate under a one-reading response: it degenerates to the drop today, and adding a `period` to the query later keeps the real reading instead of discarding the gauge.

**`run_water_ingestion_job` reports `details.sentinel_gauges`, and that is the second metric to watch.** `fetch_streamflow_gauges` returns `StreamflowFetch(gauges, sentinel_sites)` rather than a bare list for exactly this reason — nothing counted the drops, which is why six days of corruption went unnoticed. The count is deduped by site, because tiles overlap at their shared edges and a boundary gauge would otherwise be counted once per tile. It sits beside `wall_clock_identities` and answers the opposite half of the same question: how many gauges NWIS named but did not measure. `records_seen` deliberately still counts only gauges that reported a discharge, since that is the number `truncated` and `resolve_max_source_records` are both measured against; a sentinel-only gauge was never a writable record. **No other numeric field on the forward path can carry the sentinel**, and this was checked rather than assumed: `percentile` is a hardcoded `None` on both paths (NWIS instantaneous values never supply one), gauge height is `parameterCd=00065` and the query pins `parameterCd=00060`, and `lat`/`lon` come from `geoLocation.geogLocation` as site metadata rather than as measurements.

Note the narrower guard this sits next to: `build_streamflow_gauge_identity` (`identity.py`) still raises `MissingNativeKeyError` on a genuinely absent `siteNo`. That is not T5 — it is the identity layer refusing to synthesise a key component itself; the *fetcher* here is what supplies the wall-clock fallback explicitly, deliberately, and only for `updatedAt`, never for `siteNo`. `build_gauge_write` catches that error and drops the one gauge rather than propagating it or keying it on an empty prefix — a second, separate deviation from the TypeScript, recorded below.

## Deliberate deviations from the TypeScript

Append to this list rather than burying a deviation in a code comment. Each entry states what differs and why the difference is correct.

- **`ingest-all` runs the six jobs sequentially, not concurrently.** `runAllIngestionJobs` (`ingestion-jobs.ts:391-423`) uses `Promise.all`. The Python runner awaits each job in turn. Failure attribution is simpler, the hourly budget is ample, and the weather job already fans out internally up to the `MAX_WEATHER_SAMPLE_POINTS` cap of 150 Open-Meteo requests — running six such jobs at once buys nothing and makes an upstream rate-limit harder to attribute. Per-job isolation is preserved exactly as in the TypeScript: `run_isolated_job` turns a raised exception into a `failed` result so one source can never erase another's progress.
- **The cron run exits non-zero when any job failed.** This has no TypeScript equivalent and is the entire reliability motivation for the port. The old path (`src/app/api/cron/ingest/route.ts:30-46`) fired `void runAllIngestionJobs()` detached and returned `202 {status:"started"}` before any job had done anything, so a thrown job landed in `console.error` and nothing went red. `any_job_failed` is what makes a failed FIRMS fetch a failed Railway cron run.
- **The USGS wall-clock `updatedAt` fallback is ported as-is, by owner ruling.** `usgs-water.ts:183` reads `latest?.dateTime ?? new Date().toISOString()`, so a gauge reporting no current timeseries value gets a wall-clock `updatedAt` that goes straight into its feature id. The consequence, stated plainly: **such a gauge mints a new feature id every run, so the `water-gauges` layer grows every hour by design.** This was considered and kept rather than "fixed", because skipping those gauges is a behaviour change that would silently drop stations from the map. The fetcher supplies the fallback explicitly rather than letting the identity layer synthesise it, flags each affected record with `updatedAtIsWallClock`, and reports a per-run count of fallback gauges — that count is the metric to watch, and a jump in it is the early warning that the layer is growing faster than expected.
- **Realtime publishing speaks RESP directly** rather than through a connection-pooling client, because an ingest run needs exactly one short-lived connection and a publish that can fail without consequence.
- **A USGS gauge with no readable `siteNo` is dropped, not kept under a degenerate id.** `usgs-water.ts` builds a `WaterGauge` unconditionally, so a time series whose `siteCode` is unreadable becomes `ingestion-jobs.ts:225`'s `${siteNo}:${updatedAt}` with an empty prefix (a bare `:2026-...` id) — a real but unverified-in-production row shape. `identity.py`'s `build_streamflow_gauge_identity` raises `MissingNativeKeyError` for a blank `siteNo` instead, per the identity contract's "reject rather than synthesise" rule (see the `identity.py` paragraph above), and `usgs_nwis.py`'s `build_gauge_write` catches that error and skips the one gauge rather than the whole job. This has not been checked against a production capture; it is expected to be rare, since a time series NWIS itself has trouble naming is already an unusual response shape.
- **A USDM release that was explicitly requested but is not published reports `skipped`, never `ingested`.** On a miss `ingestDroughtRelease` returns `validDate: options?.validDate ?? null` (`drought-ingestion.ts:111-120`), and `runDroughtIngestionJob` derives its status from `outcome.validDate ? "ingested" : "skipped"` (`ingestion-jobs.ts:408`) — so whenever a date was passed in, the TypeScript reports an *ingested* release it never actually fetched. The branch is unreachable in the TypeScript because its job never passes a date; the Python CLI exposes `ingest-drought --valid-date`, which makes it reachable. Reporting `ingested` with zero records for a file that 404'd would put a lie in the cron summary, so the Python job keys its status off whether a release was returned rather than off which date was asked for.

## firms.py

The job fans out across the **full VIIRS NRT constellation** (`VIIRS_SNPP_NRT`, `VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`), matching `nasa-firms.ts`'s exported `FIRMS_VIIRS_SOURCES` and `runFireIngestionJob`'s `Promise.allSettled` fan-out (`ingestion-jobs.ts:137-149`) rather than querying one product. `nasa-firms.ts`'s own comment states the reason: a single satellite is not a reliable feed, and that satellite going quiet would silently zero the layer. `_gather_constellation` mirrors `Promise.allSettled` with `asyncio.gather(..., return_exceptions=True)`; the job raises only when every product is unavailable (the first captured exception, matching `collections[0] as PromiseRejectedResult`), and a partial outage is reported through `reason` (`"Unavailable FIRMS products: ..."`) without dropping the satellites that did answer. Detections merge into one `dict[external_id, FeatureWrite]` with last-write-wins, a literal port of the TypeScript's `seen.set(featureId, ...)` `Map` — this is genuine deduplication, not just a cross-satellite safety net: two rows that key to the same 4dp-rounded natural key, whether from the same product or different ones, collapse to one write. The merged, deduplicated set is then sorted newest-first by `observedAt` before the `INGEST_MAX_SOURCE_RECORDS` cap applies, so truncation drops the oldest detections rather than whichever satellite happened to resolve last (`ingestion-jobs.ts:184-199`).

**A first pass at this module queried only `VIIRS_SNPP_NRT` and shipped without the constellation fan-out.** That is the exact single-satellite failure mode `nasa-firms.ts`'s own comment warns against, and it was caught and corrected before this module's tests were finalised — recorded here because a future "simplification" back to one product would reintroduce a real production gap silently, with every existing test still green.

Two more corrections belong in the CSV parser, both silent-data-loss bugs rather than porting slips, caught the same pass. First, `parseFIRMSCsv`'s brightness column has a MODIS/VIIRS fallback (`nasa-firms.ts:58-61`): `brightnessIdx = header.indexOf("brightness") >= 0 ? ... : header.indexOf("bright_ti4")`. VIIRS — the only constellation this job ever queries — never publishes a `"brightness"` column, only `"bright_ti4"` (the header comment at `nasa-firms.ts:47` lists it explicitly); a parser that only ever looked for `"brightness"` reads `-1` on every real production row and silently zeroes the field forever, with no error and nothing in `details.rejected` to notice by. Second, the `satellite` column's fallback is the **requested product token** (`source`), not a fixed label — `nasa-firms.ts:84`'s `satellite: satelliteIdx >= 0 ? cols[satelliteIdx].trim() : source`. Both are now covered by named regression tests built on a genuinely VIIRS-shaped header (no `"brightness"` column), rather than the MODIS-style header the original fixtures happened to use — which is exactly why neither gap was caught the first time.

The CSV adapter otherwise resolves every column by *header name*, and the `_column` helper exists for exactly one reason: `header.indexOf(name)` returns `-1` in JavaScript and `cols[-1]` is `undefined`, so a FIRMS feed that stopped sending a column silently fell back to its default. In Python `columns[-1]` is the *last* cell, so a naive transcription would have keyed every detection on whatever the final column happened to hold. `-1` is therefore checked explicitly before any indexing. `javascript_parse_float` is likewise not decoration: `parseFloat("312.4NRT")` is `312.4` where `float()` raises, and the confidence and version columns of some FIRMS releases carry exactly that shape.

Two rejections are load-bearing and must stay rejections rather than substitutions. A detection whose `observedAt` cannot be derived, or whose native key is incomplete, is dropped (the TypeScript's `flatMap -> []`), and a detection older than `min(5, max(1, dayRange))` days, or skewed more than five minutes into the future, is dropped. Neither is an error: FIRMS routinely reissues rows a previous run already stored, and the count lands in the result's `details.rejected` so a sudden jump stays visible. The freshness window derives from the *effective* day range rather than from `FIRMS_DAY_RANGE` directly, because `firms_day_range()` clamps a garbage value to the default and the two must not diverge. That variable accepts only a plain non-negative integer: `"5abc"` falls back to `2` rather than being partially accepted the way `parseInt` would truncate it.

The API key env var is `NASA_FIRMS_KEY` (`nasa-firms.ts:104`) — not `FIRMS_API_KEY`, which an earlier version of this lane's brief had — and it is read at call time inside `fetch_active_fires`, once per constellation product per run, matching `fetchActiveFiresNASA`'s own per-call check (`nasa-firms.ts:104-107`) rather than being validated once up front. A missing key raises `ValueError` immediately, before any HTTP request; with all three products failing identically, `run_fire_ingestion_job` raises that same error rather than reporting a quiet "ingested, wrote nothing" — matching `runFireIngestionJob`'s behaviour when every product in its `Promise.allSettled` rejects. The key never reaches an exception message anywhere in the module: it is interpolated into the request URL, so `http.py` converts every `httpx.HTTPError` into an `UpstreamTransportError` carrying only the exception class name — a job's failure reason is printed into a cron log that operators read and paste.

**No standalone `time_rules.py` was created**, though one was in this lane's original file list, because `environmental-time.ts`'s three functions already have a single home apiece and a fourth copy would be the "second definition of a contract" the `results.py` paragraph above already warns against for the same reason. `parseFirmsObservationTime`/`parseZonedObservationTime` are folded into `identity.py`'s `build_firms_identity` — the only place that ever derives a FIRMS record's `observed_at`, see the `identity.py` paragraph above. `firmsDayRange` lives in this module as `firms_day_range()` because it is genuinely FIRMS-only and nothing else in the package reads `FIRMS_DAY_RANGE`. `isFreshObservation` lives in `policy.py` as `is_fresh_observation()` because it is the general bounded-ingestion freshness check the "policy.py" paragraph above already documents, not a FIRMS-specific rule — `open_meteo.py` calls that same function for its own staleness check. Introducing `time_rules.py` would have meant either duplicating logic two other modules already own correctly, or relocating `is_fresh_observation` out of `policy.py` and rewriting `open_meteo.py` and its tests, both outside this module's file boundary.

## firms.py: the archive is the same endpoint with a date, and the product is the discriminator

`nasa-firms-archive` is a second `--source` token over the **same** producer, layer, channel and identity
contract as `nasa-firms` — not a second producer. FIRMS has no separate archive host: `/api/area/csv/…`
takes an optional trailing start date, and how far back you can reach is a property of the *product*, not
of the URL. Four things about it are load-bearing.

**The day-range ceiling is 5, and it was 10.** Measured against the live API on 2026-08-05 with the
production key over `-125,42,-111,49`: day ranges 1, 2, 3, 4 and 5 answered HTTP 200 (3,741 / 6,886 /
10,094 / 16,264 / 20,633 CSV lines on `VIIRS_NOAA21_NRT`), and 6, 7 and 10 each answered
`400 Invalid day range. Expects [1..5].` — identically for the dated and the undated form. `MAX_FIRMS_DAY_RANGE`
was 10 until that measurement, so `_clamp_day_range` advertised a ceiling every product refused:
`FIRMS_DAY_RANGE=10` produced a 400 on all three constellation products and therefore a **failed job**,
confirmed by running `ingest-firms` that way against production. Nothing in production sets
`FIRMS_DAY_RANGE` (checked on `plantgeo-ingest-cron`, `plantgeo-cron-firms` and `plantgeo-main`), so
`firms_day_range()` returns the default 2 there and lowering the clamp changes no live behaviour — it only
stops a 6-to-10 configuration from being silently accepted and then failing.

**That clamp is currently forked, and the TypeScript half is the one that serves.** `firmsDayRange()`
(`src/lib/server/services/environmental-time.ts:55,69`) and `fetchActiveFiresNASA`
(`src/lib/server/services/nasa-firms.ts:96,110`) both still clamp to 10, and `firmsDayRange()` feeds the
**serving** window through `src/app/api/fires/route.ts`. So `FIRMS_DAY_RANGE=7` today would ingest 5 days
and serve 7 — the map would ask for a window the ingester never filled. Those files are outside this
package; the corrective hunks are with the owner of `src/**`. Nothing in production sets the variable, so
this is latent rather than live, but do not treat `MAX_FIRMS_DAY_RANGE` as the single source of truth
until the TypeScript half lands.

**Which product answers for a past day is read from the live availability table, never assumed, and
resolved over the whole span rather than its first day.** `fetch_product_availability` reads
`/api/data_availability/csv/{key}/all` once per walk. `products_covering` picks per day and
`products_covering_span` unions that over every day of a span. Resolving from `start_day` alone was a
silent hole: a span is up to five days wide, so a product whose coverage begins on day three of it was
never asked for the two days it does cover, and the walk answered `records_seen=0` for them —
indistinguishable from "no fires that week". Over-asking is safe and cheap; a product asked for a day it
lacks simply returns fewer rows. A span **no** product covers is now named in a
`firms_history_spans_uncovered` warning rather than returning a clean empty answer, which is the same
"gap certified as complete" failure `HistoryCapability` exists to prevent, wearing a date range.

`properties.product` records which product actually answered and is the stored discriminator. Note what
that means for rows written **before** this landed: they carry no `product` key at all, and absence
therefore means "written by the forward NRT path before 2026-08-05" — the forward path records `product`
from now on, so the gap does not grow, but it does not retroactively close either.

**Standard processing supersedes near-real-time for one key, explicitly, and that is a precedence rule
rather than a de-duplication convenience.** The near-real-time and standard-processing series of one
satellite **report the same `satellite` token** — `VIIRS_NOAA20_NRT` and `VIIRS_NOAA20_SP` are both `N20`
— and `product` is deliberately **not** in `build_firms_identity`'s key, because that key is
contractually byte-identical to the TypeScript `properties->>'id'` and 148,460 stored rows depend on it.
So the same acquisition delivered by both series keys to one row, and something has to decide which one
survives. `collapse_history_records` compares `processing_tier` (read off the `_SP`/`_NRT` suffix, never
off the satellite) and keeps SP, recording `properties.supersededProduct` on the survivor. Equal tiers
keep the later arrival, which is the forward job's `Map.set` semantics. Relying on `FIRMS_HISTORY_SOURCES`
order for this was the previous behaviour and it happened to produce the right answer, which is exactly
why it needed a test: reorder the tuple and the NRT draft silently overwrites the SP reprocessing of the
same physical detection. Their windows are disjoint upstream today (`VIIRS_NOAA20_SP` ends 2026-05-31,
the day before `VIIRS_NOAA20_NRT` begins), but `products_covering_span` now deliberately over-asks across
a span's whole width and every reprocessing campaign moves that boundary, so the overlap is reachable
rather than hypothetical.

**Measured on production 2026-08-05, that collision has not yet happened, and dropping NRT from
`FIRMS_HISTORY_SOURCES` would not have prevented it.** Every stored SP row has an acquisition date in
2022-08-05..2023-01-14 and every stored NRT row 2026-08-05, so the two series share no acquisition day;
all 115,810 SP rows were created inside the walk window with `updated_at = created_at`, so no pre-existing
NRT row was rewritten as SP, and no row carries an SP token with a pre-walk `created_at`. Dropping NRT
products from the history list was the reviewer's proposal and was **not** taken. It buys less than it
costs: the collision is between what the *walk* writes (SP) and what the *forward job* already wrote
(NRT) on the same day, so removing NRT from the walk's candidate list removes one entry point and leaves
the collision itself intact, while making the ~2-month band between the SP frontier and the forward job's
2-day window unreachable by any path. The trade-off accepted instead: the walk keeps both series, the
precedence is explicit and tested, and one limitation stands unfixed — across runs, an SP row landing on
a stored NRT row leaves no record that the row was ever NRT beyond its `product` token changing and
`updated_at` moving. Recording that properly needs a column `geo.features` does not have.

**MODIS is a different instrument, not a further satellite, and the served layer must not put it on one
scale with VIIRS.** `MODIS_SP` was added to `FIRMS_HISTORY_SOURCES` for the reach its 2000-11-01 floor
buys — twelve years further back than `VIIRS_SNPP_SP` — onto a layer the forward path had only ever
filled from VIIRS. Measured on production 2026-08-05: MODIS_SP's median FRP is **33.10 MW** against
VIIRS' **4.27**, an ~8x gap that is pixel area (1 km² against 0.14 km²) and not fire intensity, and all
12,157 MODIS rows write a numeric `"0".."100"` confidence into the same `properties.confidence` that
136,303 VIIRS rows write `l`/`n`/`h` into. Every FIRMS record now carries
`properties.spatialSupportMeters` (375 or 1000) and `properties.confidenceNormalized`
(`low`/`nominal`/`high`, MODIS' percentage banded by FIRMS' own published equivalence: <30 / 30-80 />80),
so the discriminator is a field rather than prose. The raw `confidence` is kept verbatim beside it —
MODIS' percentage is a real measurement, not a spelling of the band. An unrecognised product omits
`spatialSupportMeters` rather than defaulting to VIIRS, because a wrong spatial support is worse than an
absent one: it makes an incomparable value look comparable. **The serving side is not fixed by this and
is not in this package**: `environmental-read-model.ts`'s `METRIC_SOURCES` serves `fire-radiative-power`
on `valueKey: "frp"` with no product filter, so the slider still paints mixed-instrument FRP in one
symbology. The hunk that gates it is reported to the owner of `src/**`; until it lands, treat the
`fire-radiative-power` metric as instrument-mixed for any acquisition day before 2023-01-15.

**A product answering with a header and no rows is not evidence the sky was empty.** `VIIRS_SNPP_SP`
returned a bare header for 2022-08-05..08-09 while `VIIRS_NOAA20_SP` returned 1,650 detections and
`MODIS_SP` 356 for exactly the same five days and box. The walk therefore queries every product the
availability table covers and merges, and never reads one product's silence as coverage. This is the same
"gap certified as complete" failure `HistoryCapability` exists to prevent, wearing a product token.

**`max_observation_age=None` removes the age floor and keeps the future-skew guard.** The forward job's
rolling window is `min(5, max(1, dayRange))` days, which would reject every archive record; a history
walk's window *is* its age bound. `build_fire_detection_write` therefore takes `timedelta | None` and
substitutes `UNBOUNDED_OBSERVATION_AGE` (a century) rather than skipping `is_fresh_observation`, so an
acquisition FIRMS dates after the run clock is still refused. An undated record stays refused
unconditionally, separately from the age check.

**`merge_history_records` was renamed `collapse_history_records`, and its old rationale was false in
every clause.** It read: "`select_writes` does not deduplicate, so several rows carrying one
`properties->>'id'` in a single batch would reach `_INSERT_FEATURES`, whose `ON CONFLICT DO UPDATE`
cannot affect one row twice and would fail the whole chunk." `_INSERT_FEATURES` has **no `ON CONFLICT`
clause at all** — collisions are handled by a prior `_SELECT_EXISTING_EXTERNAL_IDS` plus an in-place
`_REFRESH_FEATURES` under an advisory lock — and `writer.py`'s `_ingest_resolved_batch` already collapses
each batch with `{write.external_id: write for write in batch}` before it inserts anything. A repeated
key inside one batch was never going to fail a chunk. The function survives because the thing it is
actually needed for is real and is documented above: it is the SP-supersedes-NRT precedence rule. Keys
still come from `build_firms_identity` — never re-derived — and a record that cannot be keyed passes
through so `select_writes` counts it as a rejection rather than this function dropping it silently.

**The coverage frontier travels with the record.** The availability table names each product's
`max_date` and the walk used to discard it. Every archive record now carries
`properties.productCoverageThrough`, the answering product's published frontier, so a leakage-sensitive
as-of query gates on a field rather than on this paragraph. This stands in for the governed
`data_available_at` concept until `geo.features` has a column to model publication lag: the SP series'
`observedAt` is acquisition time and carries a months-long lag behind the day the product actually
published, and `max_date` is the only figure upstream gives for that frontier. It is **absent** on a
forward-path record, where the product's coverage is "now" by construction and the forward job never
reads the availability table.

`history_day_spans` cuts a chunk on the bounds' **own UTC calendar dates**. A window expressed at `-07:00`
covers two UTC days and the span says two, which is a superset of what was asked for and every record in it
carries the acquisition day FIRMS itself named; reading the bound as a naive local date instead would start
the walk a day early and still miss part of the day requested.

## usdm.py: a single-part drought class is a Polygon, and rejecting it lost 26 weeks

`_parse_drought_feature` required `geometry.type == "MultiPolygon"`. USDM publishes a class as a bare
`Polygon` whenever that class happens to be one contiguous area, so the gate rejected the **whole release**
over its simplest class. Measured against production on 2026-08-05, that was the sole cause of 26 of the 29
release weeks missing from `geo.drought_areas` between 2022-08-09 and 2026-08-04 — every one a week whose D4
class was single-part (2024-02-20..2024-06-04, 2024-12-17..2025-01-28, 2025-11-11, 2025-12-09, 2025-12-16).
Each of those files fetches fine and parses as a five-feature `FeatureCollection` with classes D0-D4; only
the D4 geometry type differs from the stored weeks.

Storing is unaffected and nothing is promoted in Python: `_STORE_DROUGHT_AREA_TEMPLATE` already wraps every
geometry in `ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(…), 4326)), 3))`, and
`ST_Multi` promotes a `Polygon` to a single-part `MultiPolygon`, so an accepted `Polygon` lands byte-identically
to what USDM would have sent had the class been multi-part. `DROUGHT_AREA_GEOMETRY_TYPES` is a two-member
frozenset rather than a dropped check: a `GeometryCollection`, a line or a point class still fails the gate,
and `tests/test_ingest_usdm.py` pins both directions. The three genuinely absent weeks (2026-02-17,
2026-02-24, 2026-08-04) answer HTTP 404 and stay reported as `not_published` gaps — a gap USDM never filled
is data, and is still never fabricated.

**Widening that gate widened what can repair to nothing, so the store now refuses an empty repair.**
`ST_Multi(ST_CollectionExtract(ST_MakeValid(<zero-area ring>), 3))` yields `MULTIPOLYGON EMPTY`, and
`geo.drought_areas.geom` is `NOT NULL` — which a `MULTIPOLYGON EMPTY` satisfies. Storing it records
"this drought class exists and covers nothing": a fabricated coverage claim wearing a valid geometry's
shape, against which `ST_Intersects` answers false for every point on Earth with nothing looking wrong.
`_STORE_DROUGHT_AREA_TEMPLATE` now runs the repair chain in a `repaired` CTE so the same expression can
be both inserted and tested; the insert carries `WHERE NOT ST_IsEmpty(repaired.geom)` and the statement
returns `repaired_to_empty` alongside the stored-row count, on which `store_release` raises
`UpstreamPayloadError` and rolls the release back. Rejecting the whole release rather than skipping the
one class matches this module's existing rule for a repeated DM class: a release with an unusable class
is ambiguous, not mergeable. Measured on production 2026-08-05, 0 of 1,030 stored areas are empty or
zero-area, so this guards the exposure the widened gate created rather than repairing damage it caused.

## open_meteo.py

`bounded_sample_points` **densifies, never slices**. When the derived grid would exceed `MAX_WEATHER_SAMPLE_POINTS` (150), the spacing grows uniformly until the grid fits, so the whole bbox stays covered at a coarser resolution. Truncating the point list instead — the obvious "simplification" — would silently blank whichever half of the region sorted last, and the map would render a clean, believable, half-empty weather layer. The `MIN_SPACING_GROWTH_FACTOR` of 1.01 is what guarantees the loop terminates even when the square-root estimate rounds back to the same column and row counts.

The observation timestamp is trap T3 made concrete. `weather.ts:74` builds `observedAt` as `new Date(c.time * 1000).toISOString()`, which is `2026-08-03T14:15:00.000Z`, and `ingestion-jobs.ts:294` embeds that exact string in the feature id. Python's `datetime.isoformat()` yields `2026-08-03T14:15:00+00:00` — a different key for the same observation, which would fork the entire `weather-observations` layer on the first run. `format_javascript_timestamp` from `identity.py` is the only permitted renderer of that string, and `tests/test_ingest_open_meteo.py` pins it.

One sample point's failure never discards the grid: the fan-out gathers with `return_exceptions=True`, exactly as `Promise.allSettled` did, and unreachable points are counted into `details.unavailable_points`. A point answering with a stale or out-of-range reading is refused rather than stored, because a fabricated 0 degrees is worse than a missing sample — unavailable data must stay visibly unavailable.

`WEATHER_LAYER_ID` is read at call time here (`resolve_weather_layer_name`), not as a module-level constant the way `layer-ids.ts:4` reads it once at import — the same small, deliberate widening of the call-time-env-read principle `usgs_nwis.py`'s `WATER_GAUGES_LAYER_ID` paragraph documents above, for the same reason: a long-lived cron container should not need a restart to pick up a renamed layer. It has no observable effect on a one-shot `agri-service data ingest-weather` invocation, since the environment does not change mid-process either way; it only matters if this module is ever imported into a longer-lived process.

`tests/test_ingest_open_meteo.py` pins one row read 2026-08-03 from production `geo.features` on `weather-observations` (`RECORDED_OBSERVATION`) end-to-end through `parse_current_weather` and `build_weather_write`, confirming the observedAt-inside-the-key shape against a real stored id rather than only a synthetic one, and separately pins the densified-grid point count and its distinct longitude/latitude counts for a spacing that forces growth — a regression a looser "every point lies inside the bbox" assertion would not catch, since a bug that sliced the naive grid instead of densifying it would still satisfy that looser check.

## wfigs.py

`perimeter_severity` returns **null**, never `"low"`, when WFIGS reports no containment. `src/lib/map/layers.ts:71` renders against that contract, so substituting the lowest severity would paint an uncontained fire as nearly contained. `poly_PolygonDateTime` is frequently null upstream and the `_Current` feed is already scoped server-side to active incidents, so — unlike FIRMS — no client-side freshness rejection is applied here. That absence is deliberate, not an omission.

`epoch_milliseconds_to_iso` reproduces `new Date(ms).toISOString()` including its failure mode: a value outside JavaScript's plus or minus 8.64e15 Date range became `Invalid Date` and stored null. Python's `datetime` range is far narrower than JavaScript's, so the overflow path returns null for the same reason rather than raising; WFIGS has never emitted such a value, and the branch exists so it cannot fail a whole run on the day it does. Milliseconds truncate toward zero before conversion, matching `new Date()`.

Only a busy ArcGIS payload — an HTTP-200 body carrying an `error` object — or a 429/5xx is retried, up to `MAX_ATTEMPTS` attempts with jittered exponential backoff, bounded by `RETRY_WALL_CLOCK_CEILING_SECONDS` of total wait. Everything else raises immediately: retrying a 400 or a schema change only delays a red cron run. The busy case matters because ArcGIS reports throttling *inside a 200 response*, so a status-only retry rule would read an outage as a successful empty fetch and write nothing while reporting success.

**Two retries ~3s apart was not enough to survive a real throttle, and the fix widens the budget rather than changing what gets retried.** Production (`plantgeo-cron-fire-perimeters`) crashed hourly from 2026-08-10 22:21 UTC: `wfigs_upstream_retry` fired for `attempt=1` and `attempt=2` roughly 0.5s and 2.5s apart at `offset=0`, ArcGIS answered "Too many requests" both times, and `ingestion_job_failed` ended the run — `restartPolicyType: NEVER` means that hour's fetch was simply lost, not retried by the platform. `is_retryable_failure` was already correct before this fix: a malformed or schema-mismatched `UpstreamPayloadError` never matches `BUSY_MESSAGE_PATTERN` and is never retried, so the weakness was purely the retry budget's shape, not what gets retried. `MAX_ATTEMPTS` rose from 3 to 6, the fixed two-entry `RETRY_BASE_DELAYS_SECONDS` tuple became a doubling `RETRY_BASE_DELAY_SECONDS` (1s, 2s, 4s, 8s, 16s) capped at `RETRY_MAX_DELAY_SECONDS` — matching `http.py`'s own `TRANSPORT_RETRY_BASE_SECONDS * (2 ** (attempt - 1))` shape rather than inventing a second one — and `RETRY_WALL_CLOCK_CEILING_SECONDS` is a backstop so a sustained throttle still gives up within one cron tick instead of a container held open indefinitely, checked against a monotonic clock threaded through `fetch_fire_perimeters_page`/`fetch_fire_perimeters` the same way `jobs/worker.py`'s `deadline`/`monotonic` pair already is, so a test can fake elapsed time without a real sleep. A genuinely sustained throttle — every attempt within the budget still busy — still raises `UpstreamPayloadError`/`UpstreamHttpError` exactly as before; nothing here converts a hard failure into a silent success or a partial write. The `wfigs_upstream_retry` event name and its `attempt`/`offset`/`error` fields are unchanged so an operator's existing log filter still matches; `elapsed_seconds` was added to that same line rather than a new event.

**No extracted, importable retry helper existed to reuse.** `is_retryable_failure`, `jittered_retry_delay_seconds` and `BUSY_MESSAGE_PATTERN` are still hand-duplicated, byte-for-byte, in `evacuation_zones.py` — a second bespoke copy of the same status-level retry loop, not a shared seam the way `open_meteo_lane.py` is for its consumers. Widening that duplicate too was out of scope here: the incident and the fix are scoped to WFIGS, and touching `evacuation_zones.py` risks its own, unrelated behaviour. The two modules' retry code is kept structurally identical on purpose so a future extraction into one shared module is a mechanical move, not a rewrite.

**A single unpaged query outgrew `WFIGS_BOUNDS.max_bytes` on an ordinary day, not only at fire-season peak, and the fix is the same ArcGIS pagination `evacuation_zones.py` already established.** `run_fire_perimeters_ingestion_job` crashed hourly in production from 2026-08-06 with `UpstreamPayloadError("upstream response exceeded the byte limit")`. Measured live 2026-08-08: one `resultRecordCount=2000` query over the PNW bbox (114 current perimeters, an ordinary day) answered 18,091,373 bytes against the 16 MiB cap — the previous single-shot fetch was already broken before any peak-season growth. `fetch_fire_perimeters` now pages with `resultOffset`, honouring `properties.exceededTransferLimit` (confirmed present on this host's GeoJSON responses, nested exactly as `evacuation_zones.py`'s `_exceeded_transfer_limit` already expects) as the keep-paging signal, and stops at `resolve_max_source_records()` or the `MAX_PAGES` circuit breaker, mirroring `fetch_evacuation_zones`. `MAX_RECORD_COUNT` (the page size) dropped from 2,000 to 100 and `geometryPrecision=5` (~1.1 m) was added to `build_query_url`, which measured 10,950,562 bytes for the same 114-perimeter query — cutting a fire perimeter's coordinate strings is what evacuation zones never needed, because a fire perimeter is roughly 15x heavier per row (`fire-perimeters` at ~130,583 bytes/row against `evacuation-zones` at ~8,032, per the pipelines skill's measured storage table). **The byte cap itself is untouched** — raising `max_bytes` was rejected as the fix per the incident's own diagnosis; a single page that is still too heavy (one pathologically complex perimeter) still fails that page rather than being silently permitted through a wider ceiling.

## usdm.py

Four rules here look like bugs and are not. The date is the **request parameter**, never parsed from the payload, because the published GeoJSON carries no date field at all — which is why `usdm_current.json` is deliberately never used, and why a 404 means "not published yet" rather than a failure. A repeated DM class **rejects the whole release** instead of picking one, because a duplicated class makes the release ambiguous rather than mergeable. Geometry is repaired **in the database**, with `ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(...), 4326)), 3))`: USDM ships self-intersecting rings that would make `ST_Intersects` unreliable at read time, and moving that repair into Shapely would both change the result and lose the guarantee that what was validated is what was stored. The prune keeps the newest N releases, default 8, roughly 19 MB each.

`geo.drought_areas` has no `properties->>'id'`, so USDM has no external id to key — yet `build_drought_area_identity` is still called for every area before it is written. It is the D0-D4 range check and the calendar-date check, it is what will supply the Type-2 dimension's `natural_key` when that lands, and calling it keeps "identity strings are built in exactly one place" true with no exceptions. The Tuesday rule lives in the fetcher rather than in the identity contract, matching `usdm-drought.ts:117`.

`DroughtStore` is a Protocol with a `PostgresDroughtStore` implementation, so the job's release walk, retention clamp and skip semantics are testable without PostGIS. A test that needed a live database to prove "an unpublished week is skipped, not failed" would be a test nobody runs. The store's SQL is asserted as text — the repair chain, the `ON CONFLICT (valid_date, dm_category) DO UPDATE`, and the `WHERE false`/`WHERE true` predicate — because every one of those is a clause a future tidy-up would happily "simplify". Dropping the conflict predicate is the dangerous one: it does not fail, it silently makes the weekly cron rewrite ~19 MB of geometry every hour for the rest of the week. That SQL now lives in `sql/ingest/store_drought_area.sql`, with `replace_predicate` as a load-time `.format()` slot filled from the operator's `--replace` flag and never from request input; the retention statement is `sql/ingest/prune_drought_releases.sql`. Because a `.sql` file's documentation header is part of `str(text(...))`, the prune test's "starts with `DELETE`" assertion now strips that header through `_statement_body()` before collapsing — the assertion itself is unchanged.

Three porting hazards here are pinned by test rather than by comment. `date.fromisoformat` is far more permissive than the TypeScript's `/^\d{4}-\d{2}-\d{2}$/`: it parses both `20260728` and `2026-W31-2`, and both resolve to a real Tuesday, so a naive port sails straight through the Tuesday guard and then requests a URL built from a date spelling USDM never published. `_require_tuesday` therefore round-trips the parsed date back through `isoformat()` and rejects any other spelling. `usdm_valid_date_candidates` rejects a naive `now` instead of calling `astimezone(UTC)` on it, because a JavaScript `Date` is always an absolute instant whereas a naive Python datetime resolves against the container's local zone — under a US offset that shifts the cursor a day and walks the wrong Tuesdays, producing a run of 404s that looks exactly like a genuine USDM outage. And `DROUGHT_CATEGORY_LABELS` is deliberately **not** ported: `usdm-drought.ts:9-15` remains the live definition for `alert-engine.ts:275` and `environmental-read-model.ts:481`, neither of which this track ports, so a Python copy would be a second definition of a contract that drifts in silence — the first draft of this module had already re-spelled its em-dashes as hyphens, which is precisely how that drift starts.

## ndvi.py

**Superseded 2026-08-04.** This was a hard stub returning `skipped` with the TypeScript's reason string, on the grounds that the vegetation layer was a deliberate governance stub. It is no longer a stub: `ndvi.py` is now a thin verb that delegates to `vegetation.py`'s Sentinel-2 L2A sampler, and `run_vegetation_ingestion_job` writes real observations into the `vegetation` layer. Do **not** revert it to a stub on the strength of the paragraph this replaced.

What it is now: `ndvi.py` resolves the bounded bbox, builds the source via `vegetation.build_vegetation_source()`, and runs it through the same `select_writes` path every other source uses. The source declares `shape="grid_cell"` — one upstream record is a sample landing on a shared raster cell, not one record per place — so `source.grid_cell_of` holds it to that shape and `geometry.feature_geometry_request` keys the dimension by the cell rather than by the sample. `build_ndvi_write` stores the scene instant under `observedAt`, which is what makes the layer datable to the slider (`environmental-read-model.ts` coalesces `observedAt`/`updatedAt`/`polygonDateTime` and nothing else). It declares `HistoryCapability(supported=True, earliest=SENTINEL2_L2A_EARLIEST_OBSERVATION)`, so `agri-service data ingest-backfill --source sentinel2-ndvi --since … --until …` can walk it. A scene search that finds nothing clear still returns `skipped`, not `failed`: cloud is a fact about the sky, not a broken job.

The optional `on_persisted` callback is the forward governed/Parquet seam. It runs only after the
feature writer returns successfully and receives the exact accepted writes, including on an
idempotent raw write whose changed-row count is zero. Skips, all-rejected selections and writer
failures never invoke it. `runner.py` and the dedicated `_run_ndvi` command bind the production
forward writer; tests can pass a callback without constructing PostgreSQL or object storage.
Forward counters are flattened into the existing integer-only `details` map with a `parquet_`
prefix. If publication raises after raw persistence, NDVI returns a failed result that preserves
the real `records_seen` and committed `records_written`; the cron goes red without falsely claiming
the durable raw work vanished. A bounded forward run that leaves pending or contended days raises,
so a direct CLI rerun resamples the same source scope and resumes from physical completion markers.

One gap is deliberate and still open: `METRIC_SOURCES` in `environmental-read-model.ts` has no `vegetation` entry, so once this producer fills the layer the capability payload will advertise observed days the metric map cannot answer for. Add the NDVI metric there in the same change that enables the producer on the cron, or the slider claims a day it cannot serve.

## results.py, runner.py and commands.py

`IngestionJobResult` is the whole operator-facing contract. `to_summary()` omits `truncated`, `reason` and `details` when unset so a clean run reads cleanly, and `details` is where each producer reports the number it wants watched — `rejected` for FIRMS, `wall_clock_identities` for USGS, `unavailable_points` for Open-Meteo, `releases_pruned` for USDM. `failure_reason` deliberately does **not** stringify a `SQLAlchemyError`: that message carries the whole statement and its bound parameters, which is both enormous and the wrong thing to put in a shared log, so it degrades to the exception class name in the same style as `interface/cli/commands.py:1104`.

`run_isolated_job` catches `Exception` on purpose. It is the per-job boundary replacing `Promise.all`'s per-job try/catch, and without it one upstream outage would abort the five sources that were about to succeed. `any_job_failed` is then the single place the exit code is decided, and every `ingest-*` verb routes through the same `finish()` helper, so a one-job run and a six-job run report and exit identically.

**`commands.py` stdout is a JSON-lines stream and nothing else.** `emit()` prints exactly one `to_summary()` line per job, which is what a cron log parser reads; anything else written to stdout corrupts that stream for every consumer. structlog's default configuration sinks to `sys.stdout`, so `commands.py` binds its own logger to `sys.stderr` (`structlog.wrap_logger(structlog.PrintLogger(file=sys.stderr))`) rather than taking `structlog.get_logger()`. The `realtime_publish_totals` line — the only place `RealtimePublisher.delivered`/`.dropped` are ever aggregated, since the publisher itself only logs per-channel failures — is telemetry, not output, and belongs on the diagnostic channel. Note the producer modules (`firms.py`, `usgs_nwis.py`, `wfigs.py`, `open_meteo.py`, `realtime.py`) still take a plain `structlog.get_logger()` and therefore still land on stdout during a real run; that is a known gap, and closing it needs one process-level `structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))` at the CLI entry point in `interface/cli/commands.py`, which is outside this package.

## The cron container

`infra/cron-ingest/Dockerfile` no longer holds a curl entrypoint, a `CRON_SECRET`, or the `202|409 -> exit 0` mapping. It installs the `agri-data-service` package from the committed lock and runs `agri-service data ingest-all` to completion; the process exit code is the run's verdict. `restartPolicyType: NEVER` plus the hourly `cronSchedule` is now the concurrency guard that the deleted in-memory `ingestionInFlight` boolean used to be — a container still running when the next tick fires is Railway's problem to schedule, not a flag's to serialise, and that flag only ever worked because the deployment happened to be single-replica.

**The build context is the repository root**, because the image needs `services/agri-data-service/{pyproject.toml,uv.lock,src/}` and the service's recorded Railway root directory (`/infra/cron-ingest`, `docs/deployment.md:389-392`) cannot see them. `railway.json` pins `dockerfilePath` accordingly, but **the Root Directory change is a dashboard change the owner must make**; until it lands the image cannot build on Railway. Nothing in the repo-root `railway.json` (plantgeo-main) is touched.

## mtbs.py

Captures MTBS burned-area boundaries one ignition-year cohort at a time, and emits rather than persists.

**The endpoint moved, because the old one never existed.** The retired `src/lib/server/services/mtbs.ts:21-22` pointed at `MTBS_Polygons_v1` on the NIFC ArcGIS Online org; that service answers HTTP 400 "Invalid URL" and is absent from that org's 862-service catalogue. The authoritative source is the USGS EROS / USFS GTAC partnership product at `apps.fs.usda.gov/arcx/rest/services/EDW/EDW_MTBS_01/MapServer/63` ("Burned Area Boundaries (All Years)"). Its field names are lowercase (`fire_id`, `year`, `ig_date` as an integer `YYYYMMDD`) — the capitalised `Fire_ID`/`Ig_Year`/`BurnBndAc` spellings belong to the direct-download shapefile, so both are accepted and a schema flip fails a pinned test rather than passing silently.

**`data_available_at` is the release publication date and never `Ig_Date`.** An ignition date leaks the ~18-month mapping lag into every model that consumes MTBS, invisibly — the forecast simply looks good. The subtlety the plan did not anticipate is that **MTBS publishes quarterly, not annually** (early February, May, August and November), and one fire year accretes across many quarterly releases over two to four years. There is therefore no single "annual release date" for a year still being mapped. `MTBS_ANNUAL_RELEASE_DATES` records, per fire year, the publication date of the *last* release that added fires from it — late by construction, which under-claims what we knew but can never leak hindsight. Each entry is commented with its dated announcement from `mtbs.gov/announcements`, cross-checked against the ScienceBase revision history for DOI `10.5066/P9IED7RZ`. **A year not in that table raises `MtbsReleaseNotPublishedError` with no fallback** — not `Ig_Date`, not `now()`, not `Ig_Year + 18 months`. Fire years 2023 onward are excluded on purpose: MTBS says it is still mapping them. Two tripwires back the table up: the resolved date must lead the cohort's last ignition by at least 180 days, and must not fall within 60 seconds of `now()`. `observed_from`/`observed_to` carry the ignition window — *when it happened* — and stay strictly separate from `data_available_at`, *when we could have known*.

**Fire year 2019 was absent by oversight, not by evidence, and was added 2026-08-10.** The table held 2018, 2020, 2021 and 2022, so the gap read as deliberate; it was not. The 27 September 2021 release states it added "the remaining 457 fire mappings for 2019, bringing the total release for 2019 fires to 810" — the same explicit "remaining" wording that makes 2018 the strongest entry here — 353 (21 April 2021) + 457 = 810 reproduces the announced total exactly, and the next release names only fire year 2020. 63 in-bbox fires landed on the first run. **2023 and 2024 were re-checked at the same time and still have no defensible date**: the 7 April 2026 release added 482 more 2023 fires and 147 more 2024 fires, 2024 arrived as "Initial Assessments" (MTBS's preliminary tier), and `mtbs.gov/data-availability` targets "end of FY2026" — a date not yet reached. `test_every_tabled_release_date_leads_its_own_fire_year_by_more_than_the_floor` now pins both years out of the table so a future run cannot quietly adopt one. The full release chronology, including the four pre-existing entries re-derived from their quarterly parts, is captured in `docs/reports/evidence/mtbs-release-announcements-2026-08-10.md`. **Fire years 2016 and 2017 look similarly resolvable and were deliberately left alone** — the 29 August 2019 release claims to contain "all 2017 fires" — but nobody has done for them what was done here, so they stay out until someone does.

**`ingest-mtbs` had no scheduled producer until `infra/cron-mtbs/railway.json` (2026-08-10).** The verb is deliberately excluded from `ingest-all` because MTBS publishes quarterly and a run re-reads cohorts that almost never move, so an hourly shape would be pure waste — but the consequence nobody noticed was that *nothing* ran it: 478 rows landed once on 2026-08-05 and the layer sat untouched. The cron is weekly (`55 7 * * 2`), which picks up a new quarterly release within a week while staying far cheaper than the daily lanes; minute 55 is the only slot no other `infra/cron-*` service uses, and hour 7 UTC is clear of `cron-validate` (06:00) and `cron-ndvi` (05:00). A weekly re-read is safe precisely because the writer's diff rejects an unchanged payload and the geometry adapter confirms an unchanged shape.

**The fetch pages with `orderByFields=fire_id`, and proves completeness three ways.** ArcGIS paging is unordered by default, so without a deterministic sort a page boundary can repeat or skip rows; no other service in this repo pages this host family, so there was no precedent to copy. Every page therefore sorts on `fire_id`, and every `fire_id` is asserted unique across pages — a duplicate proves the ordering was unstable and raises. Completeness is gated on an authoritative `returnCountOnly` issued *before* paging: if the reassembled feature count does not equal it, the capture raises. `exceededTransferLimit` is honoured as a keep-paging signal (its absence is inconclusive, never "complete"), and a short page only ends the loop when the flag also agrees. The bug this replaces is `mtbs.ts:51` sending `resultRecordCount: "500"` with no `resultOffset`: the Pacific Northwest holds 3,824 MTBS perimeters all-time, so that call was silently discarding roughly 87% of them. Two live-only behaviours are also handled: the host refuses an oversized polygon response with HTTP 500 rather than truncating it (1,000 rows fails; 50 rows is a 10.7 MB answer that succeeds), so pages default to 50 and halve on refusal down to a floor of 5; and transient 429/5xx answers get bounded retry with backoff, mirroring `execution/geospatial_capture.py`.

**Severity is never fabricated.** `mtbs.ts:78` did `SEVERITY_MAP[severityCode] ?? "unburned"`, and because `unburned` is a real class (code 1) an unrecognised code became a legitimate-looking observation. Worse than the plan assumed: **no MTBS perimeter service publishes a `Severity` attribute at all** — severity is a separate thematic raster product — so that fallback was mislabelling *every* feature. `SEVERITY_CLASS_BY_CODE` is preserved verbatim for the day a code appears, and a present-but-unrecognised code raises `MtbsUnknownSeverityCodeError`. An absent attribute yields `severity_class=None`, which means "the source publishes none", not "unburned"; the per-fire dNBR thresholds the layer really does carry (`low_threshold`, `moderate_threshold`, `high_threshold`, `dnbr_offst`, …) are kept instead. Likewise a missing or blank `fire_id` raises through `identity.build_burn_severity_identity` — a key is never synthesised from coordinates or a payload hash.

**Type-2 change detection versions on the mapping, not on geometry.** The service exposes no per-fire release or version field (the full 29-field list is pinned in the test). MTBS revises perimeters between releases under a stable `fire_id`, so each record carries a `mapping_revision` composed of `map_id`, `asmnt_type`, `pre_id`, `post_id` and `perim_id` — MTBS's own mapping identifiers, which move when a fire is re-mapped. Comparing geometry floats is never the trigger.

**Why it emits rather than persists.** The module is pure capture: no `AsyncSession`, no ORM import, no writes. Three independent reasons. `validate_phase_one_geojson_payload` accepts only `Point` geometry (`execution/contracts.py:725-726`) and MTBS is polygons; `publish_source_release` stores artifacts with `storage_class="database_inline"` (`source_ingestion.py:336-338`) while a real cohort is 20-40 MB, four to eight times that budget and squarely against the plan's ">1 MB goes to R2" rule; and `geo.geometry` plus the `agri` FK repoint had not landed. So each capture writes a deterministic raw GeoJSON payload plus a `SourceIngestionPlan` sidecar under `settings.local_execution_root`, and sets `requires_object_storage` so nobody inlines it into Postgres. The sidecar is only written when the operator supplies `--reviewed-by`: `SourceDefinition.reviewed_at`/`reviewed_by` are governance facts this module may not invent, which is also why the governed identity is `build_mtbs_source_definition(review)` rather than a bare constant. Capture paths and `release_set_key` are scoped by a bounding-box fingerprint, because two extents of one release are different immutable content and must not overwrite each other. `license_name` records both instruments — the U.S. federal public-domain USGS/USFS data release and, separately, the USDA FS EDW ArcGIS Server hosting — inside the 255 characters that `source_release.license_snapshot` allows; an Esri ArcGIS Online mirror of the same layer exists and is deliberately unused. Those strings become immutable on first publication, so reword them before, not after.

## geometry.py

The Type-2 dimension adapter. It answers one question per place — *is the shape we hold still the shape upstream publishes?* — and does the smallest honest thing: open a first version, confirm an unchanged one, or close the old one and open a successor. Everything else in this section exists because getting that answer wrong is not a duplicate row, it is an **interleaved version chain**: a fabricated history that still renders correctly on the map and is far harder to detect than a duplicate.

**The change-detection predicate is `=` first, `ST_Equals` second, and never a tolerance.** `open_version.geom = resolved.geom` is PostGIS exact equality — same coordinates in the same order, the operator that backs `GROUP BY` on a geometry column — not a bounding-box test, which is what `=` meant before PostGIS 2.4 and what a reader skimming it will assume. It is the cheap path and it is the path virtually every tick takes: of the 23,690 features joined to their dimension row in production on 2026-08-04, **23,684 were byte-equal, 0 were byte-different but topologically equal, and 6 differed genuinely** (all WFIGS perimeters that had drifted while nothing opened a v2 for them). `ST_Equals` is the second stage and it is not decoration — the same shape re-serialised with reordered rings, a different vertex start, or `Polygon` flipped to `MultiPolygon` is byte-different and topologically identical, and versioning on that would mint a spurious version for **every feature on every run**, which is precisely the fabricated history the dimension exists to prevent. Both operands are guaranteed valid (`geo.sync_feature_geom_from_properties` repairs or raises), so GEOS cannot throw a topology exception at us here; that guarantee is load-bearing for the second stage. There is deliberately **no snapping and no tolerance**. A tolerance is a governance decision — "a movement this small does not count" — and a gauge that is genuinely relocated three metres is a new version, not noise. If a tolerance is ever wanted it belongs in a producer's own contract, named and defended, not hidden in a shared predicate.

All seven of this module's statements now live in `sql/ingest/` — `lock_geometry_keys`, `classify_geometry_versions`, `close_geometry_versions`, `insert_geometry_versions`, `confirm_geometry_versions`, `select_current_geometry_ids` and `link_feature_geometry` — each carrying its own clause-by-clause walkthrough. The rationale comments that used to sit above the constants moved into those files.

**The candidate geometry is read out of `geo.features.geom`; it is never re-parsed from the stored GeoJSON.** `StoredFeatureGeometry(feature_id)` makes the SQL join to `geo.features` and take the column. This is the whole reason the predicate above works: `geo.sync_feature_geom_from_properties` already parsed, SRID-checked, `ST_MakeValid`-repaired and `ST_CollectionExtract`-ed that geometry, and `scripts/backfill-geometry.sql` seeded the dimension from that same column. A second normalisation chain in this module would be a third implementation of a contract that already has two, and it would disagree with the seeded rows on exactly the invalid-polygon cases that matter — every repaired WFIGS perimeter would read as "changed" on the first forward tick and the whole dimension would fork. `GeoJsonGeometry(text)` exists only for a place that owns **no** `geo.features` row, which today means a raster grid cell, and it applies `ST_SetSRID(ST_GeomFromGeoJSON(...), 4326)` and nothing else: a synthesised cell is a rectangle we generated, so if it needs repairing that is a bug in the generator and it should fail, not be silently mended. Centroid and `geom_kind` are likewise computed in SQL (`ST_Centroid`, a `CASE` on `GeometryType`) — an unmapped geometry type yields a NULL `geom_kind` and trips the NOT NULL, which is the same refusal `backfill-geometry.sql` makes rather than inventing a kind.

**`observed_at is None` is the SQL literal `-infinity`, and a version boundary is never a write clock.** `timestamptz_literal` is the only place that mapping lives. It is not `datetime.min`: year 1 is a real, sortable instant that would quietly order itself among genuine observations, whereas `-infinity` sorts below everything and reads as "we never learned when". `geo.features.created_at`/`updated_at` are unusable here for the reason the `writer.py` section already gives — `updated_at = now()` on every refresh makes it "last touched" — and `now()` is an ingestion time, not an observation time. The only input is `FeatureIdentity.observed_at`, which every producer builder already computes and which, before this module existed, was used for a freshness check, echoed into `properties.observedAt`, and then thrown away. `run_clock` is a write clock and is therefore allowed exactly one destination: `last_confirmed_at`, documented in the DDL as staleness only and never a validity bound.

**A shape that moved but cannot be dated does not get a version.** If the geometry differs and the incoming `observed_at` is not strictly greater than the open version's `version_valid_from` — which includes every `observed_at is None`, since `-infinity` is greater than nothing — the adapter records the outcome `undatable`, leaves the chain untouched, and does **not** touch `last_confirmed_at` (claiming "seen unchanged" about a shape that visibly changed would be a lie). It does not fabricate a boundary from the run clock, and it does not force the write through and let `ck_geometry_version_order` or `uq_geometry_version` decide. This is the one case where the honest answer is to keep serving a version we know is stale and to make the count visible, because the alternative — cutting the chain at an invented instant — is unrecoverable once committed.

**Close, then insert, and mint the successor's uuid in Python.** `ck_geometry_supersede` makes a half-closed version unrepresentable and every CHECK is immediate, so `version_valid_to` and `superseded_by` must be written by the *same* statement; `uq_geometry_current` is a partial index and rejects a second open row, so the successor cannot exist yet when that statement runs. The only legal order is the one the DDL comment spells out, and it forces the successor's `geometry_id` to be known before its row exists — hence `uuid4()` in `_plan_versions` rather than `gen_random_uuid()` in the INSERT. The `superseded_by` FK is `DEFERRABLE INITIALLY DEFERRED` for exactly this, and validates at COMMIT. Rehearsed against production inside `BEGIN ... ROLLBACK` on 2026-08-04: `wfigs:2026-ORVAD-260204` closed at its own `polygonDateTime` of `2026-08-04 06:32:18+00` and reopened at the same instant, contiguous and non-overlapping, with `SET CONSTRAINTS ALL IMMEDIATE` accepting the deferred FK before the rollback.

**The advisory lock is natural-key-scoped, because `writer.py`'s is not.** `_LOCK_EVENT_KEYS` locks `layer_id:external_id` — one feature — which says nothing about two concurrent runs racing on one *place*, and says nothing at all in the raster case where many features share one cell. `lock_geometry_natural_keys` takes its own `pg_advisory_xact_lock` over `geo.geometry:<natural_key>`, namespaced so it cannot alias a feature lock, and always in sorted order so two runs holding overlapping sets cannot deadlock. `uq_geometry_current` and `uq_geometry_grid_cell` are the backstop, not the plan; the `ON CONFLICT (natural_key) WHERE version_valid_to IS NULL DO NOTHING` on the insert is what makes a lost race lose harmlessly, and the authoritative `geometry_id` a caller links is always re-read afterwards rather than assumed from the insert.

**A grid cell is a place, from day one.** `GridCell` carries `grid_name`, `cell_key`, `resolution_metres` **and the cell's own GeoJSON**, because a raster sample's feature geometry is a point *inside* the cell and the dimension row must hold the cell. `geometry_key_for` therefore keys a celled place as `producer:grid_name:cell_key` rather than by the sample's own identity, which is what lets ten thousand soil samples across five hundred cells resolve to five hundred geometry rows that many features point at — `geo.features.geometry_id` is deliberately not unique. `_unique_by_natural_key` collapses a batch to one request per place, so the same cell appearing a hundred times in one batch is versioned once; the same cell reappearing in a *later* batch simply classifies as unchanged and confirms, which is why `writer.py`'s position-based `INSERT_BATCH_SIZE` chunking needs no cell awareness.

## source.py

One `Protocol` plus small frozen dataclasses, so adding a source is filling in a shape rather than writing a sixth pipeline. It states the producer token, the target layer (as a **method**, not a constant, because every live module resolves its layer from the environment at call time and a cron variable change must not need a restart), the channel, the freshness rule, the declared shape, how to fetch a current window, how to fetch a past one, and how to map one upstream record to a `FeatureWrite` carrying its `FeatureIdentity`.

**A source that has no history says so in typed terms and is refused before it is called.** `HistoryCapability(supported=False)` will not construct without a reason string, `require()` raises `HistoryUnavailableError`, and `FunctionSource.history_capability()` refuses a source that claims history but supplies no history fetcher. The failure mode being designed out is a `fetch_history` that returns `[]` — a backfill that walks two years, writes nothing, and reports a clean run is indistinguishable from a backfill that worked, and that is how a gap gets certified as complete.

**The Protocol was shaped around the live modules, not the other way round.** `FunctionSource` composes a source out of a module's existing callables — `resolve_firms_layer_name`, a fetcher, `build_fire_detection_write` — so `firms.py`, `usgs_nwis.py`, `open_meteo.py` and `wfigs.py` adapt by binding, with no edit to their internals. `FreshnessRule` delegates to `policy.is_fresh_observation` rather than re-deriving it, and carries `accepts_undated_records` because WFIGS and MTBS legitimately produce `observed_at is None` while FIRMS and Open-Meteo legitimately must not. `usdm.py` is **not** adaptable and should not be forced: it writes polygons straight into `geo.drought_areas` through `PostgresDroughtStore.store_release` and never touches `FeatureWrite` or `geo.features`, so its eventual dimension integration is a separate hook inside that store, not a `FunctionSource`.

## backfill.py

One driver for date-ranged history and one pass for repair, both writing through `writer.py` so there is exactly one write path and therefore exactly one place geometry versions are maintained.

**Chunks are anchored at the window start with a fixed step, and that is what makes resuming possible without a checkpoint table.** `history_chunks` walks `start, start+chunk, ...` clipped to the end, so the same window always yields the same boundaries and an operator who resumes from a boundary reproduces the identical grid. Re-running a completed chunk is a no-op rather than a duplicate for free, because the writer refreshes by `properties->>'id'` and its diff rejects an unchanged payload, and the geometry adapter classifies an unchanged shape as `confirmed`. Memory is bounded by processing one chunk at a time and never accumulating records across chunks. The default window is **two years back from the run date**, expressed as `DEFAULT_HISTORY_YEARS` through `default_history_window`, a parameter with a default rather than a constant buried in a loop; `subtract_years` lands 29 February on 28 February instead of raising.

**Repair rebuilds each identity through `identity.py` and refuses to invent a date.** `run_geometry_repair` walks `geo.features WHERE geometry_id IS NULL AND geom IS NOT NULL` on an `id > cursor` keyset, so a row it cannot repair advances the cursor instead of looping forever, and commits per page. `repair_identity` re-mints the identity from the stored payload — FIRMS from `satellite`/`acqDate`/`acqTime` plus the stored coordinates, USGS NWIS from `siteNo`/`updatedAt`, Open-Meteo from the stored point's latitude and longitude plus `observedAt`, WFIGS from `uniqueFireIdentifier` and `polygonDateTime` — and then **asserts that the rebuilt producer-local id equals the `properties->>'id'` already on the row**. That assertion is the guard against the interleaving hazard: all four producers round-trip today (the stored id embeds exactly the fields the builder consumes, and `format_coordinate` is deterministic on the stored raw ordinates), so a mismatch means the payload and the identity contract have drifted, and if the key cannot be trusted the parsed instant cannot be trusted to belong to it either. In that case the **stored id is kept verbatim** — preserving byte-identity with the key the forward path mints — and the version dates to `-infinity`. Never `features.created_at`, never `now()`, never a fallback field the producer's own builder does not use.

**Repair deliberately disagrees with `scripts/backfill-geometry.sql` about WFIGS, and will not reconcile it.** That script dates a perimeter whose `polygonDateTime` is absent by `coalesce(polygonDateTime, fireDiscoveryDateTime)`; `build_fire_perimeter_identity` has no such fallback and returns `observed_at=None`. Thirteen already-seeded WFIGS rows are dated by `fireDiscoveryDateTime` as a result. Repair honours `identity.py` and would date those `-infinity` — but it only ever touches rows where `geometry_id IS NULL`, so it never rewrites an existing version's `version_valid_from` and the two never collide. The divergence is recorded here rather than "fixed" because rewriting thirteen `version_valid_from` values on rows facts may already pin is a governance decision, not a tidy-up. The script's FIRMS branch has the mirror-image gap — it reads raw `observedAt` only, without `_firms_observation_time`'s `acqDate`+`acqTime` fallback — which happens to fire on zero rows today. Both are why the go-forward path calls `identity.py` and does not reimplement field selection in SQL a third time.

## writer.py: the geometry seam

The dimension is maintained **inside `_ingest_resolved_batch`, after the insert and refresh loops and before the single `session.commit()`**. That ordering is the point: a feature row and the geometry version it points at are written in one transaction, so a crash cannot leave a feature linked to a version that rolled back, nor a version orphaned from the feature that justified it. Putting it after the commit — the obvious-looking alternative — reopens the exact gap this work closed.

Nothing on the existing path was rewritten by the geometry seam. `_INSERT_FEATURES` is untouched and `_REFRESH_FEATURES` keeps the asymmetric `- 'geometry' - 'geometry_repaired'` strip the section above pins as load-bearing; the advisory lock, the `INSERT_BATCH_SIZE` batching, the realtime publish and the return value all behave as before. **The refresh was later made set-based** — it was one `UPDATE ... RETURNING` per already-existing feature inside the batch loop, so a re-walked FIRMS day (~21k detections, and the archive lane retries constantly) paid one round trip per row against the Railway proxy. It is now the same statement shape as `_LINK_FEATURE_GEOMETRY`: one `UPDATE geo.features AS feature ... FROM unnest(external_ids, next_properties)` per batch, `RETURNING feature.id, pending.external_id`. The predicate, and therefore which rows change, is character-for-character what it was. Two things about the Python side are deliberate rather than incidental: the returned ids are folded into a `dict` with `setdefault` and then re-read **in `refreshable` order**, so the counted-and-published rows keep their old order, and a layer holding duplicate rows for one external id — which the old `.first()` collapsed silently — is still counted exactly once. Two additive changes carry the seam: `_SELECT_EXISTING_EXTERNAL_IDS` now also selects `id` (the existing set-of-external-ids logic reads the same, it just no longer discards the row id it was already fetching), and `FeatureWrite` gains `grid_cell: GridCell | None = None`, trailing and defaulted so every existing construction is unaffected. `run_clock` is resolved once per `ingest_features` call so an entire run shares one `last_confirmed_at`.

**Geometry is maintained for every write in the batch, not only for the rows the writer counted as written.** A refresh whose properties did not change returns no row and is correctly not counted, but the place was still seen upstream and still deserves its `last_confirmed_at` touch — and, if the dimension had drifted out of step with `geo.features.geom`, that is the tick that catches it. The known limit, inherited rather than introduced: because `_REFRESH_FEATURES`'s diff strips `geometry` from both sides, a change that moves **only** the geometry and no other property never updates `geo.features.geom` at all, so the adapter cannot see it either. For the three point layers this is unreachable (the coordinates are part of the natural key, so a moved point is a different feature), and for WFIGS a redrawn perimeter moves `polygonDateTime` in practice. If that ever stops being true, the fix is a geometry-aware predicate in the refresh gate, not a tolerance in `geometry.py`.

## geometry.py: the dimension is keyed by the place, not by the observation

`geometry_key_for` returns `FeatureIdentity.entity_key`, not `natural_key`. The two differ for exactly the two producers that embed a reading time in their id — `usgs-nwis` is `siteNo:updatedAt`, `open-meteo` is `lat:lon:observedAt` — and `entity_local_id` is `None` everywhere else, so `firms`, `wfigs`, `mtbs` and `usdm` are unaffected by construction and their two keys coincide.

**Keying by `natural_key` made the Type-2 apparatus unreachable for half the live producers.** A key that embeds the reading time has never been seen before, so `_CLASSIFY_GEOMETRY_VERSIONS` always finds no open version, `_plan_versions` always takes the "opened" branch, and `geometry_unchanged` / `successor_is_datable` are dead code. Measured read-only against production on 2026-08-04, before the change: `usgs-nwis` held **14,494 rows for 899 gauge sites**, `open-meteo` **2,787 for 116 sample points**, and `SELECT count(*) FROM (SELECT natural_key FROM geo.geometry GROUP BY 1 HAVING count(*) > 1)` returned **0** — not one chain in the 23,690-row dimension had ever reached a second version, and every row was open. `uq_geometry_current`, whose stated meaning is "the one current geometry for this place", answered "what is the current geometry of gauge 13081500?" with twenty rows. `geo.features` for water-gauges is retained on the order of days while `geo.geometry` is never pruned, so the fan-out was not merely inert, it grew thousands of rows a day carrying no new information.

**This is not the same defect as interleaving, and it is the same family.** No two producers cross-contaminate a chain — the key stays producer-namespaced either way — but the dimension silently failed to do the one thing it exists for: collapse repeated observations of one place into one version history. That failure renders correctly on the map, which is exactly what makes it worth naming here.

**`natural_key` is untouched, and that is the point of the split.** `producer_local_id` still keys `geo.features` byte-identically to the TypeScript `featureId`, so `features_layer_external_id_unique` still accumulates a reading series rather than collapsing to last-reading-wins. `src/lib/server/db/AGENTS.md` §"Three producers are v1-only" argues against making a *gauge's feature key* its site, and that argument still stands and is not what this change does. What that section could not anticipate is `entity_key`, which is precisely the seam that lets the fact table stay per-reading while the dimension goes per-place.

**Existing rows had to be re-keyed, not left alongside.** `scripts/backfill-geometry.sql` seeded every row as `producer || ':' || (properties->>'id')`, so a dimension left un-migrated would hold both key shapes for one producer — every historical row stranded open forever while a parallel entity-keyed chain grew beside it. `scripts/rekey-geometry-to-entity.sql` is the paired one-shot: it collapses each entity's rows onto the earliest, repoints `geo.features.geometry_id`, deletes the redundant rows and renames the survivor, with a guard that refuses to collapse any entity holding more than one distinct shape (a genuine relocation deserves a real supersession chain, built deliberately). Production measured 0 such entities and 0 closed versions at the time of writing, but the script re-measures rather than trusting that.

**A batch that collapses to one place uses the LATEST observation in it.** Now that many features per tick share one key, `_unique_by_natural_key` can no longer keep whichever request it happened to see first: that would let an older reading's shape supersede a newer one purely on arrival order. It keeps the request with the greatest `observed_at`, and an undated request never displaces a dated one — `observed_at is None` means `-infinity`, which is below every real instant and must not win a tie-break it would lose in SQL.

## writer.py: the forward path reports what the dimension did

`upsert_geometry_versions` names an action per place and `run_geometry_repair` already folded those into its job result; the forward path read only `geometry_id` and returned nothing, so the most important of the four actions was invisible in production. `_maintain_batch_geometry` now returns the per-action tally, `_ingest_resolved_batch` carries it out alongside the written rows, and `ingest_features` sums it across every batch and logs `geometry_versions_maintained` once per call. `FeatureWriter` still returns `int`, so no job signature changed.

**`undatable` gets its own WARNING naming every key.** It is the one outcome that describes a permanent divergence: the shape upstream publishes differs from the version we hold, and the producer supplied no instant later than that version's own start, so there is no honest boundary to cut the chain at. The adapter correctly leaves the chain alone and correctly declines to touch `last_confirmed_at` — but that means the same divergence is re-detected on the next tick, and the next, forever, and without the log line an operator has no way to learn it exists. Two production rows are in exactly that state today: `wfigs:2026-IDBOD-265460`, whose `polygonDateTime` is JSON null, and `wfigs:2026-ORBUD-002693`, whose `polygonDateTime` equals its `version_valid_from` to the second and so fails the strict `>` in `_CLASSIFY_GEOMETRY_VERSIONS`. The second is also a live counter-example to the claim in "writer.py: the geometry seam" above that a redrawn WFIGS perimeter moves `polygonDateTime` in practice — it does not always, and the tick that catches the drift now says so out loud.

## identity.py: WFIGS dates from its discovery time when the polygon carries none

`build_fire_perimeter_identity` coalesces `polygonDateTime` to `fireDiscoveryDateTime`, which is the same rule `scripts/backfill-geometry.sql` applies and the reason that script gives for it: a perimeter cannot predate the discovery of its own fire, and a JSON null polygon time would otherwise open v1 at `-infinity`, rendering that fire as an active perimeter at **every past time-slider position for the life of the warehouse**, because `version_valid_from` is never rewritten once a fact cites the row. 13 of 112 production perimeters carry a null `polygonDateTime` and all 13 carry a parseable discovery time; 0 of the 112 seeded rows sit at `-infinity`.

**This supersedes the "repair deliberately disagrees with the seed script about WFIGS" paragraph in the `backfill.py` section above.** That paragraph declined to reconcile the divergence, and its reasoning — that rewriting thirteen `version_valid_from` values on rows facts may already pin is a governance decision, not a tidy-up — is still correct and still respected: nothing here rewrites an existing row. What changed underneath it is that `identity.py` stopped being a read-side detail and became the *minting* site for every forward geometry version, through `geometry.feature_geometry_request`. A rule that was merely inconsistent when it only affected repair becomes an active defect once the forward path uses it, and it would fire on the next perimeter WFIGS publishes with a null polygon time — 11.6% of the current population fits that shape. Only `observed_at` moves; `producer_local_id` is untouched, so the TypeScript key-parity guarantee is unaffected. A perimeter carrying *neither* field still dates to `-infinity`, and an unparseable `polygonDateTime` still raises rather than silently falling through to the discovery time.

## source.py: one truncation rule, and the forward driver that was missing

**A bitten record cap drops the OLDEST observations, never an arrival slice.** `select_writes` is now the single place a fetched window becomes the writes a run persists: it applies `accepted_writes`, and when more survive than `max_records` allows it keeps the newest by `observed_at` — undated below every dated record — while preserving arrival order among the survivors, so only *which* records survive changes and no downstream ordering assumption moves. `_run_backfill_chunk` previously did `accepted[: request.max_records]` on an unsorted list, which for `vegetation` and `sensors` (the two sources actually wired to `run_source_backfill`) silently discarded whichever records happened to arrive last regardless of age. `firms.py` already encoded the correct policy inline with the comment "so truncation drops the oldest detections rather than whichever satellite happened to resolve last"; that rule now lives once, and `ndvi.py` and `sensors.py` call it instead of re-implementing the cap.

**A backfill chunk that hit the cap FAILS and writes nothing; the forward window does not.** Keeping the newest is the right rule and the wrong report. Because `_truncation_rank` keeps the newest, what a bitten cap deletes from a multi-day chunk is not a thinner sample of every day — it is the chunk's **oldest days, whole**. `_run_backfill_chunk` reported that as `status="ingested"` with `details.rejected: 0`, because truncation is not a rejection and nothing else counted it. Measured on production 2026-08-05 against data the FIRMS archive walk itself wrote: the week 2022-09-04..09-10 holds 60,779 published detections, so a `--chunk-days 7` walk under the default 10,000-record cap writes 10,000 and silently discards 50,779. That is the exact "clean run over a thinner record than the publisher served" failure commit `7ebd6c7` set out to end, reintroduced in the new path. `SelectedWrites` now carries `dropped` (a count, not a flag — 50,779 lost and 3 lost are not the same run to look at), the chunk fails, **writes nothing**, and its `reason` names the narrower `--chunk-days` that would fit under the cap. Writing the survivors and failing anyway was considered and rejected: the days that fitted would look complete, and re-walking is idempotent only if it actually happens. The walk continues past a failed chunk — one over-large chunk must not erase the chunks after it — and `merge_backfill_results` folds the run to `failed` with the summed `dropped`. `run_source_job` deliberately keeps reporting `ingested`: a forward window is whatever the producer publishes as "now", there is no narrower window to retry with, and failing there would only turn the hourly cron red on a busy fire day with no action to take. The retry advice is always *strictly* narrower than the chunk that failed, so a one-record overshoot cannot advise the same size back and loop.

**`run_source_job` is the forward half of the contract `run_source_backfill` only served the past half of.** `IngestionSource.fetch_current` had no caller anywhere in the package: the only driver was `run_source_backfill`, which calls `fetch_history`, so every adopting module hand-rolled fetch + accept + truncate and the Protocol member was unreachable. `run_source_job` mirrors the backfill driver exactly — resolve the bounded bbox or skip with `UNCONFIGURED_BBOX_REASON`, fetch the current window, `select_writes`, write through the same `FeatureWriter`.

**The four bespoke jobs are deliberately NOT migrated onto it in this pass, and that gap is real.** `firms.py`, `usgs_nwis.py`, `open_meteo.py` and `wfigs.py` keep their own `run_*_ingestion_job`, and the section above describing them as adapting "by binding" is aspirational — no `FunctionSource` exists for any of them. The blocker is not effort, it is a missing channel: `run_fire_ingestion_job` reports `reason="Unavailable FIRMS products: VIIRS_NOAA21_NRT"` when part of the constellation is down, and `FetchCallable` returns only `Sequence[UpstreamRecord]`, so a run where two of three satellites failed would report a clean "ingested" through the shared driver. That is the same failure `HistoryCapability.__post_init__` was written to prevent on the history side, and migrating those four before a partial-availability reason channel exists would reintroduce it on the current-window side. Add the channel first, then migrate.

## open_meteo.py: two endpoints, two contracts

The module now serves both the forecast endpoint (`api.open-meteo.com/v1/forecast`, one current
observation per sample point, 128 KB / 5 s budget) and the **archive** endpoint
(`archive-api.open-meteo.com/v1/archive`, years of daily rows for up to 200 locations at a time,
64 MB / 300 s budget). They share nothing but the host vendor; keeping one bounds object for both
would either starve the archive or hand the forecast path a 300-second timeout.

**`models=era5_land` is mandatory, not a default.** The archive endpoint's default is `era5` at
**0.25 degrees**. Only the explicit `era5_land` value returns the 0.1-degree ERA5-Land product whose
layer definitions match the CDS variables (`soil_moisture_0_to_7cm` == `volumetric_soil_water_layer_1`).
Measured: a request for 43.375/-116.375 answers from 43.40001/-116.399994, the nearest 0.1-degree
node. `cell_selection=nearest` is sent explicitly for the same reason -- the parameter exists and its
other values (`land`, `sea`) would relocate a coastal request to a cell the caller did not name.

**`fetch_archive_daily` returns raw text, not parsed JSON.** The governed lane checksums exactly what
arrived, so `fetch_bounded_json` -- which parses and discards the body -- cannot be used.

**429 is classified, never generic.** `_rate_limit_scope` reads the provider's own `reason` string and
returns `minute`, `hour`, `day` or `unknown`. An unrecognised body is `unknown`, never optimistically
`minute`: the caller's backoff table is what decides whether waiting is worth it, and guessing the
cheapest scope would make a daily wall look like a transient blip. `OpenMeteoRateLimitError` carries
both the scope and the provider's wording so an operator sees which window is exhausted.

`RATE_LIMIT_SCOPE_MARKERS` is ordered **day, hour, minute** -- least retryable first -- because the
bodies are not mutually exclusive: "Daily API request limit exceeded. Please try again in 60
minutes." names two windows, and a minute-first scan would classify a daily wall as a blip.
Matching the bare noun as well as the adjective is also required: "…try again tomorrow." contains no
substring "day", which is how a daily wall got classified `unknown` in a live run.

Multi-location responses are a JSON **array**, and the provider omits `location_id` on the first entry
while numbering the rest from 1. Order is the contract; the archive lane validates it rather than
trusting it.

## open_meteo.py: the paid archive host is an environment fact, and two URLs exist on purpose

Open-Meteo's Professional tier is a **different host** plus one query parameter:
`customer-archive-api.open-meteo.com/v1/archive?...&apikey=<key>`. `resolve_open_meteo_api_key()`
reads `OPEN_METEO_API_KEY` from `os.environ` at call time, matching `firms.py::_require_api_key`,
except that **absent is not an error**: the free host is the supported default, the published repo
has no key, and the already-validated 16-cell probe was fetched keylessly.

The module therefore exposes **two** builders over one private implementation, and the distinction is
load-bearing rather than cosmetic:

| Function | Host | Credential | May be persisted |
|---|---|---|---|
| `archive_daily_url` | the one this process would call | never | **yes** — this is the canonical URL |
| `archive_daily_request` | the one this process would call | when configured | **no** — wire only |

The safe one keeps the plain name, so a caller that reaches for the obvious function persists the
safe value. `_archive_daily_parameters` builds and validates only the eight governed parameters;
appending the credential lives in `archive_daily_request` alone, so `archive_daily_url` is
structurally incapable of emitting a key rather than merely choosing not to. `ArchiveDailyRequest`
exists so the fetcher resolves the credential **once** and gets back both the host to record and the
URL to send; two separate environment reads could disagree.

**The credential is appended last, after all eight governed parameters.** `urlencode` preserves dict
insertion order, so the keyless URL is byte-identical to what the pre-paid-tier builder produced.
That is not a nicety: `agri.source_release.query_parameters.request_url` on already-persisted probe
releases contains that exact string, and `test_the_keyless_canonical_url_is_byte_identical_to_the_pre_paid_tier_builder`
pins it literally.

**`models` is a required keyword argument on both archive builders, with no default.** The
endpoint answers a variable the selected model does not publish with HTTP 200 and an all-null
series, so a defaulted model is a silent data-loss bug rather than a convenience -- see
execution/AGENTS.md §historical_open_meteo, "the archive model decides which variables have values
at all". `OpenMeteoArchiveModel` is a closed `Literal`; the caller states `era5_land` (0.1-degree
soil layers) or `era5` (0.25-degree parent, the only one carrying radiation).

**`archive_daily_url` accepts an explicit `base_url`** and validates it through
`require_archive_base_url`, which admits only the two reviewed hosts. Persistence replays a local
cache receipt, so the host must come from the receipt rather than from the current environment;
that receipt is a file, therefore untrusted, therefore checked before it can become provenance.

## open_meteo_endpoint.py: one transport half, four products

Open-Meteo publishes each product on its own host pair (free + `customer-`), but the *transport*
contract is identical across all of them: multi-location by comma-joined coordinates, at most 200
locations per request, `cell_selection=nearest` pinned, a JSON array response, a 429 whose body names
the exhausted window, and raw text returned so the caller can checksum exactly what arrived. This
module is that shared half; a product adapter beside it (`open_meteo_flood.py`,
`open_meteo_air_quality.py`, `open_meteo_ensemble.py`) contributes only its hosts, its byte budget,
and its own parameter list.

**`OpenMeteoEndpoint` is generic over its base-URL Literal, and `require_base_url` is a method on it.**
That is not a typing flourish. The rule "only a reviewed host may become provenance" was previously
restated once per product -- `require_flood_base_url`, `require_air_quality_base_url`,
`require_ensemble_base_url` -- each identical except for the narrowed return type. Four copies of one
provenance rule is exactly the duplicated truth `engineering-principles.md` §1 forbids: a correction
to any one of them would leave three lanes still accepting whatever the old rule accepted. Making the
endpoint generic lets `OPEN_METEO_FLOOD_ENDPOINT.require_base_url(value)` return
`OpenMeteoFloodBaseUrl` from the one implementation, so the narrow type survives into a receipt with
no per-lane copy. The per-product `Literal` aliases stay -- they are the closed host set a receipt
field is typed by -- and the module constants are annotated with them so the endpoint carries the
literal type rather than widening to `str`.

**`_rate_limit_scope` is imported from `open_meteo.py`, privately and deliberately.** One 429
classifier, measured against live bodies (see "open_meteo.py: two endpoints, two contracts"), must
serve every endpoint. A per-endpoint copy would drift the moment the provider reworded one wall.

**Two URL builders, same rule as the archive lane.** `open_meteo_product_url` is credential-free and
persistable; `open_meteo_product_request` resolves the key once and returns both the host to record
and the URL to send. A product adapter wraps these rather than reimplementing them, so a lane cannot
accidentally persist a credentialed URL.

## open_meteo_flood.py

GloFAS river discharge, daily, from `flood-api.open-meteo.com/v1/flood`. 64 MB / 300 s: a four-year
daily replay for 200 reaches is roughly 16 MB of JSON and the full seven-variable ensemble-statistic
set multiplies that, so the ceiling leaves headroom without ever permitting an unbounded body.

**`models` MUST be sent and is threaded from the plan, never defaulted.** v3 and v4 sit on different
native lattices (0.1 vs 0.05 degrees), so this value decides which grid box a returned reach belongs
to and therefore which spatial support the row may claim. `flood_daily_parameters` takes the model as
an argument and validates it against `GLOFAS_MODELS`, which is what makes it impossible for a v3 plan
to silently fetch v4 -- contrast `_archive_daily_parameters` in `open_meteo.py`, which hard-codes its
model because that lane serves exactly one product.

**No `timezone` parameter is sent.** The flood endpoint is daily-only, so there is no sub-daily
instant to place; the lane instead asserts `timezone=GMT` / `utc_offset_seconds=0` in the response.
Unverified against the live upstream: the multi-location array shape and `location_id` ordering. Both
fail loudly on the first real chunk rather than silently.

## open_meteo_air_quality.py

CAMS, hourly, from `air-quality-api.open-meteo.com/v1/air-quality`. 32 MB / 300 s bounds **one cell
block times one day block**; because CAMS answers hourly, 24 values per variable per cell per day
means the plan's `chunk_day_count`, not the cell block alone, is what keeps a response under the
ceiling. Tune the plan, never this constant.

**`domains` MUST be sent and is threaded from the plan.** `cams_global` is a 0.4-degree lattice and
`cams_europe` a 0.1-degree one, so the domain decides which spatial support a returned value may
claim. `HOURS_PER_DAY` lives here rather than in the execution lane because it is a property of what
this endpoint returns, and the reduction that consumes it is the lane's.

**`timezone=GMT` is pinned** so a returned hourly stamp needs no session-timezone reasoning; the
execution lane then buckets by the ISO prefix the publisher named and validates a dense 24-hour axis
before it reduces anything.

## open_meteo_ensemble.py

Per-member ensemble output, hourly, from `ensemble-api.open-meteo.com/v1/ensemble`. 64 MB / 300 s
holds a 25-cell, 5-variable, 16-day, 51-member chunk with headroom -- the body is one member series
per variable per cell, so it is `member_count` times a deterministic response.

**`models` is REQUIRED by this endpoint and is threaded from the plan through
`ensemble_hourly_parameters` into both `ensemble_hourly_url` and `ensemble_hourly_request`,
deliberately NOT hard-coded** the way `_archive_daily_parameters` hard-codes
`OPEN_METEO_ERA5_LAND_MODEL`. Each ensemble sits on its own native lattice AND carries its own member
count, which is the denominator of every quantile the execution lane derives; a defaulted model would
silently re-scale every summary.

**`timezone=GMT` is pinned** so a returned instant is compared instant-by-instant against a
GMT-anchored axis with no timezone arithmetic.

**Member series are named, not pattern-scanned.** `ensemble_member_field_name(variable, number)`
renders the provider's own two-digit `<variable>_member01` format and refuses a number outside
1..`MAX_ENSEMBLE_MEMBER_NUMBER`. Under "Deliberate deviations": the execution lane reads members by
exact name and requires exactly the declared count, rather than summarizing whatever series arrived.

## validation.py

**It is a package, and the layout follows the seams rather than the line count.** `constants.py` holds
the values mirrored from `src/lib/server/services/environmental-read-model.ts` (the two axis rules, the
USGS sentinel, the published status), the scan bounds and the validity vocabulary; `errors.py` the two
typed refusals; `models.py` the stream catalog and every `to_summary()`; `completeness.py` the pure day
algebra (gap walk, continuity clustering, density floor, verdict) with no I/O and no SQLAlchemy import at
all, which is what makes it the most reusable code in `ingest/`; `report.py` the assembly of one stream's
row; `queries.py` the nine `sql/ingest/*.sql` bindings plus the two inline `SET LOCAL` statements;
`rows.py` the typed column readers; `markdown.py` the renderer.

**The async scan band stays in `__init__.py` on purpose, and moving it is a silent test defeat.**
`tests/test_ingest_validation.py` rebinds `MAX_OBSERVED_DAY_ROWS` with `monkeypatch.setattr` on the
package to prove a day series that reaches its cap is refused rather than truncated. A reader defined in a
submodule resolves its own module global, so the patch would land on a name nothing reads and the test
would pass while exercising the 200,000-row default. The same rule governs `routes/health/`. Everything
else is re-exported from `__init__.py`, including the private names the tests reach by attribute
(`_SERVER_DAY` and its eight siblings), so no importer changed when the module became a package.

**The gap walk runs on the stream's declared cadence, not on the calendar.** A daily grid is only correct
for a daily stream. Against `drought_areas` -- one USDM release every Tuesday -- it counted the six
ordinary days between consecutive releases as missing, so a stream that had published every release it
ever owed reported roughly 6/7 of the calendar absent (54 phantom days across ten on-time releases), and
`vegetation` reported 4/5 of it on its five-day Sentinel-2 revisit. Those phantom days drowned the real
ones. `find_observation_gaps` now takes `publication_cadence_days` (defaulting to 1, so a stream that
declares no cadence is walked exactly as before) and opens a gap only when the silence runs past one whole
cadence period; what it counts is the grid points `last publication + n * cadence` that passed with
nothing published.

**A gap therefore carries two numbers, and they mean different things.** `ObservationGap.days` stays the
calendar length of the silence, because that is what `decide_verdict` compares against the declared
cadence -- a weekly stream that skips one release is still INCOMPLETE on 13 days of silence against a
7-day cadence. `ObservationGap.missed_publications` is what the stream actually owed inside that silence
and is what `CompletenessReport.missing_day_count` sums, so one skipped weekly release reads as 1, not 13.
On a daily cadence the two are equal by construction, which is why the JSON gap triple and the Markdown
gap line are unchanged for every daily stream: both renderers name the second number only when it differs.

## layer_binding.py

Nine producers each resolved their target layer with the same three lines -- an `os.environ.get(VAR, "").strip() or DEFAULT`
paired with a `*_LAYER_VARIABLE` / `DEFAULT_*_LAYER_NAME` / `*_CHANNEL` constant triple. `LayerBinding(variable, default, channel)`
is now the single definition, and each module keeps its own `resolve_*_layer_name()` one-liner plus its old constant names as
aliases off the binding, so nothing outside these modules had to change (`agent/tools.py` imports two of those resolvers, and four
tests import the `DEFAULT_*` / `*_VARIABLE` spellings).

**The environment read stays at call time and that is the whole point of the shape.** `resolve()` is a method on a frozen binding
constructed at import; it is not a value computed in `__init__`. The rule this preserves is the one `policy.py`'s paragraph above
already states and `usgs_nwis.py`'s and `open_meteo.py`'s paragraphs each widened once: the cron container is long-lived, so an
operator renaming a layer through `FIRE_PERIMETERS_LAYER_ID` must not need a restart. A naive extraction that resolved at
construction would still pass every test that only checks defaults, which is why
`test_the_variable_is_read_at_call_time_never_at_construction` constructs a binding first and sets the variable afterwards.

**The channel is pinned to the DEFAULT layer name, never to the resolved one, and that asymmetry is deliberate rather than an
oversight.** Every live binding's channel is `layer:<default>`, and renaming the layer through the environment leaves the channel
alone -- which is correct, because the map subscribes to a fixed channel string and `writer.py` validates it against
`^layer:[a-z0-9-]{1,100}$` before accepting a write. Deriving the channel from `resolve()` would let one environment variable
silently re-point live invalidation at a channel nothing is listening on.
`test_a_channel_is_pinned_to_the_default_layer_name_not_to_the_resolved_one` pins that for all nine.

## upstream_retry.py

`is_retryable_failure`, `jittered_retry_delay_seconds` and `BUSY_MESSAGE_PATTERN` were hand-duplicated in `wfigs.py` and
`evacuation_zones.py`, and the `wfigs.py` section above closes by saying the two copies were kept structurally identical on
purpose so a future extraction would be a mechanical move. This is that move.

**The extraction is FROM the widened WFIGS version, and evacuation-zones was brought up to it rather than the reverse.** By
2026-08-10 the two copies had drifted: WFIGS carried the post-incident budget (6 attempts, a doubling `RETRY_BASE_DELAY_SECONDS`
capped at `RETRY_MAX_DELAY_SECONDS`, a `RETRY_WALL_CLOCK_CEILING_SECONDS` backstop checked against a threaded monotonic clock)
while `evacuation_zones.py` still carried the pre-incident 3-attempt fixed `(1.0, 2.0)` tuple -- the exact shape that lost every
hourly `plantgeo-cron-fire-perimeters` run through a sustained ArcGIS throttle. Extracting the narrower copy would have quietly
un-fixed the incident. `RetryLadder` owns the shape and its post-incident defaults; `UpstreamRetryPolicy` adds the two per-source
strings; `retry_upstream` is the loop, byte-for-byte the WFIGS one with the event name and the log context parameterised.

**`wfigs_upstream_retry` and its four fields are unchanged.** The event name is a policy field rather than a derived string
precisely so an operator's existing log filter keeps matching; the caller's own fields arrive through `context` (WFIGS passes
`offset`) and the loop adds `attempt`, `error` and `elapsed_seconds`. Rendered field ORDER was checked rather than assumed and is
not something either version controls: structlog's console renderer sorts keys alphabetically, so the line reads
`attempt= elapsed_seconds= error= offset=` before and after this change alike. What an operator's filter matches on -- the event
name and the four key names -- is byte-identical. `evacuation_zones_upstream_retry` keeps its own name and gains
`elapsed_seconds` on the same line rather than a new event.

**What changed behaviourally for evacuation-zones, stated plainly.** Its attempt budget went 3 -> 6; its backoff went from a fixed
two-entry tuple to 1s/2s/4s/8s/16s doubling capped at 20s, each jittered 0.5-1.5x as before; a 60s wall-clock ceiling now bounds
the loop so a sustained throttle gives up inside one cron tick instead of holding a container open; `fetch_evacuation_zone_page`
and `fetch_evacuation_zones` gained a trailing `monotonic` parameter so a test can fake elapsed time without a real sleep. Nothing
about *what* is retried moved: a malformed or schema-mismatched `UpstreamPayloadError` still never matches `BUSY_MESSAGE_PATTERN`
and is still never retried, and a sustained throttle still raises rather than becoming a silent empty success or a partial write.
That last property is load-bearing for this source in particular -- an evacuation layer reporting "ingested, wrote nothing"
during an upstream outage is the one failure mode it must not have -- so it has its own named test.

**Per-source values stay per-source; the ladder stops being reinvented.** `MAX_RECORD_COUNT` (100 for WFIGS, 1,000 for
evacuation zones) and `MAX_PAGES` (200 vs 20) are unchanged and remain module constants. `MAX_ATTEMPTS` is still each module's own
name, now bound to its policy's ladder, so the two cannot silently disagree about a budget they were meant to share. A source that
genuinely needs a narrower budget passes its own `RetryLadder` rather than writing a third loop.

## arcgis.py

Three modules paged the same ArcGIS FeatureServer mechanics independently. This module is the shared vocabulary: the
error document ArcGIS hides behind HTTP 200, the `exceededTransferLimit` flag in both the GeoJSON and the Esri JSON envelope,
feature-collection and polygon-ring validation, the optional attribute readers, the envelope query URL, and the `resultOffset`
walk. `wfigs.py` and `evacuation_zones.py` now hold only their own field projections and their own per-source constants.

**MTBS is the reference implementation and was deliberately NOT rewired onto this module.** Its pager is the strongest of the
three -- deterministic `orderByFields=fire_id`, an authoritative `returnCountOnly` completeness gate, cross-page `fire_id`
uniqueness, page-size halving on the host's HTTP 500 refusal -- and this module's shape is derived from it. It is not *called by*
it, for reasons that are contract differences rather than effort. MTBS speaks raw `httpx` with `params=`/`raise_for_status()` and
raises `MtbsTruncatedCaptureError`; the other two speak `fetch_bounded_json` under an `UpstreamBounds` byte cap and raise
`UpstreamPayloadError`. More importantly the two loops answer different questions: MTBS proves one frozen release complete against
a count taken before paging, while WFIGS and evacuation zones page a live feed under a deliberate `resolve_max_source_records()`
cap and report `truncated=True` -- a count-equality gate there would turn every concurrent upstream edit into a red cron. Its
`page_is_truncated` also reads the flag truthily where `page_exceeded_transfer_limit` reads it as `is True`; the two agree on
every payload ArcGIS actually emits, and reconciling them is a change to a completeness gate rather than a tidy-up, so it was left
alone. `watersheds.py` is likewise not a consumer: it addresses basins by explicit `objectIds` batches because that layer answers
HTTP 500 when asked to sort while returning geometry, which is a deliberate rejection of offset paging and not a duplicate of it.

**Three MTBS mechanics are wired as parameters and left switched OFF for the two live feeds; that is a stopping point, not an
oversight.** `ArcGisEnvelopeQuery.order_by_fields` is `None` for both: ArcGIS paging without a deterministic sort may repeat or
skip rows, so turning it on is a genuine improvement, but it also changes which records survive a bitten record cap on a live
feed, and neither `attr_UniqueFireIdentifier` nor `GlobalID` has been probed as a sortable field on its host. The
`returnCountOnly` pre-check is not offered to them at all, for the contract reason above. Page-size halving is not enabled either:
`wfigs.py`'s own section states that a page still too heavy for `WFIGS_BOUNDS.max_bytes` must fail that page rather than be
silently permitted through, and halving would change that documented answer. Each of the three needs a live probe and an owner
decision, not a refactor's discretion.

**The parse split is behaviour-identical, and the reason it is safe is that one message covers every shape rejection.**
`parse_feature_collection` validates each feature's `type` before the caller's loop reaches any of them, where the old code
validated type, properties and key one feature at a time. A payload with two different defects therefore raises on a different
feature than it used to -- but both modules raise the same `UNEXPECTED_SHAPE_REASON` string for all of them, so nothing an
operator or a test can observe changed. `WFIGS_ERROR_PREFIX` and `UNEXPECTED_SHAPE_REASON` are now named constants in `wfigs.py`
because the shared parser takes them as arguments; `evacuation_zones.py` already had the latter.

**Not extracted, and named here so the next pass does not have to rediscover it:** `epoch_milliseconds_to_iso` (`wfigs.py`) and
`epoch_milliseconds_to_datetime` (`evacuation_zones.py`) still duplicate `MAX_JAVASCRIPT_EPOCH_MILLISECONDS`, `EPOCH` and the
same guard chain. They differ in return type and in what each wraps in its `try`, so folding them is a small behaviour question
rather than a move, and it was left for a wave that can answer it.

## history declarations, wave 2026-08-10

`weather-observations`, `fire-perimeters` and `evacuation-zones` declared no `HistoryCapability` at all -- not even a typed
refusal -- so they were structurally incapable of backfill with nothing stating why. Under the owner's max-available-per-layer
policy a reflexive `supported=False` is the wrong default: a false refusal is the same "gap certified as complete" failure the
type exists to prevent, wearing a different mask. Each declaration below is **declaration only**: no fetcher, lane or
`FunctionSource` was wired this wave, and none of the three appears in `commands._build_backfillable_sources()`. A declaration
with `supported=True` and no fetcher cannot silently produce an empty walk, because `FunctionSource.history_capability()` raises
`SourceContractError` for exactly that combination the moment anyone composes one.

**`weather-observations` -- `supported=True`, rolling floor.** Open-Meteo's forecast endpoint (`api.open-meteo.com/v1/forecast`,
the endpoint this job already reads) serves a rolling past window whose documented `past_days` maximum is 92
(open-meteo.com/en/docs, read 2026-08-10 -- documentation-sourced, **not** live-probed). `weather_history_capability(now)`
therefore resolves `earliest` per call rather than freezing it at import, exactly as `sensors.nws_sensor_source` does for NWS'
six-day retention. The far deeper ERA5/ERA5-Land archive is in this same module and is deliberately **not** this floor: it is a
different product -- daily aggregates on a 0.1-degree lattice, against the sub-hourly point `current` block this layer stores --
and filling `weather-observations` from it without a stored product discriminator would repeat the MODIS-into-VIIRS mixing the
`firms.py` section above documents, on a layer whose serving side has no product filter either.

**`fire-perimeters` -- `supported=True`, floor 2020-01-01.** WFIGS publishes historical perimeter services distinct from the
`_Current` feed this job reads, and the IRWIN-integrated interagency data model those services carry begins with the 2020 fire
year. That floor is documentation-derived and **not** live-probed as of 2026-08-10; it under-claims rather than over-claims, in
the same spirit as `MTBS_ANNUAL_RELEASE_DATES`. NIFC's separate `InterAgencyFirePerimeterHistory` archive reaches back to 1878
and is deliberately **not** this floor: it publishes no `attr_UniqueFireIdentifier`, so ingesting it under this producer would
fork the layer's key rather than deepen it, and `build_fire_perimeter_identity` would have nothing to key on. **A real history
source exists here and is unimplemented** -- wiring the archive service is scheduled work, not a closed question, and it needs a
live probe of the service URL and its field spellings before the floor above should be trusted operationally.

**`evacuation-zones` -- `supported=False`, and the reason names the upstream fact.** Oregon publishes
`Fire_Evacuation_Areas_Public` as a current-state hosted view: its definition query drops an area once the upstream integration
stops re-confirming it, and no attribute records when a level was raised, lowered or retired, so a past evacuation level cannot
be reconstructed from it. No archive service of historical Oregon evacuation levels is published (checked 2026-08-10), and the
module's existing note above already records that no equivalent government-run aggregator exists for Washington, Idaho or western
Montana. The only history this layer has is what the `geo.geometry` Type-2 chain has accumulated since ingestion began. This is
the one of the three where a refusal is the honest answer, and it is also the one where a wrong `supported=True` would be worst:
a life-safety layer must not advertise a backfill that would quietly write nothing.

## Deliberate deviations, wave 2026-08-10

Recorded here rather than in the "Deliberate deviations from the TypeScript" list above because none of these is a divergence
from the retired TypeScript -- they are deviations from the stated intent of this extraction wave.

- **`is_retryable_failure` and `BUSY_MESSAGE_PATTERN` are no longer importable from `wfigs.py` or `evacuation_zones.py`.** Nothing
  in `src/` imported either from those modules; only the two test files did, and their two byte-identical predicate tests are
  consolidated into `tests/test_ingest_upstream_retry.py` with both producers' busy wordings pinned. Re-exporting the names for
  compatibility was considered and rejected: an unused import kept alive only to preserve an import path is the duplication this
  wave set out to remove, wearing an alias.
- **The three MTBS pager upgrades are parameterised and left off.** See the `arcgis.py` section above for each one's specific
  blocker; all three need a live probe or an owner call, and enabling any of them silently would be a behaviour change to a
  production cron shipped inside a refactor.
- **MTBS's own pager and `watersheds.py` are untouched by the `arcgis.py` extraction.** Both are documented above with the
  contract difference that makes them not-a-duplicate.
- **Three history capabilities are declared with no fetcher behind them.** `WFIGS_HISTORY_CAPABILITY` and
  `weather_history_capability()` state what the upstream publishes, not what this package can currently walk. Declaring
  `supported=False` instead would have been a false refusal under the max-available-per-layer policy; the guard that keeps the
  declaration honest is `FunctionSource.history_capability()`, which refuses a source claiming history with no history fetcher.

## One gap rule

There were two, and the weaker one was the settler. `validation/completeness.py` walked a stream's own
publication cadence; `reconcile.window_coverage` had its own day-by-day comparison with the cadence
implicitly fixed at 1. That second implementation was a strictly weaker special case of the first --
correct only while every registered lane's stream published daily, and wrong in the SETTLING direction the
moment one did not. A five-day window of the five-day-revisit `vegetation` stream that published once owes
nothing more; the cadence-blind rule called it partial and would have left it queued forever, and the
symmetric failure (a wider cadence marking a real hole covered) is the one that writes a silent lie.

`_silences` in `completeness.py` is now the single walk. It returns each run of calendar days nothing was
published in, together with the first cadence grid point inside that run, and two renderers sit on it:
`find_observation_gaps` (the report's calendar runs, unchanged public signature) and
`missing_publication_days` (the individual owed days a backfill lane has to re-plan). `window_coverage`
calls the second one, so the report and the settler cannot drift into two meanings of "missing". Both
lanes registered today declare a daily cadence and `lane_publication_cadence_days` defaults to daily for a
lane no stream claims, so this substitution changed no current behaviour -- it removed the trap, not a
live bug. The default direction is deliberate: too SHORT a cadence demands more coverage and costs a
re-walk, too long settles a window over days nothing ever published.

The day census under both of them is also one statement now. `sql/ingest/observed_layer_days.sql` and
`sql/ingest/feature_observed_days.sql` were the same three filters at two scopes, agreeing by inspection
and by a comment asking the next reader to keep them agreeing. They are one file, `observed_days.sql`,
whose single `{layer_scope}` slot is filled at import from two constants in `validation/queries.py` --
the same load-time slot `store_drought_area.sql` uses for its replace predicate, and NOT a bound
parameter, because an "either the parameter is null or the column matches it" predicate cannot use the
index whose leading column is `layer_id` under a generic plan. `sql/AGENTS.md`'s LOADED rule is what
forces the shape: a file may be loaded by exactly one `load_query_sql` call, so `queries.py` loads it,
formats it twice, and `reconcile.py` imports `OBSERVED_DAYS_FOR_LAYER` rather than reading the file again.

### The census was sharded for one day, and the sharding was the wrong fix (2026-08-15 to 2026-08-16)

`observed_days.sql` over the firms layer's whole span measured 81 to 101 seconds on prod against the 120s
transaction statement timeout `apply_statement_timeout` pins, so the census did not merely run slowly, it
failed outright whenever anything else was competing for the database. The response was a fan-out:
`observed_day_shards` cut the lane's floor-to-today span into 730-day pieces, `observed_layer_days`
gathered them behind an `asyncio.Lock` (one `AsyncSession` is one connection, and SQLAlchemy refuses two
concurrent executes on it), and `jobs-pulse` grew a `_probe_census_shards` helper that re-read the whole
census a second time, per lane, per tick, to report `census shards=N slowest_shard_seconds=S`.

**The slowness was a missing index, not a missing partition.** `geo.feature_observation_day` is IMMUTABLE
and PARALLEL SAFE, so it can carry an expression index; `ix_features_layer_observation_day` over
`(layer_id, geo.feature_observation_day(properties))` now exists in production and the planner uses it as
an Index Cond on the day predicate. Re-measured against prod on 2026-08-16 with that index present: ONE
statement 68.7s, thirteen shards 95.3s, `shared_blks_read` a wash (331,152 against 332,076). Sharding
never reduced the work; it added roughly 27 seconds of per-statement overhead on top of it. 68.7s is 57%
of the timeout. So the fan-out, the lock, the shard constant and the probe are all gone.

**What survived, and why it must keep surviving.** The day BOUNDS are the point of the revert, not a
casualty of it: `{day_scope}` in `observed_days.sql` and `_DAY_RANGE_SCOPE` in `validation/queries.py`
are exactly the predicate the new index serves, and the map's date slider wants a bounded range for the
same reason. `_DAY_RANGE_SCOPE` is inclusive at BOTH ends. What was removed is the SHARDED CENSUS; what
was never in question is bounded querying. The row-cap refusal is likewise unchanged and is still per
EXECUTION of the statement -- there simply is only ever one execution now.

The lesson worth keeping is the diagnostic one. A statement that is slow because it reads too many rows
and a statement that is slow because it reads the right rows the wrong way look identical from the
application side; only `shared_blks_read` told them apart, and it was not measured until after the
fan-out shipped. Measure the buffers before partitioning anything.

## jobs-plan-gaps: the half of the loop that was missing

Nothing could turn a detected gap into a work item. `validate-streams` finds gaps and exits 0 on them by
design, `reconcile_lane` only ever REMOVES work, and `jobs-plan-lane` only ever appends whole windows
BELOW today -- so a hole discovered in the middle of an already-`succeeded` run was unreachable by every
verb in the package. `jobs-plan-gaps` is the inverse of `_mark_windows_reconciled` and reuses its
statement shape.

**It plans onto the lane's own grid and never a second one.** `map_days_to_grid` inverts the arithmetic
`lane_windows` used to build the grid (floor plus grid index times window days) and then CHECKS its answer
by asking the chosen window for its own days, so a drift between the two formulas fails loudly rather than
mis-keying a shard. A shard planned by this verb is byte-identical to one `jobs-plan-lane` would have
planned, which is what makes `ON CONFLICT (job_run_id, shard_key) DO NOTHING` a no-op rather than a
parallel key space. Days above the newest whole window and days below the floor are REPORTED as
unplannable rather than dropped: the first belong to the forward hourly cron and a trailing partial
re-keys itself every day, the second are outside what the lane declared.

**Only `succeeded` is converted.** `queued`/`retry_wait`/`deferred` are already claimable, `leased`/
`running` are held by a live fence, `dead_letter` is the evidence that every attempt failed, and
`cancelled` is an operator's decision. All four are reported by name, because "we found a gap and can do
nothing about it" must not have to be inferred from silence.

## Reopening a window: two ledger facts that make the naive version a silent hole

**`attempt_count` is never reset; `max_attempts` is raised instead.** `claim_work_item` refuses a shard
whose `attempt_count` has reached `max_attempts`, so a reopened shard that had spent its budget would sit
in `queued` forever, unclaimable -- a hole wearing the word "reopened". Zeroing the counter is worse than
useless: the next claim derives its attempt NUMBER from it and `uq_job_attempt_item_number` is unique per
work item, so a reset makes the shard collide with a stored attempt row and never run again. Raising the
ceiling by the lane's own declared budget is the same primitive `defer_work_item` already uses (see
`jobs/AGENTS.md`, "A deferral must not spend the retry budget") and keeps attempt numbers dense-unique.
`GREATEST` keeps it monotone so a shard that deferred its way to a high ceiling is never quietly lowered.

**The walk generation exists because resume position outlives a reopen.** `latest_checkpoint_cursor`
returns the newest `job_checkpoint` row for the work item, unconditionally, and a window that COMPLETED
left its last checkpoint pointing at its FINAL chunk -- the handler returns `completed` with no cursor on
the last chunk, so nothing overwrites it. A five-day FIRMS window reopened over four missing days would
therefore walk day five, find no further chunk, and succeed again over the same hole: the exact failure
this whole package exists to make impossible, reintroduced by the verb meant to fix it. So
`reopen_gap_windows.sql` bumps the payload's `walk_generation`, `archive_walk_handler` stamps that
generation onto every cursor it writes, and `effective_cursor` discards a cursor whose generation
disagrees. It discards the cursor WHOLE, not just its chunk marker, because the running record totals
beside it belong to the same superseded pass. Absent on both sides reads as generation 0, so every window
planned before this existed behaves exactly as it did.

Appending a corrective checkpoint instead was considered and is not available: `fk_job_checkpoint_attempt_fence`
requires a real `job_attempt` row behind every checkpoint, and fabricating an attempt would write a worker
id, a fencing token and a wall-clock duration into the ledger for work no process performed -- the same
refusal `reconcile.py` already makes for its own marker. Scoping the cursor read to the current generation
inside `latest_checkpoint_cursor.sql` would need a column the schema does not have.

**What is deliberately not touched on a reopen:** `fencing_token` (monotonicity is the whole mechanism),
`checkpoint_sequence` (the next checkpoint must not collide under `uq_job_checkpoint_item_sequence`),
`started_at` (stamped once, "when this shard first did anything"), and `last_error_class`/
`last_error_summary` (the record of how it failed before, which is history rather than current state).
`completed_at` and `progress_fraction` ARE cleared, because a queued row carrying a completion time makes
every operator query that reads "completed" disagree with the status beside it; the fact that it had
completed is written into the payload marker, and every `job_attempt` row it produced is untouched.

## The scheduled loop, and what is not yet true about it

`infra/cron-maintain-firms/` and `infra/cron-maintain-streamflow/` run `jobs-plan-gaps --apply` then
`jobs-reconcile-lane --apply` for one lane each, daily at 07:17 and 07:47 UTC -- off the five-minute grid
because every such minute is already taken by an existing cron service, and after `cron-validate`'s 06:00
report so the two read the same day's warehouse. Chained with `&&` and not `;`: both verbs exit 0 on
"nothing to do", so the only thing that breaks the chain is a genuine ledger failure, which is exactly
when a cron run should go red.

**A `railway.json` configures a service that already exists; it does not create one.** Until somebody
provisions those two Railway services, the loop is code that runs correctly when invoked by hand and is
not running on a schedule. `infra/cron-maintain-firms/AGENTS.md` says the same thing; do not read either
file as evidence that the loop is live.

**What has NOT been proved about the reopen path.** Every test here answers `AsyncSession.execute` from a
recording stub, so no bind in `reopen_gap_windows.sql` has ever reached a PostgreSQL type resolver. That
is the precise gap that let `mark_windows_reconciled.sql` pass its dry run and fail its first production
`--apply` with `IndeterminateDatatypeError` on 2026-08-07. Every parameter in the new statement is CAST at
its use site for that reason and a test asserts it, but a real-database pass is still owed: the local
warehouse on 127.0.0.1:5442 was not running when this landed, and no disposable database was reachable.

## The contract these producers serve

`docs/layer-lane-standard.md` is the end-to-end contract every lane here must satisfy -- horizon,
gap-to-work loop, governed absences, three crons, slider registration, agent tools. A producer that
ingests correctly and is absent from the slider capability catalogue is not a finished layer.
