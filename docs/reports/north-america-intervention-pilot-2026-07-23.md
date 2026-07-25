# North America intervention evidence pilot

## Outcome

The Boise/Hillside to Hollow vertical slice is complete in the guarded
disposable PostgreSQL database `plantgeo_geospatial_test_20260723_1421`. It
proves reproducible raw capture, checksummed provenance, PostGIS normalization,
resolution-aware facts/features/gaps, and an evaluation-only forecast rerun.

The persistent `plantgeo` warehouse was not migrated or changed. It remains at
Alembic `20260722_0007` with the original rejected forecast run, 14 hindcasts,
98 forecast-versus-actual values, 34,854,080 observations, 1,055 USDM
snapshots, zero forecast receipts/values/publications/pointers, and no
geospatial pilot tables. Railway, schedules, deployments, publication
pointers, strategy selection, and recommendations were not changed.

## Open source evidence captured

The complete authoritative US/Canada/Mexico coverage, resolution, cadence,
history, licence, access, reliability, and inference-scale review is in the
[North American source matrix](../north-america-intervention-source-matrix.md).
The implemented Boise release is `boise-hillside-hollow-open-v1`, manifest
`59e95a16e94e8d3c75e57d426536b32914ebd73fc15663b034675206c5496d7c`.

| Source | Captured evidence | Bytes | SHA-256 | Defensible maximum use |
|---|---|---:|---|---|
| US Census TIGERweb, 2025 incorporated places | Boise boundary, one feature | 213,610 | `0025169cd38411e915edbb3f03e89f41a6ba7c471f19f807a55e6850107da96c` | City boundary/context; not a legal land description |
| OpenStreetMap/ODbL | Hillside to Hollow Reserve way 674700373 geometry | 3,191 | `7552170d903f8f6313c79d20c186f1c192cd4db5bc3865c99919f8458fa789b3` | Named property identity and neighborhood context; not cadastral |
| Official versioned OSM API | Way 674700373 version 19 reference, timestamp 2025-04-09T18:53:39Z | 1,812 | `b8f3e0e9a7312bdbcf796156a4257eebe1daec8f6880e7c5ead3de0552ca13e0` | Exact version evidence for the OSM geometry |
| USDA Forest Service WUI, 2020 | Complete five-feature response for the property bounding box | 30,387 | `b3a198baeb277b65735381240e9919be317978b6bb6a8ff75ac88ce24ae212da` | Census-block/neighborhood exposure context; not parcel fuels or regulatory WUI |

The immutable capture root is:

`C:\Users\atooz\Programming\plantgeo\.agri-local-runs\north-america-intervention\boise-hillside-hollow-20260723\39d01682649d68a9bfb1d7ee4ebd258a3cfb627b6dc192df8e84be6c1eb2ac73`

The four raw/reference artifacts total 249,000 bytes. Their receipt-set checksum
is `c17baeefc8c758b577989dcc02f3e8b78eb8354199f029580a7781de9906874a`.

## PostGIS evidence result

The validated release contains two immutable subjects, seven normalized source
features, one reproducible analysis receipt, 17 evidence inputs, and 26 lineage
edges.

- Boise: administrative-boundary subject; computed geodesic area
  225,537,120 m2. Census reported land area remains a separate observed fact
  (222,999,296 m2).
- Hillside to Hollow Reserve: non-cadastral named-property subject; computed
  geometry area 1,541,055 m2.
- Property/city topology: the property geometry is covered by the Boise
  boundary.
- WUI: dominant intersecting class `Med_Dens_Intermix`, overlap fraction
  `0.76`, and a conservative 2,029-day minimum age at capture. These are
  neighborhood/census-block context, not site inspection or life-safety
  predictions.
- Nine known-gap records cover cadastral authority; structures/inspection;
  terrain, fuels, canopy, egress, and fire history; watershed, wetlands,
  groundwater, and infiltration; current drought/weather/soil moisture; water
  rights, capacity, quality, and infrastructure; aquaponics/hydroponics;
  silvopasture/agroforestry; and regulation/professional review.

No recommendation row or validated life-safety prediction was created.

## Forecast completion result

The corrected fixture now:

- accepts the forecast schema at `20260722_0008` or geospatial head
  `20260723_0009`;
- reuses the existing exact-source series instead of violating its unique
  identity;
