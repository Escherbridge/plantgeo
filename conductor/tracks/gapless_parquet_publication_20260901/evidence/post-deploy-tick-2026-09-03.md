---
type: track-evidence
track: gapless_parquet_publication_20260901
slice: deployment-observation
status: red_build_failures_diagnosed
observed_at: 2026-09-03
---

# Post-push deployment observation — 2026-09-03

Continuation plan step 1 of the 2026-09-03 handoff. Read with the Railway MCP against project
`6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`, environment `b7cfa813-8a5c-4fcd-80f2-cab736d840a7`.

## Verdict: RED — neither Python image built; production runs mixed versions

| service | push at `e4a101f` (06:28 UTC) | push at `821a830` (12:13 UTC) | code actually running |
|---|---|---|---|
| `plantgeo-main` | `acf6853c` SUCCESS (later REMOVED by the next deploy) | `70703284` SUCCESS, live 12:17 | **new** (`821a830`) |
| `plantgeo-parquet-api` (agri service) | `56fa0804` **FAILED**, stage `quality-receipt` | `9f4e77e0` SKIPPED (docs-only change outside watch paths) | **old** (`40ea78b0` @ `e4490c3`) |
| `plantgeo-job-executor` | `fbc4cbb7` **FAILED**, stage `BUILD_IMAGE` (wrong Dockerfile) | `a4b8396e` SKIPPED | **old** (`b1f35a20` @ `e4490c3`) |
| `plantgeo-martin` | — | `97ddd4cb` SUCCESS | unchanged image |
| `plantgeo-ingest-cron`, `plantgeo-cron-mtbs`, `plantgeo-cron-soilgrids` | — | all **FAILED** at 12:13 with an empty build log | fenced (no-op start, `NEVER`); their `infra/cron-*` Dockerfiles were deleted in `e4490c3`, so every push now fails their build. Harmless noise until the services are removed per the scheduler handoff. |

Consequence: the wave-1/2 runtime repairs (`jobs-matview-refresh` `relation_absent`, WFIGS page halving,
gap-fill rung repair, coverage authority v2) are NOT in production. The old executor still ticks
every ~30 s and every tick logs `plantgeo_job_executor_tick_unhealthy` with
`failing_lanes=['jobs-matview-refresh', 'maintenance-validate-streams', 'parquet-drought',
'parquet-evacuation-zones', 'parquet-fire-perimeters', 'parquet-soil-survey',
'postgres-fire-perimeters', 'postgres-vegetation', 'soilgrids-cache-warm', 'vegetation-catch-up', …]`
(read 12:16–12:25 UTC from deployment `b1f35a20`). No new-code tick can be recorded yet.

## Failure 1 — agri service: the quality receipt can never verify from a Windows checkout

Build log, stage `[quality-receipt 5/5] RUN python scripts/verify_quality_receipt.py`:

```
QUALITY RECEIPT REFUSED: the tree does not match its receipt -- source changed without a green sweep.
  receipt: sha256:3824cf2c7033b635c662d8114b09f68d2e35ea8ad1cdb5383e831dac413b566e over 842 files
  tree:    sha256:b0ec43471f7c3d222b7fa26a3985cbbc9f7a8b956064370136f3c731222f2e2f over 842 files
```

Reproduced locally: `python scripts/verify_quality_receipt.py` on the working tree → verified
(`3824cf2c…`); the same command on `git archive HEAD services/agri-data-service` (the bytes a Linux
clone sees) → refused with exactly Railway's `b0ec4347…`. Per-file comparison: 0 files added or
missing, **181 of 842 files differ, every one only by CRLF→LF** (e.g. `interface/cli/commands.py`
4,941 CR bytes in the working tree, 0 in the archive). `.gitattributes` declares `* text=auto eol=lf`,
so git commits LF, but this checkout (`core.autocrlf=true`, files rewritten by tools) holds CRLF that
`git status` reports as clean. `compute_tree_digest` hashes raw disk bytes, so any receipt written on
this machine describes bytes the build context never contains. This is a defect in the wave-3 gate
itself, not a stale receipt; re-running `--write-receipt` cannot fix it.

