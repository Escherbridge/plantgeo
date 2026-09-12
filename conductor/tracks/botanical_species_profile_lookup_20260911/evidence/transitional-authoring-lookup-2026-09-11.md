---
type: evidence-receipt
track: botanical_species_profile_lookup_20260911
date: 2026-09-11
status: local-verified
---

# Transitional botanical authoring lookup

## Scope

This local slice adds a read-only, exact-UUID lookup over the already-modeled
`agri.species` and approved `agri.companion_relationships` relations. It adds no
migration, source, ingestion path, writer, candidate data, Parquet release,
production query, deployment, or recommendation behavior. Legacy profile values
remain explicitly unpublished and unverified; nulls remain explicit missingness.

## Validation

- Full selected sweep: `mypy` passed for 467 source files; `pytest` reached
  5,989 passed, 149 skipped and one xfailed, with one new failure caused
  by treating Python string methods as PostgreSQL range bounds. The full sweep
  also reported five formatting targets and ten lint findings.
- Consolidated correction: range rendering now rejects callable string methods,
  caller UUIDs are bound service-side in graph runs, all-null sections report
  `not_reported`, and formatter/lint findings were corrected together.
- Final bounded checks: Ruff format passed for 789 files; Ruff lint passed; mypy
  passed for 467 source files; focused botanical/agent/registration tests passed
  `66 passed, 2 skipped`.
- Separate dependency-last code review returned PASS after verifying both prior
  P1 findings. It did not approve production population, deployment, source
  admission, immutable publication, or completion of the broader track.

## Remaining boundary

Railway census remains blocked by the absence of an operator-authorized target
and read-only DSN. WCVP remains unaccepted. Published serving, training, profile
recommendation, suitability, objective-effect, occurrence, and fuel/fire claims
remain blocked until independently admitted evidence is frozen into an immutable
reviewed Parquet profile release.
