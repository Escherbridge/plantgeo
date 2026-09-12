---
type: evidence-receipt
track: platform_experience_qa_20260911
recorded_on: 2026-09-12
status: approved_bounded_local_scope
---

# Historical weather report: failed refresh

Review base: `0ee4f5bd99666760a5881ed1427301944dc7b001`.

Read-only preflight found a report presentation defect: a transport error can
retain a ready result in query memory. The report then displayed cached cards
and the map-marking action beside its no-cached-fallback error notice. The
installed query-core observer explicitly supports `isRefetchError` with data.

The report now withholds its presented result on query error. A synthetic
ready-to-error fixture holds the ready data constant and verifies that values
and the marking action disappear while the retry action still calls refetch.
Pending placeholders remain allowed with an explicit served-day caption.
Directory documentation records that distinction.

An independent read-only reviewer approved the three-file implementation,
fixture and directory documentation with no actionable findings.

## Final local verification

Dependencies were restored from the lockfile using offline npm installation
with lifecycle scripts disabled. All application edits preceded one final sweep:

- `npm run check:data-boundary`: passed.
- `npm run type-check`: passed.
- `npm run lint`: passed, zero errors and 545 warnings across the repository.
- `npm run test:changed -- --base 0ee4f5bd99666760a5881ed1427301944dc7b001`:
  source-related batch passed 6 files / 79 tests, including all 14 report tests;
  contract batch passed 9 files / 221 tests, with 2 files / 13 database tests skipped.
- `git diff --check`: passed.

The scoped test run emitted React act warnings. These results are fixture and
contract evidence, not a full-suite or browser visual acceptance receipt.

## Boundaries and next QA

The existing unavailable-state square-grid repair remains covered only by the
[prior bounded approval](weather-approval-decision-20260912.md). This change
does not modify map rendering or establish new populated-data visual evidence.
Declared aggregate support cells must not be removed as if they were placeholders.

`LayerManager` separately admits retained ready query data without checking
`isError`. A next bounded map fixture should exercise ready-to-refetch-error,
verify that failed frames do not remain painted, and preserve legitimate pending
retention. This report-only correction does not resolve that map path.

Further browser QA remains: synthetic dense wind-label collision, precipitation
hover, selected-day transitions, and narrow/mobile touch reflow. Synthetic
evidence must remain distinct from governed populated-data acceptance. Forecast
implementation and requested-day versus served-day policy remain separate tracks.

No governed readers, fallback sources, ingestion, writers, databases, object
storage, Railway, deployment, or push were changed or executed.
