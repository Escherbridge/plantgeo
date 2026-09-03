---
type: track-evidence
track: gapless_parquet_publication_20260901
slice: p5
status: activated_with_fenced_legacy_objects
observed_at: 2026-09-02
---

# Executor-only scheduler handoff

## Verdict

`plantgeo-job-executor` is PlantGeo's sole production scheduler and durable invocation owner.
Railway cron scheduling is rejected. The reviewed release is on `main`, the exact executor release
is active, and Railway reports no scheduled services in the environment.

All six legacy scheduled/one-shot writer objects remain present but fenced: `cronSchedule: null`, a
no-op start command, `restartPolicyType: NEVER`, and zero retries. They are not scheduler fallbacks.
`plantgeo-ingest-cron` must remain inert until its hidden CDS credentials and service-reference
variables are promoted to stable owners. Each other object remains until its mapped executor lane
has an observed success and a removal receipt can be recorded.

No PostgreSQL data, R2 data, source adapter, ingestion command, manifest, checkpoint or durable
ledger is retired. Rollback disables an executor lane and diagnoses it in place. It never restores a
`cronSchedule`, reconnects a drain, redeploys an old writer or recreates a Railway service.

## Authority and observed boundary

| field | exact evidence |
|---|---|
| Railway project | `6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990` |
| Railway environment | `b7cfa813-8a5c-4fcd-80f2-cab736d840a7` |
| repository preflight base | `88dff29535339c08f97a55bf258417674268cd92` |
| preflight ingest deployment | `d3c6e254-b00b-43c5-93b8-c38040c14ad3`, observed `CRASHED` |
| later ingest read | preflight deployment shown as `REMOVED`; active deployment `80f43cf6-d0c9-449f-9efa-f8353e1d7519` shown `SUCCESS` at the same base while its invocation remained in flight and emitted source failures |
| executor service | `plantgeo-job-executor`, `565ecaad-9946-48f1-8a0b-28fa60494a16` |
| preflight executor deployment | `36e7a5ff-a5b2-466a-abc0-257f5e7db659`, `SUCCESS`, branch `codex/unified-data-lane-scheduler`, commit `b4ec9c77ca1c65f2a1d0dbf24e95acaa1210f1e1`; shadow evidence only |
| release commit | `e4490c3c2f2e23f75cc9d6e297f4be646e0e00a1`, exact `origin/main` |
| current executor deployment | `b1f35a20-6e05-48ff-9801-5235c9753a01`, `SUCCESS`, exact release commit above |
| current executor ownership | 38 registered responsibilities: 37 active executable lanes plus the terminal snapshot-only soil-moisture responsibility |
| Railway schedule census | production environment `scheduled=[]`; zero Railway cron schedules |
| activation variables | `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES`; `PLANTGEO_JOB_EXECUTOR_HANDOFF_ACKNOWLEDGEMENTS`; `PLANTGEO_JOB_EXECUTOR_POLL_SECONDS`; `PLANTGEO_JOB_EXECUTOR_MAX_LANES_PER_TICK` |

During preflight, the `environment-status` convenience read returned `You don't have the required role (viewer) on
this resource`. Service listing, per-service config, deployment history, status and bounded logs were
still readable and produced the matrix below. MTBS and SoilGrids are a Railway resolution edge case:
the service API returned a null schedule while their deployed repository configs and repeated log
start minutes proved the then-existing schedules. The post-handoff full environment read is
authoritative for current state and returns `scheduled=[]`.

## Authoritative responsibility matrix

The common executor envelope for every recurring row is one code-owned `agri.job_definition`, one
idempotent logical run key per cadence bucket, a fenced `job_work_item`, lease heartbeats, an
append-only checkpoint/event trail, five outer attempts with exponential backoff from 30 to 3,600
seconds, and terminal dead-letter visibility. A session advisory leader lock permits only one
executor tick to invoke child writers at a time. The inner writers keep their own bounded work,
natural keys, lane-day advisory locks, immutable object/completion receipts and marker-last
publication. The outer `ready` checkpoint deliberately allows replay after a process kill, so inner
idempotency is part of the contract rather than an exactly-once claim.