Fix in flight: normalize `\r\n`→`\n` before hashing (digest domain bumped to v2), regression test,
docs; then one green sweep, `--write-receipt`, commit, `git archive` re-verification, push.

## Failure 2 — executor: a GitHub push builds the wrong Dockerfile

Build log for `fbc4cbb7`: `[build 3/9] COPY . .` then `[build 4/9] RUN if [ "production" = "production" ]
… node -e … NEXT_PUBLIC_PMTILES_URL …` → `Error: NEXT_PUBLIC_PMTILES_URL must be a reviewed production
URL`. Those are the **repository-root** Next.js Dockerfile's steps. The service config reads
`dockerfilePath: infra/job-executor/Dockerfile`, `rootDirectory: /`, but has **no config-as-code file
set**, so Railway discovers the root `railway.json` (`build.dockerfilePath: "Dockerfile"`) on push and
it overrides the dashboard path (the mechanism recorded 2026-08-04 for `plantgeo-ingest-cron`).
History proves the pattern: at `e4490c3` the push deploy `003bfc6e` died identically at 17:51 UTC and
only the manual redeploy `b1f35a20` at 18:03 built `infra/job-executor/Dockerfile`. Every future push
will fail the same way until the setting exists.

Required setting (one field, reversible; the Railway MCP `update-service` mutation was denied by the
permission classifier in this session and Railway CLI 5.45.2 has no service-update verb):

- service `plantgeo-job-executor` (`565ecaad-9946-48f1-8a0b-28fa60494a16`) → Settings → Config-as-code
  file path = `services/agri-data-service/railway.job-executor.json` (already in the repo:
  `dockerfilePath: infra/job-executor/Dockerfile`, start command `agri-service ops jobs-executor`,
  `ON_FAILURE`/10). Equivalent MCP call: `update-service{railwayConfigFile:
  "services/agri-data-service/railway.job-executor.json"}` in environment `b7cfa813-…`.

The repository already documents this exact requirement (`docs/deployment.md:567-568`,
`infra/job-executor/AGENTS.md:9-12`: Root Directory `/`, Config-as-code path
`/services/agri-data-service/railway.job-executor.json`, Dockerfile `infra/job-executor/Dockerfile`
"as one coordinated change"); production diverges from its documented configuration. Interim
workaround per push: `railway service redeploy --service plantgeo-job-executor --environment
production --from-source --yes`, which builds the latest commit under the service's own settings
(the path that produced `b1f35a20`).

Once set, the executor image also needs Failure 1 fixed (same receipt stage in
`infra/job-executor/Dockerfile`).

## Mixed-version risk while the Python images are stale

`src/lib/server/services/parquet-plane-client.ts` reads only `coverage_schema_version` 2 and throws
`ParquetPlaneContractError` on any other version. See the "coverage schema" note appended below for
what the old service (`e4490c3`) emits.

### Coverage schema note — production is degraded right now, not merely stale

`git grep coverage_schema_version e4490c3 -- services/agri-data-service/src` returns nothing: the
running Parquet API emits no `coverage_schema_version` at all, while the running web app requires the
key (`parquet-plane-client.ts`, zod `z.number().int()`, then `!== 2` → `ParquetPlaneContractError`).
`parquet-trpc-readers.ts` maps that error to `{ state: "upstream_unavailable", fault: { kind:
"contract" } }` and `parquet-slider-capabilities.ts` counts it as a coverage boundary fault, so every
Parquet-backed layer and its slider has been reporting "upstream unavailable" since `plantgeo-main`
went live at 12:17 UTC on `821a830`. `/api/ready` still answers 200 (0.66 s). Nothing is lost, but
the site is worse than before the push until `plantgeo-parquet-api` builds. This is why the digest
fix is pushed ahead of the wave-3 review verdict.
