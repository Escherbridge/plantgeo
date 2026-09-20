---
type: evidence
---

# Release checkpoint - commit fae95a94 - 2026-09-20

PASS -- all four non-ML platform services reached `SUCCESS` on
`fae95a9463cde500b934235d4de38de76658a486`. This checkpoint repairs the rejected
`c02f7e7b` release without modifying or redeploying the separately owned PlantGeo ML service.

## Rejected predecessor

Commit `c02f7e7b12ff86faad8dbf1b4fcbd8333983fc3e` restored fifteen retired agri-service
weather-forecast/PostgreSQL files and added a region-binding test with an incomplete module mock.
Railway rejected the release:

- `plantgeo-main` deployment `482252ee-6e1e-417a-8f64-c4416a7823c6`: `FAILED`; the new test omitted
  `regionIdentityVerdict` from its `@/lib/region/region` mock. The other 2,937 web tests passed.
- `plantgeo-parquet-api` deployment `c0a0fdeb-1f1a-4c58-9bc1-e53812da1b29`: `FAILED`; the governed
  receipt described `9b65028a...` over 872 files while the restored tree digested to `373d0abf...`
  over 887 files.
- `plantgeo-job-executor` deployment `fde4a125-6ad7-4080-8e37-c93dad5cd15d`: `FAILED`.
- `plantgeo-martin` deployment `930413f8-1b80-44cd-beca-8fc5e818438f`: `SUCCESS`.

The repair deletes only the proven restored files, preserves the no-projection/no-PostgreSQL
environmental boundary, and changes the test to a partial mock that retains every real export.
Before push, `scripts/verify_quality_receipt.py` verified the restored corpus as
`9b65028af770289a9a1490cdbacd710b38b592ec84aa4f5cd36857e38928663b` over 872 files.

## Railway verdict

Project `Aevani`, environment `production`:

| Service | Deployment | Result |
| --- | --- | --- |
| plantgeo-main | `1d310436-5625-404d-824a-489dfcbdbd3d` | `SUCCESS` |
| plantgeo-parquet-api | `26167f89-97a6-41b8-bd67-2dfd68133021` | `SUCCESS` |
| plantgeo-job-executor | `3243245b-b473-4732-827c-0d975699af16` | `SUCCESS` |
| plantgeo-martin | `54f6b450-3a7a-4f0b-be3d-5acde8e0cf42` | `SUCCESS` |

## Live health probes

At `2026-09-20T13:24Z`:

- `GET https://plantgeo.aevani.com/api/ready` -> 200; configuration, database and Redis true.
- `GET https://plantgeo-parquet-api-production.up.railway.app/ready` -> 200; published-reader
  profile with database profile, receiver identity, extensions, migration and serving surface true.
- `GET https://plantgeo-martin-production.up.railway.app/health` -> 200, `OK`.

No ML service status, configuration, deployment, or filesystem path was changed for this checkpoint.