| existing Railway writer | command / product | cadence and source ceiling | executor registry mapping | checkpoint and concurrency fence | retry / dead letter | rollback and service disposition |
|---|---|---|---|---|---|---|
| `plantgeo-ingest-cron` (`3ae3cc37-c398-43fe-b74c-83e4da130423`) | Composite `ingest-all`; `parquet-catch-up-vegetation`; `jobs-pulse --time-budget-seconds 600`; `parquet-gap-fill --time-budget-seconds 900`. It covers FIRMS, NWIS PostgreSQL streamflow, Open-Meteo weather, WFIGS perimeters, USDM drought, Sentinel-2 vegetation, NWS sensors, evacuation zones, geometry repair, four durable worker lanes, four archive-maintenance passes, stream validation and every registered Parquet stream. | Configured hourly at `0 * * * *`, but `ingest-all` was measured longer than one hour and Railway skipped overlapping ticks. Each replacement carries the lane registry's source ceiling: daily products stop at calendar day minus their declared lag; drought preserves a four-day lag and weekly source cadence; static products use their source watermark; generic fire history retains its fixed direct-writer ceiling. | Atomic owner group: `postgres-firms`, `postgres-streamflow`, `postgres-weather`, `postgres-fire-perimeters`, `postgres-drought`, `postgres-vegetation`, `vegetation-catch-up`, `postgres-sensors`, `postgres-evacuation-zones`, `postgres-geometry-repair`; `jobs-matview-refresh`, `jobs-strategy-mv-refresh`, `jobs-firms-archive`, `jobs-streamflow-archive`; archive reconcile/plan-gap lanes and `maintenance-validate-streams`; plus every `parquet-<registered-slug>` lane. | One executor leader serializes invocation. PostgreSQL writers retain source-key diffs/upserts. Vegetation retains its durable pending queue. Inner job lanes retain `agri.job_*`. Parquet lanes run at most one day per turn under lane-day locks and marker-last receipts. | Each responsibility gets an independent outer retry/DLQ instead of sharing the failed macro's exit status. Existing inner retries and dead letters remain intact. | Disable the complete atomic executor owner group; do not partially transfer it. Never restore the cron. Service object is removable only after every mapped lane is seen running successfully. |
| `plantgeo-cron-mtbs` (`a683cc83-2b49-4276-a136-941e1b2cbe24`) | `agri-service data ingest-mtbs`; MTBS/burn-severity cohort capture. | Exact Tuesday 07:55 UTC (`55 7 * * 2`). MTBS publishes quarterly and accretes cohorts; weekly polling bounds discovery to seven days. A missing established source release date is a refusal, never an invented ceiling. | `mtbs-forward`, exact weekly cadence and phase, command timeout 1,800 seconds. | Executor lease/leader fence plus the writer's source identity/diff. Unchanged payload and geometry are idempotent no-ops. | Five outer attempts/DLQ plus the source command's bounded ArcGIS paging/retry. | Disable `mtbs-forward`; never recreate the weekly cron. Remove service only after an observed executor weekly-equivalent run. |
| `plantgeo-cron-soilgrids` (`0960aa81-4499-4cb1-9daa-3350eed4d654`) | `node scripts/warm-soilgrids.mjs 120`; six static ISRIC topsoil properties in `public.soil_grid_cache`. | Exact hourly `:25` (`25 * * * *`). Static lookup: no calendar settlement lag; the ceiling is the finite configured spatial-cell set minus cache-complete or governed no-data cells. Each turn is bounded to 120 cells, a 45-minute deadline and paced upstream requests. | `soilgrids-cache-warm`, executable in the dedicated combined executor image with the exact Node driver. Missed buckets coalesce to one current bounded cache-diff run because a static cell must be placed once, not once per missed hour. | Outer ready checkpoint/lease/leader plus the durable database cache census. `(lat, lon)` upsert is the idempotent publication/checkpoint; a restart recomputes only missing cache cells. | Driver throttling is bounded; nonzero exit enters five outer attempts and the executor DLQ. | Disable `soilgrids-cache-warm`; keep every cache row. Never recreate the SoilGrids cron. Remove service after the executor command and cache checkpoint are observed. |
| `plantgeo-fire-detections-forward` (`f4ad61fe-e71a-4776-b9d5-0b153c9ee5b7`) | `python -m agri_data_service.pipeline.direct.fire_detections`; settled FIRMS days to all R2 rungs. | Exact hourly `:15`; two-day settlement lag, bounded five-day lookback, and direct ownership from `2026-08-25`. Generic history stops at the preceding day. | `fire-detections-direct-forward`, exact command/cadence/phase and the same registry writer ceiling. | Executor leader/lease plus source writer's session advisory lane-day lock, all-rung receipts and completion marker last. | Bounded source/object retry remains; nonzero exit gets five outer retries and DLQ visibility. | Disable the direct executor lane; preserve the direct/generic date boundary. Never recreate the fire cron. Remove after successful executor publication/settlement observation. |
| `plantgeo-water-gauges-forward` (`40cb252b-e21c-4140-8d94-5db77eb2398d`) | `python -m agri_data_service.pipeline.parquet.water_gauges_forward`; one complete NWIS IV snapshot grouped by publisher day and merged to all rungs. | Exact hourly `:15`; source snapshot uses publisher timestamps. Direct ownership starts `2026-09-02` and the command rejects earlier publisher days. Generic daily-history repair is clamped to `2026-09-01`, in addition to its two-day settlement lag. Work is capped by `INGEST_MAX_SOURCE_RECORDS` and bounded per-day retries. | `water-gauges-direct-forward`, exact deployed command/cadence/phase, and `parquet-water-gauges`, the distinct history/repair duty in the ingest-owner atomic group. Both may activate together because their fixed date windows are disjoint and the elected executor also invokes them serially. | Executor leader/lease plus the water writer's lane-day advisory lock, per-day checkpoint events, content verification and all-rung marker-last publication. | Five bounded inner day attempts by default; incomplete exit enters five outer retries and DLQ visibility. | Disable `water-gauges-direct-forward`; preserve the `2026-09-02` partition boundary and never recreate the water cron. Remove only after every direct-owned day from that floor through the current allowed source ceiling is terminal and a source-direct executor run plus boundary/no-overlap proof is observed. |
| `plantgeo-soil-moisture-parquet-load` (`4a1413f1-5f96-44ea-853c-6a379c7673c4`) | Historical command `python -m agri_data_service.pipeline.parquet.soil_field_moisture_backfill`; completed immutable snapshot load. | No schedule. Terminal receipt: deployment `29c54089-79b1-45a4-8b9e-471369e2ce93` completed 1,556 days and 6,861,960 rows through `2026-08-02`. | `soil-moisture-parquet-backfill` is a registered `snapshot-only` terminal responsibility, not a fabricated recurring lane. Incremental soil-product scheduling is a different product responsibility and cannot be inferred from this completed service. | Immutable R2 parts/manifests and the completion log are the retained checkpoint. No new invocation is due. | No retry or dead letter is due after terminal completion; retained artifacts provide reconciliation evidence. | Re-read no schedule/no active run and immutable artifacts, then remove the inert service object. Rollback is artifact verification, never rerunning or recreating the one-shot service. |

