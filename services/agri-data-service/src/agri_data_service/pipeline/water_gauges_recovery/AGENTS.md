# NWIS daily-values archive preparation

This product package prepares local historical daily-mean candidates from complete preserved
NWIS JSON responses. It uses the retained pure `parse_daily_value_series`, direct
`tables_by_publisher_day`, and registered four-rung derivation. It has no database, network,
object-store, FeatureWriter, executor, queue or publication caller. The existing direct forward
floor remains 2026-09-02; old archive caller ownership is unchanged.

## Bounded source replay

`source-manifest.json` conforms to `WaterSourceCapture`. Its independently recorded full SHA-256
is a required argument. Captured entity bytes are `responses/<complete-sha256>.json`; each file
is read completely and checked against exact length and full hash. Range samples are inadmissible.
One unit covers 1–15 publisher days on/after 2022-08-05, at most 16 canonical four-degree NWIS tiles,
8 MiB per response, 64 MiB total responses, 64 KiB manifest, 50,000 raw readings and a ten-minute
capture interval. Each response must attest HTTP 200, completed transport and a clock within that
capture. The exact complete tile set is recomputed from the requested bbox. The manifest carries
no optional endpoint override, URL, headers or credentials.

The request is the NWIS **daily-values** endpoint `/nwis/dv/`, `parameterCd=00060`,
`statisticCd=00003`, `siteType=ST`, with no active-only site restriction (`site_status_filter=all`).
The NWIS `endDT` is inclusive; translate `end_day_exclusive - 1 day` when capturing. The JSON
series itself must also declare discharge, `ft3/s`, daily mean statistic `00003` and the known
`-999999` sentinel. NWIS service semantics are documented at
<https://waterservices.usgs.gov/docs/dv-service/daily-values-service-details/>.

Every source site must identify one gauge, finite numeric coordinates inside its requested tile,
and an explicit valid publisher **standard-time** UTC offset. Every reading must carry the
requested publisher day at a naive midnight, a complete finite numeric string and source
qualifiers. Missing zones are refused, so the old parser's UTC fallback cannot fabricate a source
instant. Negative discharge remains a real value; only the explicit source sentinel is accounted
as a missing reading. `data_available_at` stays null and `ingested_at` is the actual capture clock.

Every returned series must account for every requested day through a value or explicit sentinel.
Missing days, unsupported multiple value blocks, malformed objects/rows, duplicate JSON object
keys, non-finite constants and conflicting site/day records refuse the whole unit. Identical
duplicates collapse with counts and full source bytes retained. Conflicts include conflicting
source support or qualifiers, not only changed discharge. A window with no normalized data for
one requested day requires a separate governed-absence decision and produces no candidate.
This intentionally conservative slice may need smaller windows or separate reviewed absence
evidence for gauges with discontinuous records; it never fills them by assumption.

## Candidate and generic owner hooks

`prepare_water_gauges_recovery.py` takes `--source-root`, `--source-manifest-sha256` and a new
`--output` directory. Validation and all z0/z5/z9/z13 derivation precede output creation. Original
JSON bytes, source manifest, exact registered-schema Parquet parts, complete part hashes and row
counts accompany a hashed `candidate.json`. The packet states daily mean statistical support,
`apply=false`, `availability_published=false`, and `upstream_population_complete=false`.

This is a replay of an operator-supplied capture attestation, not authentication of upstream
population or proof of the full governed AOI. A small bbox cannot replace the complete layer day.
The generic owner must bind audited request coverage, capture provenance, a complete prior
four-rung snapshot and absence/availability state under exclusive historical lane-day ownership.
Its integration entry point can call this pure preparer or reuse the normalized records/tables,
then perform a reviewed native-grain merge and shared terminal/availability finalization. Do not
wire a `FeatureWriter` or invoke the source adapter that writes an incoming-only base day.

Daily mean and instantaneous NWIS readings have different statistical support. The registered
row schema does not yet carry a statistic discriminator. Consequently admission must explicitly
reconcile any existing instantaneous grain before reusing `merge_water_gauges_day`; it must not
overwrite same-grain instantaneous observations with daily means on the strength of their equal
timestamp. The existing merge helper is a preservation primitive, not automatic semantic approval.
Rollback restores the pinned prior complete ladder and availability generation under the same
barrier and retains immutable candidate bytes. No local preparation script performs that mutation.

## Verification

`tests/parquet/test_water_gauges_recovery.py` covers registered schema on all four rungs, publisher
day/standard-time identity, negative discharge and sentinel separation, identical versus
conflicting duplicates, missing units/statistic/zone, malformed or undercovered inputs, missing
tiles, duplicate JSON keys and full-hash tampering. Tests remain held for the root's integrated sweep.
