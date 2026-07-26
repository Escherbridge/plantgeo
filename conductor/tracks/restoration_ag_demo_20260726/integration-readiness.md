---
type: integration-readiness
---

# Restoration Agriculture integration readiness

## Decision

The first coordinated run is an **offline historical crop-spectrum
classification benchmark**, not a map-goal demo or strategy selector. It binds
to `kaggle_ghisaconus_mirror` Version 1 and its checked receipt. It must state:

> This evaluation-only benchmark classifies historical crop hyperspectral
> signatures. It does not identify property management, forecast water or
> yield, or show that a restoration strategy caused an outcome.

The source register's public/non-synthetic policy is authoritative for this
benchmark. A public map, property, utility, or causal vertical retains its
separate AOI, authorization, support, and outcome-label gates.

## Frozen inputs

| Artifact | Status | Binding |
| --- | --- | --- |
| GHISACONUS public benchmark receipt | Accepted, evaluation-only | `receipts/kaggle-ghisaconus-v1.json`; CSV SHA-256 `e2f5a21b24fac00e930520ba959ab54cc8a3f8c56368f8e0a1868bbf3e3377d5` |
| Forecast phase-zero export | Frozen locally, evaluation-only | `C:\tmp\plantgeo-frozen-forecast-20260726\manifest.json`; manifest SHA-256 `1bb6a6a707b432f2036edba86a426a32c1c04304b350af4caaec14a48cb20d09` |

## Session packet

| Session | Start now | Required handoff | Blocking gate |
| --- | --- | --- | --- |
| A — data steward | Produce a durable, columnar GHISACONUS export from the accepted CSV; profile finite band values, duplicate rows, image-to-crop cardinality, and support by crop/image. | Export and profile checksum; exclusions file | Fail closed if image-group support cannot sustain every declared split/class. |
| B — data architect | Specify raw-to-export mapping, receipt binding, `Image` as the leakage-control group, and a private-free offline access boundary. | Mapping and feature-availability contract | No coordinates, AEZ, stage, or calendar field may be a primary classifier feature without a separately declared shortcut experiment. |
| C — evaluation/product | Pre-register `Crop` as the target; create deterministic grouped train/validation/final-test allocation (about 60/20/20 images); define macro-F1 and balanced accuracy as primary. | Target card, split manifest, abstention/wording card | Rice or any class lacking enough independent image groups must be declared unsupported; never random-row split or silently merge classes. |
| D — integration | Reconcile receipt, export, mapping, target card, and split checksum into one evidence matrix. | Approved/revise/abstain decision | Reject a mismatch in source, clock, feature list, split group, or claim wording. |
| E — single writer | Only after D approval, implement/run majority, ridge, then one bounded nonlinear classifier. | Checksummed evaluation artifact | No map publication, forecast publication, or strategy/effect output. |
| F — independent verifier | Replay export/split bindings and review grouped-fold integrity, calibration, per-class metrics, and wording. | Final benchmark decision | No approval without untouched final-image evaluation. |

## Deferred-data goals

The current phase does not acquire utility, parcel, private-AOI, meter, or
private operational data. Water/energy, parcel vegetation, site soil,
yield/cost/biodiversity, and strategy-selection cards remain explicitly
deferred until future data is appended through a new receipt and target
contract. They do not stop the two public/retained-data capabilities above.

## Seasonal forecast phase-zero decision

The local frozen forecast export can now support a read-only quality/profile
report. It contains 1,462 daily source observations and 98 rows from 14
seven-day hindcasts. Its warehouse schema head is `20260722_0007`, predating
the later forecast-iteration contract. It may not support a 30-day model
selection claim without additional independent 30-day origins and a
pre-registered final holdout.
