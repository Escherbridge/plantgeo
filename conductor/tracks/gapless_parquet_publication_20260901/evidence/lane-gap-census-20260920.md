---
type: evidence
---

# Non-ML lane gap census - 2026-09-20

Read-only census against production coverage generated at
`2026-09-20T13:14:46.073764Z`, the current lane/executor registries, and direct object-store ladder
audits. No production data, availability pointer, Railway variable, or ML-owned resource was changed.

## Current lane state

| Lane/family | Current evidence | Remaining work |
| --- | --- | --- |
| Dense climate fields except RH/shortwave | Through 2026-09-15; 1,600 days for the 2022-floor products and 15,599 dew-point days; zero gaps; four-rung availability pointers | Runtime selected-day/agent traces and three scheduled advances only |
| Dense soil fields | Through 2026-09-11; 1,596 days each; zero gaps; four-rung availability pointers | Runtime traces and scheduled advances only |
| Relative humidity | 3,124 published; 56 historical gaps (`2022-02-09`, `2022-03-06..2022-04-29`) | Bounded NASA POWER historical repair for those exact dates; data or provider-proven absences |
| Shortwave radiation | Zero admitted days; correctly withheld `availability_stale` | Re-probe `ALLSKY_SFC_SW_DWN`; admit only non-`-999` data or governed provider absences |
| Drought | Release history complete through the 2026-09-15 provider edge, carried through 2026-09-20 | `/release`/map/agent requested-day vs served-day proof |
| Fire detections | 8,384 published, 1,069 governed absences, zero gaps, through 2026-09-18 | Runtime proof; preserve a source-specific historical repair path for any future old hole |
| Burn severity | Seven admitted releases, 2,102 absence days, 2,079 gaps | R3 MTBS release-cohort history; never calendar-day jobs for a release series |
| Vegetation | 1,220 published, 276 absences, five gaps (`2026-09-01..05`) | Upgrade the legacy marker and reconcile `09-01`; source-probe `09-02..05` and publish data or governed absences |
| Water gauges | 1,546 published, 11,594 gaps, through 2026-09-20 | Upgrade/reconcile physical `2026-09-06`; implement bounded historical NWIS repair for the remaining gaps |
| Weather observations | 1,473 published, three absences, 1,709 gaps | Resolve the historical archive/source identity; the current-conditions two-day recovery path cannot backfill the claim |
| Sensors | 24 published, two absences, 27 gaps | Complete/reconcile 25 repairable days `2026-07-30..08-23`; independently resolve `2026-09-10..11` and the absent registered floor `07-29` |
| Evacuation zones | Nine version days; census authority; prior audit found legacy z13-only versions | Audit only real version days, derive required rungs/markers, bootstrap static availability |
| Fire perimeters | 59 complete version days reported previously; no availability pointer | Re-verify exact versions and bootstrap static availability |
| Watersheds | One complete version day (`2026-08-07`); no availability pointer | Re-verify and bootstrap static availability; continue watermark polling |
| Soil survey | Roughly 959 z13 objects / 957.5 MB, but no admitted complete ladder or pointer | Reconcile SSURGO rows, admit publisher, derive ladder/receipts, bootstrap availability, replace hard refusal |

## Physical disputed-day audit

- `sensors/observed`, `2026-07-30..2026-08-23`: all 25 days have physical z13 data and legacy
  count-only completion markers; z9/z5/z0 are missing. With the reviewed marker-upgrade logic they
  classify as repairable without provider traffic.
- `vegetation/observed`, `2026-09-01`: all derived rungs exist; the z13 marker is legacy and can be
  physically re-attested before availability reconciliation.
- `vegetation/observed`, `2026-09-02..05`: z13 has no source parts. These require a source probe,
  not a ladder-only operation.
- `water-gauges/observed`, `2026-09-06`: all-rung presence is insufficient because the base marker
  carries no part identities. The strict reconciler correctly refuses it until physical receipts are
  rebuilt.

## Repair order

1. Apply the locked marker/ladder operator to the 25 sensor days; review and CAS-publish availability.
2. Upgrade/reconcile vegetation `09-01`; separately source-probe `09-02..05`.
3. Upgrade/reconcile water `09-06`; keep the 11,593 historical gaps for the NWIS R3 verb.
4. Bootstrap static availability in the order watersheds, fire perimeters, evacuation zones; admit
   soil survey only after source/row reconciliation.
5. Repair the 56 RH dates, then address weather-history identity, NWIS history and MTBS cohorts as
   distinct source problems.
6. For every publication, prove the same generation through direct serving, public map/tRPC and
   selected-day/temporal/spatial agent tools.

The HTTP window route remains bounded to 31 days and 120,000 rows with no cursor. It is not a bulk
completeness surface; all physical census and repair verification must use the object store directly.
