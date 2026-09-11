---
type: track-evidence
slug: parquet_reader_cutover_acceptance_20260901
artifact: reader-local-handoff
status: local-verified-production-open
reviewed: 2026-09-11
source_revision: fa202230958fb55521963e886eb031be5fc266c4
candidate_revision: d56b508fb3afe8adb85969ce1553cd739daf779f
candidate_branch: codex/reader-cutover-acceptance-20260911
---

# September 11 reader acceptance handoff

The approved code candidate is `d56b508fb3afe8adb85969ce1553cd739daf779f`
on `codex/reader-cutover-acceptance-20260911`. Its following documentation commit
contains this evidence packet. The candidate is committed locally and has not
been pushed or deployed. The track remains active for the boundaries below.

This packet starts from the September 11 working-tree checkpoint at `fa20223`.
The fire hard cut, obsolete REST-route removal, governed availability consumption,
and MTBS rollout were already present. This branch repairs reader and caption
defects; it does not repeat those completed changes or certify production data.
The inherited checkpoint in this track's plan and metadata is retained. Other
tracks, the project registry and RUNBOOK are outside this change.

## Inventory and changes

| Boundary | Finding before edits | Result |
| --- | --- | --- |
| Plane envelope | Day/release reads decoded a valid-shaped response without binding its requested day to the caller; ordinary responses could claim a different served day. | Every terminal state must echo the requested day. Exact day/window members must serve that day. Only release reads may carry an older served day; future releases are refused. Window responses retain complete ordered membership. |
| Gauge/weather reader | Explicit UTC today selected the implicit live-edge lookback branch. | A supplied date always selects that exact day; only omitted dates use the existing freshness window. |
| Climate/soil collections | Published empty viewports, governed absence and unwritten days lost distinctions; an empty result could discard truncation. | Collections carry explicit Parquet terminal metadata. Empty published viewports remain published and retain served day and truncation. |
| Regional/point weather | Selected today was discarded; point weather accepted no date; proximity was not stated with the returned observation. | Selected-day requests reach the detail reader. Returned nearest gauge/weather evidence names observation day, signed/absolute day distance, measured spatial distance and bounded search bbox. |
| Regional cancellation | The HTTP request signal did not reach environmental context assembly. | The route forwards its signal into the assembler and row reads; aborted results cannot become successful context. Shared capability warming retains its existing independent cancellation policy. |
| Slider | Authority/source ceiling fields had no caption consumer. Unpublished availability was described as actively building. | Visible and accessible captions distinguish availability, object inventory and unstated authority, and identify the source publication limit without forbidding carried dates. An unpublished index is a settled unverified-date state. |
| Carried coverage | A drought release could be selectable after its source publication ceiling but still fall beyond the capability's described boundary. | The described boundary includes every proved common carried day while retaining the publication ceiling separately. Days beyond both boundaries remain undescribed. |
| Regional governed absence | An interior governed-absence range could fall through to published-empty and license a claim of no activity. | A separate governed-absence outcome and prompt instruction identify unavailable observations and forbid treating them as measured zero. |
| Cached coverage versus direct evidence | A direct governed absence could be discarded by value-only adapters while the five-minute capability memo still described the day as published. | Adapter terminal evidence and direct fire/MTBS absence take precedence over cached coverage. Public point weather returns the absence reason, served day and evidence instead of an internal error. |
| Truncated fire viewport | A valid empty-and-truncated result could be labeled observed zero using the capability memo. Unpositioned source rows or a spent serving budget can produce this combination. | Empty truncated fire evidence remains coverage-unknown and the prompt forbids claims of presence or absence. Nonempty truncated observations retain their values and incompleteness flag. |
| Regional temporal neighbours | Selected-day values had spatial proximity but no before/after evidence. | For fire, gauges and weather, availability-described candidates within 180 days on each side are read at detail zoom. At most six requests run across three concurrent source jobs. Each nearest spatial observation retains its own date, signed/absolute day distance and distance in metres. Empty local candidates, missing rungs, absences, truncation and refusals remain explicit. |
| Python point-feature semantics | An exact partition lookup claimed feature parity for carried, cumulative, static or rolling map products. | Six unsupported surfaces return `unsupported_feature_temporal_semantics` before any storage access. Ordinary daily feature reads retain exact day and spatial distance. This makes the capability limitation explicit; it does not complete wider product parity. |

## Current production identity

The read-only [deployment identity receipt](deployment-identities-20260911.json)
records `plantgeo-main` deployment `4fca7553-c5b0-4358-ba8b-907941be6c45` and
`plantgeo-parquet-api` deployment `9b65d7b8-40e0-4dac-9b74-1744b2ffd8fc`, both
`SUCCESS` at the exact baseline revision. The project is `Aevani`, ID
`6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`, production environment
`b7cfa813-8a5c-4fcd-80f2-cab736d840a7`. Public probes describe that deployed
baseline, not the local candidate. No deployment or production mutation was made.

## Verification