- creates only new v2 candidate/model/policy/run/hindcast identities;
- is hard-wired `evaluation_only = true` and
  `publication_authorized = false`;
- independently refuses to cross into receipt/publication DML unless
  publication authorization is changed by a reviewed code change;
- returns before forecast receipts, values, publications, or pointer
  advancement even if every gate passes.

The disposable run `3a3723bc-a039-4b22-b564-25ebd4edbf57` was honestly
rejected:

| Evaluation | Result | Gate |
|---|---:|---:|
| Terminal MAE / RMSE / naive RMSE | 0.5293168554 / 0.6248889318 / 1.6827783149 | Terminal gate passed |
| Terminal skill / MAPE / coverage | 0.6286564153 / 0.2970724300 / 1.0 | Terminal gate passed |
| Rolling origins | 3 of 14 passed (0.2142857143) | At least 0.50 required |
| Aggregate MAE / RMSE / naive RMSE | 0.7664812881 / 1.0076008331 / 1.3054340900 | MAE/RMSE exceeded policy |
| Aggregate skill / MAPE | 0.2281488274 / 0.3531289996 | MAPE exceeded policy |
| Empirical p10-p90 interval coverage | 0.6326530612 | At least 0.70 required |

The v2 run retained 14 immutable `hindcast_v2` receipts and 98 outcome values.
Its receipt-manifest checksum is
`dc787e726acb6ba81e5a7d0a3361d8b57f8457d794add74bdc906d59c3bc444c`.
It created zero forecast receipts, forecast values, publication items,
publications, or publication pointers.

This is not a useful current Boise/property forecast. The source is a single
NASA POWER point at `(-105, 40)`, about 55.66 km native support, ending
2026-04-30 and 84.98 days old at the v2 issue time. An operational candidate
requires newly pinned history through the current issue window, Boise-relevant
spatial support, current weather inputs, and a fresh time-honest evaluation.

## Generic 30-day forecast-iteration proof

Revision `20260723_0010` is applied only to disposable database
`plantgeo_geospatial_test_20260723_1421`. It adds the requested generic
time-series contract, UTC date spine, deterministic historical-increment
bootstrap, immutable iteration procedures, and later actual/error signals.

The retained NASA POWER WS2M point series completed a 30-day evaluation:

| Evidence | Result |
|---|---:|
| Iteration | `46e7673a-b263-4922-8e8c-4e01fdb555dd` |
| Stable key | `nasa-power-ws2m-20260331-bootstrap-v2` |
| As-of / cutoff | `2026-07-24T03:56:35Z` / `2026-03-31T00:00:00Z` |
| Training days / increments | 1,432 / 1,431 |
| Simulations / horizon | 1,000 / 30 days |
| Forecast receipt | `bafa2af9ef8cfeb39ad5025c9a8118b7ca23d4d1f4e36591ee834a25c15008cb` |
| Persisted low/median/high rows | 30 |
| License-bound actual-digest-v2 rows | 30 |
| MAE / RMSE | 0.7916666667 / 0.8980079807 m/s |
| Empirical p10-p90 coverage | 0.9666666667 |

The contract checksum is
`53b3296528041ac6842da9ca263126e40b5f0614117388142e052f19171cb855`.
The server high-water ledger contains 3,203 rows and occupies 592 KB; all
iteration/actual tables plus the ledger occupy about 912 KB. The full database
remains about 16 GB. Migration time was about six seconds, the 1,000-path
forecast about 12-17 seconds, and actual reconciliation about nine seconds on
the local machine.

The pre-existing rejected evidence remained unchanged: 14 `hindcast_v1`
receipts retain aggregate checksum
`5c27c4f71db62fa78b21b93d27311afaae1bf949bc86a7a28754a3d041bb689d`,
and 14 `hindcast_v2` receipts retain
`afa66be603ac97d92bcb232ce7b02da6c6c507eefd72e803d251a36efe041fd1`.
Operational forecast receipts, values, publications, and pointers remain zero.

These metrics prove that the framework runs and persists evidence; they do not
validate a current or life-safety forecast. The series is still a stale,
55.66-km Denver point sample. The method treats historical daily increments as
exchangeable and does not yet model seasonality, autocorrelation, regime
change, forecast weather, or cross-series dependence. Accuracy can improve by
adding new immutable method versions and training on the persisted actual,
residual, error, and interval-coverage signals.

## How to read the data