The old `infra/parquet-drain/railway.json` is not a seventh live service: no
`plantgeo-parquet-drain` object appeared in the 14-service production inventory. Its `ALWAYS`
infinite loop is nevertheless a duplicate writer path and is removed from the repository. The
bounded `parquet-drain`/`parquet-gap-fill` implementation remains available; registered
`parquet-<slug>` backlog lanes own ongoing bounded repair. Reconnection of the historical service is
prohibited.

## Preflight production service census

This table preserves the preflight deployments. The first six rows are eventual removal candidates;
their current fence receipt follows the table. The remaining eight services are outside this
scheduler cleanup and must not be removed.

| service | service id | active deployment / status at read | classification |
|---|---|---|---|
| `plantgeo-ingest-cron` | `3ae3cc37-c398-43fe-b74c-83e4da130423` | `80f43cf6-d0c9-449f-9efa-f8353e1d7519` / `SUCCESS` at base, invocation observed in flight with failures | blocked legacy writer |
| `plantgeo-cron-mtbs` | `a683cc83-2b49-4276-a136-941e1b2cbe24` | `7b0c3182-60ef-4fb2-95dc-511743010972` / `SUCCESS` | blocked legacy writer |
| `plantgeo-cron-soilgrids` | `0960aa81-4499-4cb1-9daa-3350eed4d654` | `d4833ce0-a4ae-4b5e-87d9-f019078f744b` / `SUCCESS` | blocked legacy writer |
| `plantgeo-fire-detections-forward` | `f4ad61fe-e71a-4776-b9d5-0b153c9ee5b7` | `5e3ebe9f-5a26-449b-85d1-344c32a44c2a` / `SUCCESS` | blocked legacy writer |
| `plantgeo-water-gauges-forward` | `40cb252b-e21c-4140-8d94-5db77eb2398d` | `e04bb4b9-6b0a-4e4e-9b60-4eb82447f90e` / `SUCCESS` | blocked legacy writer |
| `plantgeo-soil-moisture-parquet-load` | `4a1413f1-5f96-44ea-853c-6a379c7673c4` | `29c54089-79b1-45a4-8b9e-471369e2ce93` / `SUCCESS`, completed one-shot | blocked until post-merge no-run/artifact check |
| `plantgeo-job-executor` | `565ecaad-9946-48f1-8a0b-28fa60494a16` | `36e7a5ff-a5b2-466a-abc0-257f5e7db659` / `SUCCESS`, shadow-only feature branch | preflight replacement evidence; superseded by current deployment `b1f35a20-6e05-48ff-9801-5235c9753a01` |
| `plantgeo-parquet-api` | `33aed861-af76-4fdd-a95e-784bdcc95e55` | `91b791ab-9552-4d31-9ff8-61a89c8f637e` / `SUCCESS` | serving; not a scheduler target |
| `plantgeo-main` | `fa08a3aa-6d1d-43eb-846b-15dbfd887d61` | `f232fb54-d7df-4fe2-a91b-fc90fce315fc` / `SUCCESS` | application; not a scheduler target |
| `plantgeo-martin` | `fe6ef46e-7b4c-41ef-8b64-5100a344c526` | `dc48f11a-1216-4934-9536-2a41e4f68a5b` / `SUCCESS` | serving; not a scheduler target |
| `aevani-web` | `b6c06bf1-f1f4-4733-a33d-0f88d178c2fc` | `de0db5ba-0168-4350-8dd9-414ecc17a38e` / `SUCCESS` | different repository/application |
| `plantgeo-spatiotemporal-db` | `1e166530-9c8a-4d4a-b685-a70c801fc449` | `1f33637e-bb81-410d-9066-770e778832dc` / `SUCCESS` | data-bearing; never remove here |
| `plantgeo-Redis` | `ae23c58e-b1e3-4c01-9d94-cd365550f363` | `dcc757e0-ead9-4226-8e52-9a89f4d251ee` / `SUCCESS` | cache/transport; not a scheduler target |
| `Aevani-Postgress` | `3e0ea761-f509-474e-97cf-0086acd9ab7a` | `b3a6ab66-145b-48ca-9dfa-db41e64c1094` / `SUCCESS` | affiliate data, out of scope |

