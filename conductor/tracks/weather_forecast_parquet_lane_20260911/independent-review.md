---
type: independent-review
track: weather_forecast_parquet_lane_20260911
review_base: 362422e3dffebb61a68fd4d7303234c3a14a43ed
status: approved-isolated-local-handoff
reviewed_commit: aab0dceb6fb76ff3164b440f4fa48da5748f88a9
archive_ready: false
---

# Independent weather batch review

This review covers the isolated uncommitted forecast contracts, source normalizer,
local artifacts/readers, agent context and browser presentation. The reviewer did
not author those modules. Static inspection only: no test, lint, typecheck, network
request, deployment, production publication or external mutation was performed.
The implementation owner owns the single integrated sweep after all fixes.

## Findings delivered to implementation owner

1. **P1: Equivalent UTC timestamps can clear a valid frame.** The initial
   `src/lib/environmental/weather-forecast.ts` selection matcher compares
   start/end strings literally, its hourly map keys retain literal strings, and
   `src/components/map/layers/weather-forecast/presentation.ts:10` compares the
   selected valid time literally. Python serializes whole-second UTC datetimes
   with `Z`; JavaScript `Date.toISOString()` includes `.000Z`. The same instant
   therefore fails selection matching, map filtering and current-hour lookup.
   Normalize UTC hour identity throughout these seams and cover a real Python
   wire-style timestamp against JavaScript-style selected timestamps.

2. **P2: Explicit absence reasons are hidden without run provenance.** Initial
   `src/components/panels/WeatherDetails.tsx:24` rejects a null run before
   rendering status. The plane intentionally returns null run plus empty values
   for missing manifests or corrupt/unavailable artifacts. Permit an exact
   selection with empty null-run absence, then render the governed status. The
   owner started correcting this during review; final regression evidence is
   still pending.

3. **P2: Map cleanup can dereference a removed style.** Initial
   `src/components/map/layers/weather-forecast/useForecastMapSource.ts:21`
   unconditionally calls `getLayer` during effect cleanup. Existing MapView
   cleanup removes the map. Installed MapLibre `src/ui/map.ts:3757` removes its
   style, while `getLayer` at line 2884 directly dereferences that style. Handle
   removal before child cleanup and an absent style; cover unmount after removal
   and style reload with latest data in lifecycle tests.

4. **Publication provenance gate:** initial `prepare_run` completes the immutable
   manifest, and pinned `read_run` can serve it before active-pointer selection.
   The caller supplies `published_at`; the browser labels it PlantGeo published.
   A declared timestamp is not evidence of an actual local publication event.
   Require an explicit committed publication receipt or honestly distinguish
   caller assertions from recorded local visibility. Active-pointer selection
   must stay separate from pinned run readability so rollback does not invalidate
   older published runs. The owner also identified inferred Arrow schema as a
   problem and requested a fixed schema before the sweep. Both corrections need
   separate verification before claiming governed publication.

## Positive evidence and scope

Source normalization pins the exact Single Runs endpoint/model/request and its
240-hour axis, preserves source nulls and returned coordinates, rejects invented
provider issue/model-version metadata and mismatched units, and preserves exact
source hashes. The retained fixture distinguishes capture CRLF from response
bytes. The source reconciliation separates data attribution from commercial API
entitlement; no production entitlement is established.

Wind uses eastward/northward velocity components with the meteorological from
convention and component averaging. Browser arrows point downwind; calm and
unpaired samples do not create arrows. Scalar points imply no continuous support.
Daily precipitation totals require contiguous complete source intervals, with
tests authored for 23/25-hour timezone days. No interpolation or probability is
invented for the sampled deterministic candidate.

Local path tokens, descendant symlink refusal, hash/size checks, finite-value
contracts, complete hourly inventories and fixed read ceilings are appropriate
for a trusted local artifact root. The protocol explicitly excludes hostile
concurrent filesystem mutation and production storage. Agent context is host
bound to picked/search forecast mode and retains the selected run/window/place.

Security: no additional actionable vulnerability found in the bounded local
scope. Correctness: fixes above pending. Performance: artifact and draw bounds
exist; real request-to-paint/mobile evidence remains pending. Maintainability:
the isolated ownership seams and directory-level rationale are clear.

## Readiness

