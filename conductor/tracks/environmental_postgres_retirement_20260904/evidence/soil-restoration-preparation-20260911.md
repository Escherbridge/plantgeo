---
type: track-evidence
slug: environmental_postgres_retirement_20260904
recorded_on: 2026-09-11
status: preparation_only
---

# Static soil candidate and soil-survey restoration boundary

This slice implements local, reviewable preparation. It performs no deploy, database write,
object-store mutation, admission, active-pointer publication, migration or relation drop. It does
not certify production serving or scheduled execution. The September 11 plan is current; the
historical PostgreSQL exporter/backfill wave was not resumed. Static SoilGrids and USDA soil-survey
remain separate products.

## Saved SoilGrids bytes revalidated and preserved

The original twelve files still exist in
`C:/Users/atooz/Programming/plantgeo/data/raster/soil/`. Their original independent receipts were
found in the earlier `C:/Users/atooz/.codex/worktrees/1d40/plantgeo/.omc/research/`, rather than the
main checkout. Exact receipt copies are retained in
`.omc/research/soil-saved-receipts-20260911/` in this worktree.

The maintained local command `scripts/prepare_static_soil.py prepare` verified complete bytes for
all twelve files against both the original COG source manifest and the independently pinned local
hash receipt. It wrote exact source bytes under `objects/<sha256>` in the exclusive local directory
`.omc/research/static-soil-candidate-20260911/`. The twelve assets total **237,891,309 bytes**;
the six COGs total **63,503,828 bytes**. The object list, full digests, proposed immutable object
keys, exact grids, units/scales, PMTiles headers and physical-unit ramps are retained in the
[tracked candidate](static-soil-candidate-20260911.json).

| Artifact | Complete-byte SHA256 |
| --- | --- |
| Original `data/raster/soil/manifest.json` | `a70359386bea7468b10bda0665f3b1728dd9c0c5118c8d94300e4b7933e92c5f` |
| Independent local twelve-file receipt | `15f163774fdfe4145c8149c47c41479442a01ce7688cadba26a80723711d8836` |
| Newly prepared candidate manifest | `8bd6d81ef92a0515f8d4133be17ccef247f6e52e01ff307464fef2e997e212dc` |
| Four requested-point evidence file | `dc7b2cf6defef4f0e6fda5bc43dd955f24e80a5be6ebdf6b0ba8de1eb9cd8297` |
| Valid-pixel inspection/evidence file | `72bb00084e1642af2f3099234ebfdaab9e5b34fa605024ac0e4cdb30db8d8d23` |

Preparation retains `remote_full_hash_verified=false`. No September 11 remote SoilGrids object
was fetched; the September 10 first/last-range receipt remains a **sampled remote byte identity**,
not proof of all remote bytes. The candidate describes a local preservation artifact, not admitted
assets. Its upstream retrieval time and immutable source version remain unknown; `latest`, the
v2.0 label and preparation time do not establish those facts.

## Actual saved-grid point evidence

The new point reader loads and fully rehashes at most 64 MiB of COGs once, keeps immutable bytes,
and selects at most six raw full-resolution pixels. Each property can decode only one admitted
block, with a 1 MiB decoded-block cap. It returns row, column, pixel center, spherical distance,
raw value, scaled value, physical unit, and full COG identity. It neither reads palette colors nor
requests averaged overviews. Nodata is masked before division exactly once.

The first four [requested-point probes](static-soil-point-evidence-20260911.json), all carrying
selected day **2026-09-05**, returned:

- Boise `[-116.2023,43.615]`: nodata for all six saved properties.
- Bend `[-121.3153,44.0582]`: nodata for all six saved properties.
- `[-117,42]`: out of extent, correctly respecting the inspected southern edge at
  `42.000619809699636` rather than the requested build bbox's nominal 42 degrees.
- Pacific `[-125,46]`: nodata for all six properties.

These outcomes were retained without substituting a nearby value or authoring an absence. A
separate offline preparation inspection of the complete phh2o base grid found **9,339,483 valid
pixels out of 11,894,366 (78.52022545800256%)**. This count applies only to the saved phh2o grid;
it does not establish full regional coverage or an identical valid population for every property.
The four selected valid pixel centers and their six-property answers are retained in
[valid-pixel inspection](static-soil-valid-pixel-inspection-20260911.json).