### Current six-service fence receipt

The post-handoff re-read found the same six exact service IDs. Every row below has
`cronSchedule: null`, start command `sh -c 'echo retired-to-plantgeo-job-executor; exit 0'`,
`restartPolicyType: NEVER`, and zero restart retries.

| service | service id | remaining disposition |
|---|---|---|
| `plantgeo-ingest-cron` | `3ae3cc37-c398-43fe-b74c-83e4da130423` | inert credential/reference holder; do not delete until CDS secrets and service references move |
| `plantgeo-cron-mtbs` | `a683cc83-2b49-4276-a136-941e1b2cbe24` | remove after observed `mtbs-forward` success |
| `plantgeo-cron-soilgrids` | `0960aa81-4499-4cb1-9daa-3350eed4d654` | remove after observed `soilgrids-cache-warm` success |
| `plantgeo-fire-detections-forward` | `f4ad61fe-e71a-4776-b9d5-0b153c9ee5b7` | direct executor publication succeeded; retain until removal receipt is recorded |
| `plantgeo-water-gauges-forward` | `40cb252b-e21c-4140-8d94-5db77eb2398d` | remove after direct boundary and terminal-day proof |
| `plantgeo-soil-moisture-parquet-load` | `4a1413f1-5f96-44ea-853c-6a379c7673c4` | terminal one-shot; remove after immutable artifact re-read receipt |

## Repository scheduler disposition

The release removes these duplicate scheduling authorities after registry parity tests:

- `infra/cron-ingest/railway.json` and `infra/cron-ingest/Dockerfile`;
- `infra/cron-mtbs/railway.json`;
- `infra/cron-soilgrids/railway.json` and `infra/cron-soilgrids/Dockerfile`;
- `infra/parquet-drain/railway.json`;
- `services/agri-data-service/railway.fire-detections-forward.json`; and
- `services/agri-data-service/railway.water-gauges-forward.json`.

`services/agri-data-service/railway.job-executor.json` remains the sole scheduler service config and
uses a dedicated repository-root executor image. The release preserves the commands behind every
old entrypoint. A guard test enumerates tracked Railway JSON and fails if `cronSchedule` returns.
No-resurrection proof is therefore both a zero-result tracked-file scan and an executable test, not
a runbook promise.

The Railway source settings are part of the candidate identity: Root Directory `/`, Config-as-code
path `/services/agri-data-service/railway.job-executor.json`, and Dockerfile path
`infra/job-executor/Dockerfile`. All three must resolve together before the exact `main` deployment
can satisfy the `SUCCESS` gate.

## Post-activation and removal receipt

For each candidate service, record all of the following in one append-only operation receipt:

