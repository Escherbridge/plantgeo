# Holonic and Kimball modeling standard

> **STATUS — 2026-08-22, body below untouched.** The architecture pivot
> (`conductor/RUNBOOK.md` §0.23/§0.24) retires the Postgres materialized-view
> warehouse this standard is built on: "Parquet files are the materialised views
> and continuous aggregates" now (§0.23.1), computed at ingestion rather than as
> Postgres holon/dimension/fact tables. The holon-envelope and conformed-dimension
> *thinking* may still be worth preserving in Parquet-native form, but nothing
> here has been re-specified for that. Read RUNBOOK §0.23/§0.24 before applying
> this standard to new work.

## Purpose

Every datum used by a PlantGeo model must be retained in the warehouse with
replayable source, process, support, quality, and availability metadata. The
warehouse uses two complementary shapes:

```text
immutable source/release/artifact
        -> holon/process envelope
        -> Kimball conformed dimensions + facts
        -> feature snapshot / model / evaluation holons
```

The holon envelope makes every object traceable through its whole-to-part and
part-to-whole process lineage. Kimball stars make those retained objects fast to
mine across source, time, geography/support, signal, experiment, and quality.

## Existing authoritative envelope

The existing governed relations are the immutable base envelope:

| Layer | Existing relation | Meaning |
| --- | --- | --- |
| Source holon | `agri.data_source` | Stable producer/distributor identity and policy metadata |
| Release holon | `agri.source_release` | Immutable retrieved payload, observed/available times, checksum, schema, quality |
| Artifact holon | `agri.artifact` | Content-addressed raw input, export, model, or report bytes |
| Process boundary | `agri.release_set` / `agri.release_set_item` | Checksummed set of releases available to one computation |
| Model/evaluation holon | existing feature, model, training, forecast, and receipt relations | Immutable compute identity and output lineage |

No training code may consume an unretained local file. Its raw file and the
frozen normalized/feature export must first be bound to this envelope.

## Holonic projection contract

Holonic is a modeling discipline here, not a new universal wrapper table.
Existing typed relations remain canonical: source, release, artifact,
release-set, normalized feature, feature snapshot, model, training run,
forecast iteration, value, and receipt already carry lifecycle, checksums, and
foreign keys. A projection must expose the following coherent whole-to-part
context for every analytical object without creating a polymorphic second
lineage graph:

- immutable source/release/artifact/release-set identities and checksums;
- typed process/run/recipe/code identities from the existing feature, model,
  training, forecast, and receipt relations;
- canonical observed, valid, available, and warehouse-recorded timestamps;
- native and allowed support/resolution; and
- quality, access, evidence, and claim class from the typed source/fact.

A new physical analysis registry is permitted only where no typed relation can
represent a source-specific object. It must have one immutable grain, explicit
kind/version uniqueness, typed foreign keys, and no optional polymorphic
table/key edges.

## Kimball analytical plane

Conformed dimensions are read-only projections over canonical relations first:

| Projection | Canonical authority |
| --- | --- |
| `v_dim_source_release` | `data_source`, `source_release`, `artifact`, `release_set`, `release_set_item` |
| `v_dim_support` | `spatial_cell`, crosswalks, normalized feature/series support columns |
| `v_dim_signal` | signal/series definitions and their typed units/transforms |
| `v_dim_process` | source transform, feature snapshot, training run, model, forecast, and receipt relations |
| `v_dim_experiment` | typed feature/model/training/evaluation identities, split artifacts, and policy receipts |

Observed, valid, available, and warehouse-recorded instants remain canonical
`timestamptz` facts. A later calendar projection uses explicitly role-named date
keys; it does not collapse the clocks into one `dim_time`. QA flag, coverage,
missingness, correction, and retraction remain observation-level facts unless a
measured query justifies a constrained dimension.

Core facts are append-only:

| Fact | Grain | Initial use |
| --- | --- | --- |
| `v_fact_signal_observation` | One source observation at native support/time | Projection of `signal_observation` and its contract views; never a 34.8M-row copy |
| `fact_crop_spectrum_signature` | One GHISACONUS signature | New source-specific fact only if existing normalized feature relations cannot represent it; FK to the retained source release/artifact |
| `fact_crop_spectrum_band` | One signature × one wavelength band | New source-specific long fact only if needed for mining/QC; `dim_spectral_band` retains wavelength/band order |
| `v_fact_feature_snapshot` | One feature row in a frozen forecast experiment export | Projection or typed FK to `forecast_feature_snapshot` for compatible forecast evidence only |
| `v_fact_prediction` | One target/origin/model forecast prediction | Projection or typed FK to forecast iteration/value/receipt relations for compatible forecast evidence only |
| `v_fact_evaluation` | One forecast model × held-out slice/metric | Projection or typed FK to forecast model/training/evaluation/receipt relations for compatible forecast evidence only |

GHISACONUS classification must not be forced into forecast-only model tables.
If existing typed benchmark relations do not fit, add a small typed benchmark
model/run/prediction/evaluation set with direct source-release, artifact, and
grouped-split foreign keys. Its grain and immutable checksum contract must be
specified by a forward-only migration.

For numerical training, a compact checksum-bound matrix artifact may accompany
the long band fact; it is a performance projection, not a second source of
truth.

## Current implementation order

1. Retain GHISACONUS metadata, ZIP, and CSV as warehouse artifacts under one
   immutable source release and validated release set.
2. Start with a documented query-only Kimball projection contract over retained
   signal, release, quality, support, and forecast relations. Any database view
   is a forward-only migration with regenerated declarative schema; document
   canonical authority and `as_of` behavior before adding it.
3. Add only the GHISACONUS-specific signature/band and grouped-split structures
   that the existing normalized feature and artifact plane cannot represent.
4. Normalize the benchmark into those typed facts; retain the deterministic
   grouped split and feature matrix as artifacts with direct foreign keys.
5. Materialize a projection only after a measured query need, with an explicit
   refresh and availability contract. Retain every model, metric, prediction,
   and report through the existing typed envelope.

Utility, parcel, and private-AOI data are not part of this phase. They can be
appended later as new source/release holons and conformed facts without changing
the public benchmark or its historical claims.
