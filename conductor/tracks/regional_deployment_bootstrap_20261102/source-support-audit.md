---
type: verification-evidence
---
# Regional source support audit

Read-only source inspection during the November 2 continuation. Country-level upstream scope is not proof that the current writer supports a custom footprint.

| Source | Existing support | Required generalization |
| --- | --- | --- |
| Water daily | Passed region envelope, stable request-tile identities, existing source completeness | Bootstrap budgets and isolated state; keep revision policy distinct from history target. |
| NASA POWER climate | 397 selected integer-degree points from a 462-point western-NA rectangle; sparse historical set | Versioned explicit-point support, preserving historical keys/IDs; selected support must drive fetches, partial checks, cache identity and reporting. The old `nasa-power-0.5-degree` label is not its sample spacing. |
| ERA5-Land soil | 1,568 quarter-degree cells; native 0.1-degree box matching; 1,470 expected valued cells | Generated regional support and admitted domain-mask semantics. The PNW 98-cell null allowance is not a global fact. Preserve native-box collision checks and historical ordinals. |
| Sentinel-2 vegetation | Same 1,568-cell analysis lattice, 5x5 subpoint sampling; assets carry native affine/pixel geometry | Generate stable regional identities and support digest; retain expected support through clouds/no scenes. Count alone is not identity. |
| Weather | Region-derived current queries | Correct current-day execution semantics; no invented historical archive or coverage-target suppression of current capture. |
| FIRMS / watersheds | Config strategies read INGEST_BBOX instead of their passed region | Bind the request envelope to the selected manifest and apply source/size limits. Static watershed versions do not use daily coverage targets. |

Historical identity loaders must remain a compatibility branch: do not regenerate existing cell IDs from a new UUID rule or replace the sparse climate set with its containing rectangle. New regions need a versioned support definition with deterministic keys/IDs, coordinates, ordering, source/model identity, analysis support geometry, mask provenance and request-unit assignment. Hash the actual definition, not its cell count or slug alone.

Key seams: `pipeline/direct/climate/{support,forward}.py`; `pipeline/lanes/soil/{support,open_meteo_era5_land}.py`; `pipeline/lanes/vegetation/{support,sentinel2_ndvi}.py`; `pipeline/lanes/fire_detections/nasa_firms.py`; `pipeline/lanes/watersheds/nhdplus.py`.

Preserve fixtures for the 397-point historical climate plan, native soil-box matching, vegetation affine/sampling behavior and pinned historical support. Add checks for equal-count/different-coordinate identity, reordered-input determinism, partial/cloud/null outcomes, native-box collisions and cross-region object/checkpoint isolation.

## Temporal and recurring-cost distinctions

- Only water currently implements the config runner's source-support contract. Targets on legacy TOMLs would not govern their actual scheduled writers.
- Water's 550-day revision horizon and 31-day blocks intentionally reread history. Give corrections a separate cadence/budget policy; retain near-edge updates.
- Climate, soil and vegetation need separate decisions for new settled days, historical holes, partial support, absence rechecks and source-free publication repair.
- MTBS is release-cohort history, not a daily denominator. Crop maintenance already idles upstream within a checked month, but daily physical integrity reads still cost work.
- BLM backfill/reconcile often replays stored captures or repairs publication. Preserve that source-free recovery regardless of acquisition targets.
- Snapshot watermark checks and weather/NWS rolling capture remain necessary. Historical coverage must not stop them indefinitely.

The bootstrap must require isolated executor/database state as well as an isolated object prefix. Existing ingest policy refuses rectangles wider than 30 degrees longitude or 20 degrees latitude and antimeridian crossings. Supporting larger US footprints requires partitioning; silently broadening those limits is not admission.

Custom definitions also expose a previously immutable assumption: `regionIdentityVerdict` compares only slugs. Web/service definitions can disagree under the same custom slug, and persistent browser caches may retain earlier geographic data. Content-aware manifest parsing does not solve either issue. Carry the canonical definition/support fingerprint through availability and cache namespaces before calling custom-region deployment admitted. Changing history alone should not unnecessarily invalidate a geographically identical deployment.
