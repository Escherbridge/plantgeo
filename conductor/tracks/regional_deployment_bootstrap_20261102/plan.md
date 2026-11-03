---
type: implementation-plan
---
# Regional bootstrap completion plan

- [x] Revalidate deployed baseline and identify remaining requirements.
- [x] Audit regional registries, Docker build variables, source support and remaining ingestion duties.
- [x] Add validated canonical custom-manifest consumption to Python and web, preserving packaged deployments.
- [x] Wire Docker build-time region configuration and prove cross-runtime manifest parity (not production namespace/support identity).
- [x] Carry canonical region identity through serving responses and persisted cache scopes, with custom operations refused until source admission.
- [ ] Admit per-stream source support and carry its proof through availability; a geographic namespace reservation alone cannot authorize data.
- [ ] Build deterministic operator configuration/bootstrap and preflight with explicit layer decisions and state isolation.
- [ ] Generalize core regional support and request footprints; preserve PNW identity compatibility.
- [ ] Correct current-only timing where required and admit temporal policies per source.
- [ ] Extend historical coverage targets, independently bounded corrections and source-free repairs at actual executable seams.
- [ ] Exercise a fresh non-PNW multi-layer configuration and a second isolated region.
- [ ] Independent review, batched integrated verification and separate data release.
- [ ] Post-deploy serving/UI/agent verification against the full requirements.

## Confirmed implementation obstacles

Initial findings about hard-coded runtime registries and missing public Docker region settings were resolved by the foundation checkpoint. Remaining obstacles: climate uses 397 pinned cells; soil and vegetation use 1,568 pinned cells; fire and watersheds read INGEST_BBOX rather than their passed region. Only water currently supplies the runner source-support identity contract. Weather's current-only strategy must not be scheduled through a yesterday-only window. Legacy TOMLs do not control legacy runtime behavior merely because a policy is added to them.

Water's 550-day historical revision window is distinct from gap-fill and remains a recurring cost. Crop maintenance already skips upstream work within a checked month, but physical integrity checks still cost reads. Snapshot sources need version/refresh policy rather than daily coverage denominators. These distinctions must survive the generalized policy.
