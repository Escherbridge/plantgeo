---
type: track-plan
track: pnw_herbaria_source_admission_20260911
status: active
---

# Plan

The authorized admission session may capture one bounded data-only WTU and UBC
archive into quarantine after the exact collection-level rights, coordinate-
withholding and archive-safety gates pass. It may inspect, hash and inventory the
archives and prepare the downstream ingestion handoff. Images, public serving,
scheduling and production mutation remain excluded.

## A0 — freeze the candidate set

- [ ] Re-open the current portal download inventory and WTU/UBC provider pages.
- [ ] Record exact collection keys, access URLs, advertised counts, dates and
  terms; keep images and non-admitted collections out of scope.
- [ ] Define the intended PlantGeo use and distribution surface against which
  collection terms will be judged.

## A1 — bounded metadata and archive inventory

- [ ] Fetch EML, field mappings and response metadata under the documented caps.
- [ ] If terms permit, capture each complete archive once into quarantine and
  verify the ZIP/member safety, hashes, counts and schema.
- [ ] Reconcile accepted, out-of-envelope, excluded-by-rights, nonspatial,
  duplicate-native-record and quarantined counts.

## A2 — release and identity proof

- [ ] Compare at least two equivalent complete releases before treating a native
  identifier or source watermark as stable.
- [ ] Specify correction, withdrawal and source-shrink behavior without treating
  a partial export as a deletion feed.
- [ ] Issue one admission/refusal packet per collection and obtain independent
  governance review.
- [ ] Keep the author and independent governance/archive-safety reviewer in
  separate task contexts; the reviewer owns the final admission verdict.

The downstream Parquet and experience tracks remain planned until A2 admits at
least one exact collection release.