- project id, environment id, service id and service name;
- repository `main` commit and exact executor deployment id/status;
- old service active deployment id/status, resolved command and cadence;
- source responsibility, source-ceiling/settlement rule and executor lane id;
- last old-service log timestamp and terminal outcome;
- query time and result for running `agri.job_run`, live/unexpired `agri.job_work_item` leases and
  executor leader visibility across every `plantgeo.executor.%` definition name and every stored
  definition version, including superseded versions;
- explicit `disabled-and-no-run-in-flight` observation before activating its mapping;
- first successful executor run id, scheduled bucket, work-item attempt/fence, terminal status and
  domain checkpoint/manifest receipt;
- complete terminal-day coverage from each fixed direct-writer floor through its current allowed
  source ceiling, so the handoff cannot strand an unrepairable day between ownership windows;
- service-removal timestamp/result;
- repository config-removal commit `e4490c3c2f2e23f75cc9d6e297f4be646e0e00a1`;
- rollback instruction naming only the affected executor lane; and
- post-removal full service/deployment re-read plus the anti-`cronSchedule` guard result.

Environment evidence records variable **names only**. Never paste DSNs, object-store keys, source
credentials or resolved Railway reference values into this file.

## Verified production handoff state

```text
origin/main == e4490c3c2f2e23f75cc9d6e297f4be646e0e00a1
plantgeo-job-executor.activeDeployment.id == b1f35a20-6e05-48ff-9801-5235c9753a01
plantgeo-job-executor.activeDeployment.commitHash == e4490c3c2f2e23f75cc9d6e297f4be646e0e00a1
plantgeo-job-executor.activeDeployment.status == SUCCESS
plantgeo-job-executor.rootDirectory == /
plantgeo-job-executor.configFile == /services/agri-data-service/railway.job-executor.json
plantgeo-job-executor.dockerfilePath == infra/job-executor/Dockerfile
all six legacy service objects are still present
all six legacy service objects have cronSchedule == null and no-op start commands
Railway environment scheduled services == []
all legacy writer invocations == terminal and not running
all recurring responsibility mappings == executable and 37 are active
soil-moisture one-shot == terminal completed, no schedule, no active run
independent review == APPROVE
integrated sweep == PASS
explicit orchestration follow-up == received
```

The handoff through activation is complete. Remaining work is bounded service-object cleanup:
observe the mapped lane success and domain checkpoint, migrate the ingest object's hidden CDS
credentials/references, remove one eligible legacy object at a time, and re-read the environment
after each removal. Stop before removal if any mapping, lease, source ceiling, checkpoint,
publication receipt or successful run is missing. There is no rollback command that creates or
schedules a Railway cron; the only rollback state is the affected executor lane disabled with all
data retained.

## Correction — 2026-09-03

The line above reading `plantgeo-job-executor.configFile == /services/agri-data-service/railway.job-executor.json`
is retracted, not extended: it is not deleted here because this file is append-only, but it no longer
describes production state. Re-read via the Railway API on 2026-09-03 (project
`6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`, environment `b7cfa813-8a5c-4fcd-80f2-cab736d840a7`) shows the
service's config-as-code field absent entirely — by contrast, `plantgeo-parquet-api` shows
`configFile: /services/agri-data-service/railway.json`. Without a config-as-code path, Railway
discovers the repository-root `railway.json` (`build.dockerfilePath: "Dockerfile"`, the Next.js app)
on every GitHub push, overriding the dashboard's Dockerfile setting and building the Next.js image
instead of `infra/job-executor/Dockerfile`; that image dies at
`Error: NEXT_PUBLIC_PMTILES_URL must be a reviewed production URL` ([build 4/9]).

Four deployments have failed this way since this handoff was recorded: `003bfc6e` at `e4490c3`
(2026-09-02 17:51 UTC, the push immediately following this handoff), `fbc4cbb7` at `e4a101f`
(2026-09-03 06:28), `9fa4c8a8` at `1da1a28` (2026-09-03 12:39), and `5523d2e8`
(2026-09-03 12:40, `railway service redeploy --from-source` — a from-source redeploy does not bypass
root discovery either). The `b1f35a20` deployment this handoff verified was reached by a manual
redeploy that loaded `infra/job-executor/Dockerfile` correctly; whatever setting made that possible
no longer exists on the service. The executor has been running `e4490c3` — the exact commit this
handoff certified — ever since, while `plantgeo-main` and `plantgeo-parquet-api` have advanced to
`1da1a28`.

**Remedy:** set the service's config-as-code path to
`services/agri-data-service/railway.job-executor.json` (dashboard Settings → Config-as-code, or the
Railway MCP `update-service` `railwayConfigFile` field — Railway CLI 5.45.2 has no service-update
verb). See `docs/deployment.md`, "Current mechanics" for the full deployment census.