Neither track is archive-ready. Shared runtime integration is intentionally held
for the canonical post-botanical handoff; it is an acknowledged dependency, not
an unnoticed omission. HTTP/MCP registrations, active availability/catalogue
integration, scheduled forward/repair/status duties, durable provider cooldown,
remote immutable publication, actual admission/entitlement and real-data
desktop/mobile accessibility and canvas evidence remain open. The saved April
28 catalogue gap proves snapshot unavailability only, not what painted the
original screenshot. Tests here do not certify that shared runtime regression.

## Correction re-review

The independent reviewer re-read the corrected browser files and their authored
regressions. Findings 1–3 are resolved in code: `utcInstantKey` supplies canonical
UTC identity for selection, hour grouping/selection and field filtering; null-run
empty absences preserve exact-selection status; and the existing
`safeRemoveLayerAndSource` helper guards absent/removed map styles. Tests now
exercise Python/JavaScript timestamp spelling, both null-run absence reasons,
style reload using current data and removal before component cleanup. The card
also checks expected product identity for responses carrying a run.

The artifact re-review confirms an explicit registered Arrow schema with UTC
timestamp and nullable numeric columns, validated against decoded Parquet.
`read_published_run` now requires an immutable publication receipt binding the
prepared-manifest hash. Activation creates that receipt with its publication
clock, and the plane returns the receipt timestamp instead of the caller's
prepared assertion. Preferred-run pointer changes do not remove earlier pinned
publication authority. These changes resolve the identified local publication
and inferred-schema concerns in code. Production admission still requires its
separate provenance and service integration.

The requested regression for interruption between publication-receipt commit and
preferred-pointer write is now present. It asserts that the committed pinned run
remains readable, and retry preserves its first publication instant. Directory
documentation now states that exact two-event protocol. A further real-fixture
end-to-end test covers source normalization, immutable Parquet preparation,
prepublication refusal, publication, and the same selected-location agent
context, including the archived source run's stale status. Final quality-only
formatting changes were also inspected; no further static finding was identified.

## Final independent closure

**Approved for isolated local handoff only**, against implementation commit
`aab0dceb6fb76ff3164b440f4fa48da5748f88a9` on
`codex/weather-forecast-20260911`, with review base
`362422e3dffebb61a68fd4d7303234c3a14a43ed`. All identified static findings are
closed. The reviewer verified HEAD matched this commit and the worktree was
clean before this documentation-only closure. All 27 tested working-file hashes
in `evidence/verification.json` matched the checkout. Following the evidence-only
hash-semantics clarification, the reviewer independently hashed all 27 committed
Git blobs and confirmed every recorded committed hash matched, with tree
`3508af01613724c34b6211fe58c5007b68763c30`. Separate working and committed hashes
account for text newline normalization. The scoped fixture `.gitattributes` uses
`binary`, preserving the exact captured response bytes across fresh checkouts.
No runtime test was run by this review lane.

The reviewer inspected these committed evidence files:

- `evidence/frontend-result.json`: zero exits for boundary, typecheck, lint and
  tests. `verification.json` records the changed-selector full fallback with
  145 passed files, 2,162 passed tests, 13 skipped tests and 11 weather tests;
  lint has zero errors and 542 warnings.
- `evidence/python-final-quality.txt` and `python-final-result.json`: format,
  lint and mypy PASS; quality exit zero. The selector explicitly records
  `receipt_eligible: false`.
- `evidence/python-final-tests.txt`: 114 passed with one nonfatal pytest cache
  permission warning; test exit zero. `verification.json` enumerates the six
  new weather files plus the two tests that failed for environmental reasons in
  the preceding broader recovery run.
- `evidence/verification.json`: preserves the unsuccessful initial and recovery
  runs. Initial Python selection had 5,561 passed, 147 skipped, one xfailed and
  529 temporary-directory setup errors. The broader recovery recorded 6,086
  passed, 149 skipped, one xfailed and two environmental failures; the final
  focused run then passed both previously failing cases and every weather test.

The final focused 114-test run is **not a full pytest rerun**. The changed-selector
fallback and recovery evidence support this bounded handoff; they must not be
reported as a clean final full-suite Python run or an official full Python
quality receipt. The initial failures remain part of the evidence rather than
being overwritten by a blanket pass claim.

This approval covers the isolated local contracts, source fixture adapter,
publication/readers and prepared presentation/agent modules. It does not approve
shared integration, production source admission, deployment, external publication
or track archival. Both tracks remain in progress and **not archive-ready** for
the dependencies and acceptance gates listed above.
