---
type: evidence-receipt
track: platform_experience_qa_20260911
recorded_on: 2026-09-11
observed_at: 2026-09-12T04:23:23Z
status: candidate
---

# Integrated historical-weather and botanical-census candidate

## Immutable intake

The integration branch started from local `main` commit
`8c3ecf04433f69c9afcaf11c2b6eac45086f2ec5`. The reconciled implementation
and owner-evidence commit is
`b95f18a2dd74189962ebf522a427cbde95577cef`, tree
`e7f61047b95684fdce2c2e1dacd3a84f6d066693`.

The candidate binds three reviewed handoffs:

| Handoff | Commit | Tree | Reconciliation |
| --- | --- | --- | --- |
| Historical weather visual | `8e53b416ce5dc5287295a707dae2f9c121e1e993` | `23ba93ff46458c5d8bd399072ce667d152d11235` | All 18 destination blobs and modes matched; no unmerged paths or shared-file conflict. |
| Botanical production census blocker | `557c4c06a58fec6876bae474f14c8070de929ae0` | `380e2274e6a5eaa4ff274cb9fed28b9bc6553192` | Sole evidence blob `bf9bf89faa06626d6fd1db492ff8519e0d652c1a` was preserved verbatim. |
| Root-QA task ledger | `eb7b6d0f29dace5c6685ddac140fe713b50aa7c1` | `b4a143e6227db737035467f5ddfad4b3ae197475` | Sole reviewed ledger blob `0d2e640731339891b4dfd30bf6206a285bb9d455` was applied to its exact parent blob. |

No handoff touched an active data writer, source load, Railway resource,
production database, object-store release, remote branch or deployment.

## Final integrated verification

The following gates judged the combined candidate after the implementation and
owner evidence were committed:

| Gate | Result |
| --- | --- |
| `npm run check:data-boundary` | PASS: 12 documented URL rules, restricted imports and observation-fabrication checks. |
| `npm run type-check` | PASS. |
| `npm run lint` | PASS with 0 errors and 553 warning-only findings already admitted by the repository gate. |
| `npm run test:changed -- --base main` | PASS, scoped changed surface: 49/49 related files and 1,182/1,182 tests; contract surface 9 files passed, 2 skipped, 213 passed and 13 skipped. This is not a full-suite receipt. |
| `uv run --no-sync python scripts/check.py --changed --base main` from `services/agri-data-service` | PASS for format, Ruff and mypy. The selector found no changed Python service paths (`mode: none`) and therefore correctly selected no pytest batch; this is not a full Python quality receipt. |

The clean integration worktree intentionally reused existing dependency
environments rather than installing packages. The frontend dependency junction
was sourced from the reviewed weather-owner checkout. The Python environment
was sourced from the main checkout only after its `uv.lock` SHA-256 matched this
candidate exactly (`930dea83dbb12804e8d5356858feeb4cb7ce0fdeecda8ff951ea7b7e0cdf5319`).
Temporary junctions and generated cache/build artifacts were removed after the
gates completed.

The contract batch skipped four real-PostGIS cases because `POSTGIS_TEST_DSN`
was unset and nine climate SQL cases because
`PLANTGEO_TEST_DATABASE_URL` was unset. No database credential was inferred or
substituted.

## Remaining acceptance gates

1. Root QA completed the available fixed-desktop unavailable-state browser
   check on the immutable candidate. The result is **partial**, as recorded in
   [`browser-weather-20260911.md`](browser-weather-20260911.md): the traditional
   report treatment, Climate placement, explicit retry/unavailable state and
   no-stale-frame behavior passed, while populated and mobile journeys remain
   open.
2. The governed Parquet reader and slider-capability services were unavailable
   during root-QA browser acceptance. Live raw-point and aggregate-cell
   temperature labels, wind-label collision behavior, precipitation hover and
   actual selected-day transitions therefore remain visually unproven. The
   accepted browser evidence covers the unavailable-day/no-stale-frame state,
   not live production data.
3. Narrow-mobile visual acceptance remains pending because the available owner
   browser surface had a fixed desktop viewport.
4. The botanical census remains blocked: Railway and the production database
   were not accessed, WCVP remains unaccepted, no candidate source release is
   admitted, and `agri.species` remains an authoring surface rather than a
   published profile or serving fallback.

This receipt authorizes neither deployment nor production/data-plane mutation.