The final closure sweep passed against the complete candidate. The independent
[review](independent-reader-review-20260911.md) binds the reviewed runtime files
to their hashes. The [validation manifest](reader-validation-20260911.json)
records commands, stage outcomes, candidate file hashes and losslessly compressed
logs, including the earlier failures.

| Final gate | Observed result |
| --- | --- |
| `npm run check:data-boundary` | Passed: 12 URL rules, restricted imports and two fabrication rules. |
| `npm run type-check` | Passed. |
| `npm run lint` | Passed: zero errors, 542 warnings (the same count as the earlier sweep). |
| `npm run test:changed -- --base fa202230958fb55521963e886eb031be5fc266c4` | Full fallback: 145 files passed, two skipped; 2,226 tests passed, 13 skipped; 203.02 seconds. The inherited unclassified `.omc/ultrapilot-state.json` caused full selection. |
| Python `scripts/check.py --changed --base fa202230958fb55521963e886eb031be5fc266c4` | Full format, lint, mypy and pytest passed. Pytest ran for 273.36 seconds; the checker does not retain the successful test-count line. `agent/tools.py` caused full selection. |

Python used the existing development virtual environment with `uv run --no-sync`,
current service cwd and `PYTHONPATH=src`. The four database-test environment
variables were removed for this local run, as prescribed by `docs/testing.md`.
No database integration pass or Python release-quality receipt is claimed:
the selector explicitly marks this changed-mode full fallback receipt-ineligible.
No build or deployment was executed. Earlier recorded frontend skips and Python
optional integration skips are not converted into passing integration evidence.

The first integrated run passed boundary/type/lint checks but found three frontend
test failures and two Python reader-test failures. The combined correction batch
fixed day-bound fixture expectations, the malformed MTBS fault fixture, deterministic
failed-refresh waiter coordination, and the weighted-mean oracle's machine-precision
comparison. The mean tolerance is eight ULPs with zero relative tolerance, limited
to the aggregate mean; counts and the other values remain exact. Independent review
then identified and closed the carried-day, exact served-day, governed-absence and
neighbour/feature-semantics issues above before the final integrated run. Initial
failure evidence is retained alongside the final logs rather than rewritten.

The next integrated run passed all frontend checks (2,225 tests passed, 13 skipped)
and Python format/type checks. Full Python lint found one added return above its
limit; pytest reported 5,995 passed, 149 skipped, one failed and one xfailed due
to an outdated wording assertion. Both failures were corrected together: a pure admission helper preserves the
six-return function bound without suppression, and source-ceiling wording retains
the partition-versus-carried-map distinction with substantive assertions. Final
review also found the valid empty/truncated fire case above. All of these changes
preceded the closure sweep; there were no per-fix test runs.

## Remaining acceptance boundaries

The whole catalogue cannot currently satisfy the literal zero-LIST gate: static
lookup lanes retain census reads. Time-bearing availability lanes have a distinct
bounded pointer/generation/rollup path. A controlled production cold/warm object
operation trace is still needed; public request timings and reported authority do
not measure bucket operations. Current rollup/cache operation budgets must replace
the obsolete assumption that every request always downloads one index per lane.

Browser request-to-paint, selected-versus-painted-day, pan/scrub/rung transitions,
and downstream cancellation traces remain production acceptance evidence. Source
publication, static catalogue migration, generic Python agent support retirement,
and renderer defects discovered by these checks belong to their owning tracks.

The Next.js helper samples the nearest **globally published** day on each side
inside the described 180-day bound, then the nearest spatial row in the requested
viewport. It does not scan other days after an empty candidate and does not prove
the nearest local observation across all history. Before/after evidence never
substitutes for the selected-day answer. Unsupported products and absent authority
are explicit refusals without row reads. Python feature tools still withhold
burn-severity, evacuation-zones, fire-perimeters, soil-survey, watersheds and
vegetation until their shared map selection semantics have a supported reader.

The [production approval packet](production-approval-packet-20260911.md) defines
the precise remaining actions and required receipts. It includes the omitted
product/variant/rung/historical-positive cases, the MTBS 746-versus-747 row
conservation question and detail refusals, and the observed watersheds transient.
These are open investigations, not established data-loss diagnoses.

## Deployment and rollback boundary

There is no deployment authorization in this task. The owner must review the final
candidate commit, checks, observed route failures and cross-lane dependencies before
authorizing a release. The existing single release path remains merge/push to main,
Railway image checks, migration readiness and healthcheck; do not substitute a local
upload or data/scheduler mutation.

The September 2 verdict's broad `2b4cfef..HEAD` revert is historical and must not be
used: it would restore retired PostgreSQL readers and cross unrelated later work.
Rollback for this candidate means reverting only reader commit
`d56b508fb3afe8adb85969ce1553cd739daf779f` through the
normal release path, or selecting an exact schema-compatible deployment after
readiness review. The baseline frontend deployment above is the known serving
reference; its availability must be rechecked at execution time. Preserve all
Parquet data, publication pointers and retired environmental fallback boundaries.
