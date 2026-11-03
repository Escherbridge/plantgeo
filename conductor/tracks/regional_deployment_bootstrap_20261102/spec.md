---
type: feature-spec
---
# Regional deployment bootstrap

This continues the original November 2 goal: an operator supplies credentials, chooses a US region and desired data/history, and starts a coherent deployment with minimal manual wiring. The initial water coverage policy and PNW guide do not complete this requirement.

## Required outcome

One configuration defines the geographic footprint, selected supported data layers, history interval, target coverage, correction policy and bounded acquisition budgets. A bootstrap command validates it and produces a reproducible deployment bundle. Web and service use the same canonical manifest; independent storage and executor state prevent cross-region reuse. Requested unsupported combinations fail with named reasons, never silently falling back to PNW or dropping layers.

## Delivery contracts

1. Both runtimes consume the same canonical snake_case region JSON through explicit configuration. Packaged PNW/Kenya defaults remain compatible; unknown or inconsistent selection fails. Browser manifests contain no secrets. Docker propagates the public selection and manifest at build time.
2. Bootstrap generates region/lattice definitions, selected lanes/providers, credential-free environment templates, isolated storage identity, database-isolation requirements and a machine-readable readiness report. Repeating the same configuration is deterministic. It never starts acquisition during configuration or validation.
3. Core requested regional environmental layers must actually honor that footprint. Water, weather, fire, soil, vegetation and climate require source-specific admission; national/global upstream scope alone is insufficient. Preserve existing PNW support identities while generating distinct deterministic support for other regions.
4. Historical acquisition remains enabled and automatically idles at its declared target. Forward acquisition, revisions and publication repair have separate policies and budgets. Current-only, release-series and static sources retain their actual temporal semantics.
5. Large/antimeridian US footprints require supported partitioning or an explicit bounded-region refusal. A refusal is truthful but does not establish completion of arbitrary-extent support.
6. Region and source identity survive through publication, availability, serving, UI and agent responses. Unbound, unpublished, absent, partial and stale states remain distinct.

## Evidence required for completion

- A fresh non-PNW US deployment configured without source-code registration edits, exercising multiple core layers end to end.
- Two different regions demonstrate separate support identities, object prefixes and executor state; changing history alone can reuse the same geographic deployment safely.
- Provider credentials are checked without disclosure; missing/invalid credentials fail without fallback or silent layer omission.
- Configured date ranges, fractional targets, revised source support, current-only restrictions and source-free publication repair have behavioral coverage.
- Generated web/service manifests and Docker configuration agree, and live selected-day UI and agent evidence agree with serving state.
- Appropriate integrated gates and independent review, with release separate from Catalyst research.

The two requested dev-drive cycles for the initial release are complete. Follow-up implementation and targeted independent verification do not retroactively add another cycle to that completed drive.
