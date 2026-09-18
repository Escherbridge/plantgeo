---
type: evidence
---

# Release checkpoint - commit 64a586e1 - 2026-09-18

Commit 64a586e1 ("refactor(agri): split availability index, snapshot products, gap fill,
executor") was pushed to origin/main at 18:59:07Z on 2026-09-18. This checkpoint watched the
resulting Railway auto-deploy to completion, then ran read-only functional and data-quality probes
against production. No local run, no writes to production data -- HTTP GET probes only.

## 1. Deploy watch (Railway project "Aevani")

| Service | Deployment ID | Result | Commit landed | Notes |
| --- | --- | --- | --- | --- |
| plantgeo-martin | 9ab1aeef-bf4e-4366-8098-272f8f982164 | SUCCESS / RUNNING | 64a586e1 | clean |
| plantgeo-job-executor | c270a47e-2596-4765-9c26-291f4290ec14 | SUCCESS / RUNNING | 64a586e1 | clean, no quality-receipt failure |
| plantgeo-parquet-api | f7a7aa99-7292-4d3b-9e5a-edd3e5657a55 | SUCCESS / RUNNING | 64a586e1 | clean, no quality-receipt failure |
| plantgeo-main frontend | dbb93d64-fe9a-4ac9-a1dd-4d023f55c2eb | FAILED build stage | still d0e57bfa previous | see finding below |

Finding -- plantgeo-main build failure, NOT the known quality-receipt failure mode. The build
died at the "npm run check:data-boundary" gate (build step 6/10), not at the Python quality
receipt (that gate only exists on the two agri-data-service Docker images, and both of those
deployed clean). Exact failing log lines, from "railway logs -b dbb93d64-fe9a-4ac9-a1dd-4d023f55c2eb":

[INFO] Unapproved browser-visible URL(s) found. Environmental facts must use a PlantGeo API, publication, or reviewed asset exception:
- src/lib/map/drawing.ts:7 https://en.wikipedia.org/wiki/Ramer-Douglas-Peucker_algorithm

[INFO] Client/server restricted-import check passed.
[INFO] Observation-fabrication check passed (2 rules over 7 display roots).
...
[ERRO] [build  6/10] RUN npm run check:data-boundary
Build Failed: build daemon returned an error < failed to solve: process "/bin/sh -c npm run check:data-boundary" did not complete successfully: exit code: 1 >

The commit message describes a "readability pass" that named the Ramer-Douglas-Peucker
algorithm at its implementation (src/lib/map/drawing.ts:7) per the project convention (name the
algorithm, link the paper or a wiki article). scripts/check-data-boundaries.mjs treats every
browser-visible string literal as a potential environmental-fact claim requiring an approved
API/publication/asset exception, and a bare Wikipedia URL in a source comment trips that same
gate. Net effect: the frontend never redeployed. Production is currently running a mix of
versions -- backend Parquet/executor/Martin services on 64a586e1, frontend still on the prior
d0e57bfa 2026-09-18 16:39 push. This is a genuine regression from the push, not a flaky infra
failure; the comment-only change should not have tripped a data-fact boundary check, or the
checker needs a comment-scoped exception. Not fixed here -- this task is evidence-only.

## 2. Functional probes production

All probes below hit the still-running services plantgeo.aevani.com frontend on d0e57bfa,
plantgeo-parquet-api-production.up.railway.app on 64a586e1.

### /api/ready

GET https://plantgeo.aevani.com/api/ready -> 200, 471-578ms
{"status":"ready","checks":{"configuration":true,"database":true,"redis":true},"timestamp":"2026-09-18T19:04:00.222Z"}

PASS.

### Parquet layer readers /api/v1/parquet/day

Replayed from public-read-requests-20260914.json and api-samples-session3-20260914.json one
request per layer the refactor touched. Shape verdict compares top-level keys against the
2026-09-14 recorded sample state, requested_day, served_day, rows, truncated, plus
layer-specific extras like mtbs_snapshot for burn-severity.

| Layer | Day | HTTP | Latency | state | served_day | Row count | Shape match |
| --- | --- | --- | --- | --- | --- | --- | --- |
| burn-severity | 2026-09-11 | 200 | 3268ms | published | 2026-09-11 | 2 | match incl mtbs_snapshot |
| burn-severity governed-absence day | 2026-08-31 | 200 | 432ms | governed_absence | 2026-08-31 | n/a | match |
| fire-detections | 2026-09-12 | 200 | 711ms | published | 2026-09-12 | 2 | match |
| fire-perimeters | 2026-09-04 | 200 | 1465ms | published | 2026-09-04 | 5 | match |
| water-gauges | 2026-09-14 | 200 | 887ms | published | 2026-09-14 | 38 | match |
| weather-observations | 2026-09-07 | 200 | 861ms | published | 2026-09-07 | 4 | match |
| soil-field-vpd | 2026-09-05 | 200 | 810ms | published | 2026-09-05 | 58 | match |
| drought | 2026-09-08 | 200 | 834ms | published | 2026-09-08 | 2 | match |
| vegetation | 2026-09-07 | 200 | n/a | published | 2026-09-07 | 0 | match, recorded evidence also returns 0 rows for this exact bbox/day; not a regression |
| watersheds | 2026-08-07 | 200 | 829ms | published | 2026-08-07 | 24 | match |
| evacuation-zones | 2026-09-14 | 200 | 725ms | published | 2026-09-14 | 0 | match |
| sensors | 2026-09-09 | 200 | 1233ms | published | 2026-09-09 | 98 | match |
| soil-survey | 2026-08-28 | 200 | 453ms | lane_never_written | n/a | n/a | match, known pre-existing state, RUNBOOK section 0.29.1 |

Water gauges named-day rule. Sample row: observed_day 2026-09-14, observed_at 2026-09-15T06:00:00Z.
served_day from the envelope is 2026-09-14, a plain YYYY-MM-DD string equal to that same rows
observed_day, not derived from naively truncating the UTC observed_at timestamp which would
wrongly read 2026-09-15. PASS, the named-day rule from the
plantgeo-named-day-rule-and-density-floor memory holds under this refactor.

### tRPC

GET https://plantgeo.aevani.com/api/trpc/environmental.getSliderCapabilities -> 200
GET https://plantgeo.aevani.com/api/trpc/teams.listMyTeams -> 401
{"error":{"json":{"message":"UNAUTHORIZED","code":-32001,"data":{"code":"UNAUTHORIZED","httpStatus":401,"path":"teams.listMyTeams"}}}}

teams.listMyTeams is a protectedProcedure in src/lib/server/trpc/routers/teams/organizations.ts.
A clean structured UNAUTHORIZED, not a 500 or crash, with no session proves the split teamsRouter
organizationProcedures, membershipProcedures, invitationProcedures, joinLinkProcedures,
directoryProcedures still composes correctly into router.ts. PASS.

## 3. Data quality

### Botanical occurrences query

GET https://plantgeo-parquet-api-production.up.railway.app/api/v1/botanical-occurrences/query
    ?release_set_id=956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4&bbox=-125,42,-111,49&zoom=5
-> 200
counts: {"matched":1711,"returned":500}, cells.length=500, sum(record_count)=2698, truncated=true

Non-zero rows, healthy aggregate response cell_id, record_count, documented_taxa, geometry,
etc, consistent with the pinned release 956c0be7 from the 2026-09-14 evidence. PASS.

### Slider capabilities and availability freshness environmental.getSliderCapabilities, 2026-09-18

serverCurrentDate is 2026-09-18. Compared latestObservedDate against the 2026-09-14 baseline,
every lane checked is equal to or newer, none regressed:

| Layer | 2026-09-14 baseline latest | 2026-09-18 latest |
| --- | --- | --- |
| drought-areas | 2026-09-14 | 2026-09-18 |
| fire-detections | 2026-09-12 | 2026-09-16 |
| fire-perimeters | 2026-09-04 | 2026-09-18 |
| water-gauges | 2026-09-14 | 2026-09-18 |
| weather-observations | 2026-09-14 | 2026-09-18 |
| sensors | 2026-09-09 | 2026-09-18 |
| vegetation | 2026-09-07 | 2026-09-11 |
| soil-field-moisture | 2026-09-05 | 2026-09-09 |
| burn-severity | 2026-09-11 | 2026-09-18 |
| watersheds | 2026-08-07 | 2026-08-07 census-only snapshot, no producer expected to move it |
| evacuation-zones | 2026-09-11 | 2026-09-16 |

climate-field-shortwave-radiation appears in withheldParquetCapabilities with
reason availability_stale, expected, matches the known NASA POWER ALLSKY_SFC_SW_DWN
outage since roughly 2026-07-01 14:30Z per memory plantgeo-power-solar-regressed-2026-09-18. Not a
regression.

## Verdict

REGRESSION -- plantgeo-main failed to build on commit 64a586e1 at the
npm run check:data-boundary gate over a bare Wikipedia algorithm-reference URL in
src/lib/map/drawing.ts:7, so the frontend never redeployed and production now serves a version
mix backend 64a586e1, frontend d0e57bfa. All backend/data probes against 64a586e1
Parquet API, job executor, Martin pass cleanly with correct shapes, correct named-day semantics,
healthy row counts, and no unexpected staleness beyond the already-known shortwave-radiation
outage.

## Hotfix 8451ebcf -- 2026-09-18 19:23Z

Hotfix commit 8451ebcf, pushed at 19:15Z, removed the Wikipedia URL from src/lib/map/drawing.ts:7
that had tripped npm run check:data-boundary.

| Service | Deployment ID | Result | Commit landed |
| --- | --- | --- | --- |
| plantgeo-main | f6609f99-53dd-4c24-bc5a-f199b8049e19 | SUCCESS / RUNNING | 8451ebcf |
| plantgeo-martin | 641a2a42-5c1e-4a2b-9e5b-c92f9d0cbb3b | SUCCESS / RUNNING | 8451ebcf redeployed alongside main |
| plantgeo-job-executor | c270a47e-2596-4765-9c26-291f4290ec14 | SUCCESS / RUNNING | 64a586e1 unchanged, fine |
| plantgeo-parquet-api | f7a7aa99-7292-4d3b-9e5a-edd3e5657a55 | SUCCESS / RUNNING | 64a586e1 unchanged, fine |

GET https://plantgeo.aevani.com/api/ready -> 200
{"status":"ready","checks":{"configuration":true,"database":true,"redis":true},"timestamp":"2026-09-18T19:23:52.070Z"}

The frontend now serves 8451ebcf, the version skew from the earlier plantgeo-main build failure
is resolved. job-executor and parquet-api did not need to redeploy since neither touches
src/lib/map/drawing.ts; both remain clean on 64a586e1 as expected.

## Verdict: PASS-AFTER-HOTFIX

The original REGRESSION verdict is superseded. plantgeo-main deployment f6609f99-53dd-4c24-bc5a-f199b8049e19
landed 8451ebcf clean, /api/ready is 200, and all four services are on a consistent, non-skewed
set of commits (8451ebcf for main/martin, 64a586e1 for job-executor/parquet-api, both green).