One reproducible point at `[-118.06689357893598,45.38618503028526]`, row **1147**, column **2847**,
returned these exact values at its pixel center (distance 0 m):

| Property | Raw int16 | Divisor | Physical value |
| --- | ---: | ---: | --- |
| phh2o | 66 | 10 | 6.6 pH |
| soc | 579 | 10 | 57.9 g/kg |
| nitrogen | 449 | 100 | 4.49 g/kg |
| bdod | 115 | 100 | 1.15 kg/dm³ |
| cec | 294 | 10 | 29.4 cmol(c)/kg |
| ocd | 401 | 10 | 40.1 kg/m³ |

All answers explicitly report `local_verified_candidate`,
`saved_reprojected_full_resolution_pixel`, `observed_day=null` and
`temporal_distance_days=null`. The selected day is echoed; no temporal observation or neighbouring
date is invented for a static estimate. This is executable local selected-day/support evidence,
not an API, agent-MCP or deployed-reader acceptance receipt. The saved reprojected pixels are
different support from ISRIC's native projected grid and from the old REST point cache.

The exact local preparation invocation is:

```text
python scripts/prepare_static_soil.py prepare
  --source-root C:/Users/atooz/Programming/plantgeo/data/raster/soil
  --hash-receipt ../../.omc/research/soil-saved-receipts-20260911/soil-independent-local-hashes-20260910.json
  --source-manifest-sha256 a70359386bea7468b10bda0665f3b1728dd9c0c5118c8d94300e4b7933e92c5f
  --hash-receipt-sha256 15f163774fdfe4145c8149c47c41479442a01ce7688cadba26a80723711d8836
  --output ../../.omc/research/static-soil-candidate-20260911
```

The actual invocation used the equivalent root-relative script path with the installed main
checkout Python environment and this worktree on `PYTHONPATH`. The existing candidate directory
cannot be overwritten. On this Windows host, the command selected rasterio's bundled PROJ data
before importing rasterio, following the measured host-PROJ collision documented in `scripts/raster/AGENTS.md`.

## Soil-survey re-inventory and preparation

The root task's fresh read-only census at **2026-09-11T18:17:40Z**, deployed revision
`fa202230958fb55521963e886eb031be5fc266c4`, found **959** native Parquet objects, with a complete
bounded listing and **no completion/JSON marker** under the exact soil-survey root:

| Native partition prefix | Objects | Compressed bytes |
| --- | ---: | ---: |
| `layer=soil-survey/kind=observed/zoom=13/year=2026/month=08/day=10/` | 478 | 478,154,389 |
| `layer=soil-survey/kind=observed/zoom=13/year=2026/month=08/day=28/` | 481 | 479,302,736 |

The raw census is `.omc/research/retirement-soil-census-20260911.json`. The root task additionally
performed two complete-object/footer probes, retained in
`.omc/research/retirement-soil-footer-20260911.json`. Each `part-0.parquet` has **849,629 bytes,
500 rows and one row group**, with the expected fifteen soil-survey columns and no schema metadata:

- August 10 part 0: `36519207e500e6b68875bf83d35a164b71afdee0746d16c46e885f19a8b9432d`.
- August 28 part 0: `71bda3e45d5c7d2fd97fb4b637e2cd74e4cc2007ce69671a64715082fd685058`.

Those two footer counts cannot be multiplied into a total-population assertion. Directory dates
and object modification times do not independently establish a governed source release date.
The census has no coarse rungs, complete source-population receipt or published availability proof.
No full 500 MB download or candidate publication was attempted on that incomplete authority.

The fresh executor inspection also reports `parquet-soil-survey` active/enabled, contradicting
reliance on an old “frozen lane” note. The current registry still imports the PostgreSQL exporter
and owns `_soil_survey_polygon_key_batches`, `_soil_survey_watermark` and `_fill_soil_survey`.
The root task owns the exact runtime definition/lease receipt; no pause or handoff occurred here.

`pipeline/soil_survey_restore/prepare.py` now provides a bounded local preparer for an explicitly
pinned native preservation input. It requires the preservation manifest, every full part hash,
byte/row count, native schema, release day, population scope and separately pinned preservation
receipt. It rejects duplicate delineation keys, missing/invalid/nonpolygon geometry, altered
source identities and null required fields. Native z13 is preserved; z0/z5/z9 are derived from
that same native population with the existing topology-preserving policy and no area floor.
Every rung must conserve every delineation and all nongeometry attributes, including hydric unknown.

