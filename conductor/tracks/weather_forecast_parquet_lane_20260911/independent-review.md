---
type: independent-review
track: weather_forecast_parquet_lane_20260911
review_base: 362422e3dffebb61a68fd4d7303234c3a14a43ed
status: static-findings-resolved-final-verification-pending
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

The inspected `evidence/frontend-result.json` records exit code zero for boundary,
typecheck, lint and tests. The initial `evidence/python-result.json` records exit
code one and is not a passing receipt. The owner reported Windows temporary-root
permission setup failures and a recovery sweep using a workspace temporary root;
that recovery result is pending at this checkpoint. Final counts and scope must
come from the completed selector receipt. A changed-selector fallback covering
the full test set must still be described by its invocation and actual coverage,
and does not create a full Python quality receipt or track archive acceptance.

This file is a static review receipt, not a passing test receipt or archive
approval. No runtime test was run in this review lane. After the integrated sweep,
record exact commit and evidence references and close the final verification
status against that tree.