Use PgAdmin with host `127.0.0.1`, port `5442`, database
`plantgeo_geospatial_test_20260723_1421`, and the read-only local viewer. Open
`infra/local-warehouse/read-pilot-evidence.sql` in Query Tool. The same file
can be run with `psql`:

```powershell
& 'C:\Program Files\PostgreSQL\16\bin\psql.exe' `
  -h 127.0.0.1 -p 5442 -U plantgeo_local_viewer `
  -d plantgeo_geospatial_test_20260723_1421 -X `
  -f infra/local-warehouse/read-pilot-evidence.sql
```

The script starts an explicit read-only transaction and shows release/source
versions, artifact URIs/checksums, subject geometry summaries, typed evidence,
lineage, forecast metrics, and the fail-closed publication audit.

For the new generic iteration plane, run
`infra/local-warehouse/read-forecast-iterations.sql`. It shows the canonical
contract, receipt and parameter/history digests, training-license snapshot,
all low/median/high/actual/error rows, actual digest version, and exact
source-release/observation/license lineage.

## Cheapest upstream PostgreSQL path

Do not upload the 1.58 GB full warehouse backup just to move this pilot. The
largest governed metadata table is `agri.artifact` at about 620 MB because it
also contains historical inline payloads; the new pilot raw/reference payload
is only 249 KB.

Selective upstream promotion is not implemented yet. The existing writer
intentionally accepts only the loopback local warehouse and stores captured
bytes as `database_inline`; it cannot be pointed at Railway or R2.

The cheapest reviewed promotion design would be:

1. Apply the reviewed Alembic schema to a private PostGIS target and create the
   constrained geospatial loader role.
2. Implement a small, reviewed promotion bundle for only the three pilot source
   releases, four artifacts, release set, seven features, two subjects, one
   analysis run, 17 evidence rows, and 26 lineage rows.
3. Initially retain the 249 KB of raw bytes inline, or separately add and test
   object-store artifact support. If R2 support is added, retain SHA-256, size,
   media type, licence snapshot, and object URI in PostgreSQL. R2 list price is
   $0.015/GB-month after its 10 GB free tier.
4. Add a target-side loader that accepts only the private approved target,
   preserves UUID/foreign-key order, is idempotent, and cannot access forecast
   publication or recommendation surfaces.
5. Verify counts, checksums, foreign keys, and zero publication/recommendation
   rows on the target before granting read access.

For a full disaster-recovery copy, use the existing custom-format backup and
`pg_restore`; for selective promotion, plain `pg_dump --table` is a poor fit
because it has no row predicate and would pull unrelated 620 MB artifact
history. Until the filtered bundle exists, the 1.58 GB custom backup/restore is
the only implemented exact-copy path and still requires the documented
PostgreSQL 18/extension rehearsal. No upstream or Railway database was changed
in this workstream.

## Validation and independent review

The integrated repository sweep passed:

- Ruff format and lint;
- mypy over 46 source files;
- 203 Python tests passed and 6 optional database tests skipped; the new
  revision-0010 PostgreSQL iteration/reconciliation proof ran in the suite;
- the disposable PostgreSQL migration, immutability, pilot writer, and
  resolution/inference contracts;
- ESLint with zero errors (75 existing warnings), TypeScript checking, the
  data-boundary check, all 140 web tests, and the Next.js production build.

An independent design/code review found and prompted fixes for parent-locking,
trigger search paths/privileges, release freeze behavior, OSM version evidence,
analysis parameter checksums, WUI vintage semantics, and confidence-field
length. All substantive review findings were closed. A separate forecast audit
identified the exact-source series conflict and the need for hard evaluation
mode; both are now enforced and proved by the v2 run.

## Remaining blockers

- Machine-readable manifests are still needed as additional country/source
  adapters are implemented.
- Open parcel/building/WUI/water-infrastructure coverage and licences remain
  uneven across the US, Canada, and Mexico.
- The pilot lacks legal parcel authority, structure and fuels inspection,
  high-resolution terrain/canopy, egress capacity, hydrologic field evidence,
  water rights/quality/capacity, and site regulatory review.
- The pre-existing seven-day v2 SQL-linear candidate failed its historical
  gate, while the new 30-day bootstrap proof remains stale/nonlocal evaluation
  evidence. Neither may be published or represented as operational.
- PostgreSQL 18 extension parity and restore rehearsal remain required before
  any separately authorized Railway migration.