This bounded preparer currently permits 256 parts, 100,000 native rows and 512 MiB of compressed
and decoded input, with 32 MiB parts and 500-row batches. Both freshly discovered full roots
exceed the part cap, in addition to lacking source/terminal proof. An actual whole-root restoration
therefore still requires a reviewed streaming driver and complete preservation authority; this
small preparer is not a claim that either current root has been restored.

## Exact unexecuted integration and rollback packet

For **static SoilGrids**, the product candidate is complete locally, but production admission remains
an unimplemented generic-owner operation. The proposed active key is `static-soil/_LATEST.json`.
The root task verified **NoSuchKey at 2026-09-11T18:22:18Z**, retained in
`.omc/research/retirement-static-pointer-20260911.json`. It also observed definition
`plantgeo.executor.soilgrids-cache-warm`, id `e4de8b0b-64a9-4670-b7e9-3b1b888960c9`, version 2,
enabled=true with hourly schedule `25 * * * *`; that definition is outside the effective twenty-lane
allowlist, is absent from the eleven handoff-acknowledged names, and had no active lease in the
complete query. This is effective exclusion at the captured instant, not enabled=false, deletion or
permanent retirement. There is no separate required-lanes environment variable to infer.

Before requesting admission, the owner must refresh that absence/ownership fence and supply the
exact reviewed deployed reader/adapter revision. A new
`static-soil-admission/v1` descriptor should bind the candidate digest above, real admission time,
the twelve complete immutable object receipts, and the previous pointer identity. Create the twelve
`static-soil/objects/sha256/<digest>` keys, verify complete readback, create the admission descriptor,
then CAS the pointer last under the existing ownership/publication barrier. Do not publish the
candidate JSON itself as an admitted descriptor or relabel candidate answers. Its local reader
must be wrapped by the owned admitted-manifest reader and exposed through the reader/agent lane.

Proposed remote admission ceilings remain twelve objects, 64 MiB each, 256 MiB total, two streams
and a hard 600-second child deadline. Conditional identity and full hashes are required; remote
range samples do not satisfy them. Rollback CAS-restores the exact saved previous pointer, or removes
the new pointer through the same guarded owner path if the preflight proved there was none; retain
every immutable source/manifest object. No old PostgreSQL raster registration/cache warmer is part
of the action. The recorded absence and ownership readback complete those dated preflight fields;
the missing reviewed admission command/reader revision and owner authorization keep this a
concrete candidate packet, not a ready-to-fire mutation request.

For **soil-survey**, the generic owner must replace those three registry mechanisms with a
preserved-Parquet/source-direct adapter only after complete native inventory proof and a fenced
owner handoff. It must use normal per-day locks, all-rung terminal finalization, the publication
barrier and availability extension. Static-vintage semantics remain; no synthetic daily gaps.
Rollback restores the prior admitted manifest/availability pointer or keeps the product dark,
disables the replacement lane and retains all source/candidate artifacts. Do not resume the old
PostgreSQL exporter or recreate a scheduler service. No relation can be retired on this slice:
current zero-reader, whole-population preservation and per-relation rollback proof are still owed.

The reader/acceptance owners still owe deployed selected-day, native/coarse-rung, spatial-neighbour,
explicit static temporal refusal and browser evidence under the exact admitted revision. Local
point evidence and four-rung preparation logic do not stand in for that release acceptance.

## Validation boundary

Local preparation and the real saved-grid inspections above ran successfully. New behavior tests
cover raw-pixel scaling, nodata/cell edges, complete-object tampering, missing properties, compressed
metadata caps, all-rung soil-survey conservation, duplicated keys, invalid geometry and hash refusal.
The implementation lane ran no tests. The separate verifier completed the integrated checks and
the final affected retry, including exact all-rung attribute/schema conservation under both UTC
and America/Denver DuckDB sessions. It also independently rehashed all six COGs and compared four
saved point answers and 24 base-grid/scaled values. The
[final repair review](independent-repair-review-20260911.md) records the exact scopes and results.
No full-service quality receipt or production acceptance is asserted by this document.
