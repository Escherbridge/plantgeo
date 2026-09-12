---
type: evidence-receipt
track: platform_experience_qa_20260911
recorded_on: 2026-09-12
observed_at: 2026-09-12T14:11:16Z
status: local-presentation-candidate-verified
---

# Root integrated checks — weather label candidate

This receipt records the single final check sweep after the bounded weather
presentation changes and the font-safe wind-label candidate. No source download,
database or Railway access, ingestion, writer, deployment or push operation was
performed.

| Check | Result |
| --- | --- |
| `npm run check:data-boundary` | PASS — 12 documented URL rules, restricted-import and observation-fabrication checks |
| `npm run type-check` | PASS |
| `npm run lint` | PASS — 0 errors; 545 existing warnings |
| `npm run test:tooling` | PASS — 6 tests |
| `vitest run --configLoader runner --maxWorkers=2` | PASS — 150 files passed, 2 skipped; 2,240 tests passed, 13 skipped |
| Independent visual review | PASS — explicit `from <cardinal> <speed>` wording preserves meteorological semantics and avoids Unicode-tofu dependence |

The skipped suites require explicit disposable database DSNs for real PostGIS
and climate-reader SQL coverage. Existing React `act(...)` notices and the
repository's pre-existing lint warnings remain recorded output, not failures.
The candidate remains a local uncommitted presentation change because Git ref
locking is denied in this sandbox. This receipt does not close populated-data,
mobile/touch, hover, accessibility, forecast, source-admission, release,
database, Railway, writer, deployment or push gates.
