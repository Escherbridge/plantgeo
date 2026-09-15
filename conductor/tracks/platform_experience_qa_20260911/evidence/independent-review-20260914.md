---
type: review-receipt
track: platform_experience_qa_20260911
recorded_on: 2026-09-14
reviewer: /root/independent_verifier
review_base: 0f16e40dae3cce1d3b6d4ac00138254a968d974f
source_manifest_sha256: d1c4d79ef4c85691a82f51281b98cdab2177814c9017f15d8733856ebf7b728b
bounded_engineering_verdict: approved
platform_qa_verdict: RED
---

# Independent review of the September 14 correction batch

The bounded GBIF feedback, workspace lifecycle, unsupported feed telemetry and botanical
transport corrections are approved on the reviewed source and reconciled engineering evidence.
No unresolved material correctness or security finding was identified in that authored scope.
**Overall platform QA remains RED and unaccepted.** This receipt does not complete the runbook,
Q3, any browser case, any data admission gate, or production acceptance.

The reviewer authored no application or test changes and ran no duplicate test sweep. Source
inspection, independent content hashing, test/log inspection and ledger counting were performed
in this separate task context. Root authorized the reviewer to write only this review receipt.
Pre-existing environmental-track, Python availability, about-page and AgentGraph work remains
outside this source-approval scope; inclusion in the candidate manifest does not approve it.

## Candidate and evidence identity

The [final candidate](source-candidate-20260914-4.json) is an uncommitted working tree based on
`0f16e40dae3cce1d3b6d4ac00138254a968d974f`, committed base tree
`399ff61a9bf20023bcdfbc4ba8b8c3190050fff9`. The reviewer independently read all 1,665 source-manifest
entries and hashed their current bytes: zero mismatches. The manifest itself matches SHA-256
`d1c4d79ef4c85691a82f51281b98cdab2177814c9017f15d8733856ebf7b728b`.
This is content-bound local review, not immutable-commit or deployed-revision acceptance.

All 12 log hashes in the [check receipt](check-receipt-20260914.json) independently match the
captured files. Failed environment attempts and the nonzero combined Python runner remain
recorded. The successful follow-ups do not rewrite those historical exits.

| Evidence | Independently inspected result and scope |
| --- | --- |
| Frontend full test run | 190 files and 2,497 tests passed. |
| Final affected GBIF regression run | LayerManager: one file and 87 tests passed after removal of the redundant TypeScript discriminant comparison. This is a scoped follow-up, not another full suite. |
| TypeScript and data boundaries | Final type-check exited zero; client data-boundary, restricted-import and observation-fabrication checks passed. |
| ESLint | Exit zero with zero errors and 9,887 warnings. Historical worktree/vendor files contribute warnings; no zero-warning claim is supported. |
| Reviewed Python sweep | Changed-mode selector conservatively chose full pytest. Formatting, lint and pytest passed; pytest duration was 152.24 seconds. The combined runner exited one because mypy found an unused suppression comment. |
| Final Python typing | After removal of that comment, `uv run --no-sync mypy src scripts` exited zero: 436 source files clean. |
| Case inventory | Independently counted 219 unique PGQA rows: 178 `not_run`, 41 `blocked`, no duplicate IDs and no passed cases. |
| Botanical objects | Four locally captured object hashes match their receipt, including the completion marker and the 2,228-byte zero-row identifications object. This does not independently reconcile the source archive's complete field map. |

Candidate 1 to candidate 3 changes are exactly the four reviewed Python files; frontend bytes
remain unchanged. Candidate 3 to candidate 4 changes only the removed `HTTPClientError`
`type: ignore[import-untyped]` comment. The reviewer restored that comment in memory and
recomputed the file hash, obtaining the exact candidate 3 hash. Thus the final typing correction
changes no runtime behavior, and the earlier tested runtime remains applicable. This evidence
reconciliation does not claim that every check was rerun on candidate 4 or that the Python
changed-mode runner issued a full quality receipt.

## Review findings resolved before approval

1. **Hidden standalone AI panel bypassed workspace Escape confirmation.** The always-mounted
   standalone `RegionalIntelligencePanel` retained its document Escape listener while hidden.
   It could abort the AI stream even when the user canceled workspace discard. The final
   visibility guard disables that hidden listener; a regression mounts both real surfaces and
   checks canceled and confirmed Escape. Workspace handling also respects other dialogs.
2. **Late botanical manifest rereads escaped transport normalization.** The initial Python
   correction covered generation and pointer probes, but detail and aggregate readers re-read
   the manifest after scanning. A failure on that second read could still escape. The final
   narrow SDK transport catch covers that path; two regressions read real synthetic Parquet,
   succeed at the first manifest read, then fail the second and require an unavailable result
   without features, cells or private backend details. Candidate 2 remains intermediate evidence.
3. **Preserved proposal context had misleading relocation guidance.** The prior notice offered
   a clear-and-relocate flow that the controls did not perform, and the form caption could show
   new coordinates while retaining the old draft. The final form uses preserved draft
   coordinates and the notice describes the actual close, confirm discard and reselect flow.
4. **Weighted-mean reference assertion required bit-for-bit floating-point equality.** The
   captured failure was `4.720000000000001` versus `4.72`. The test now permits only a `1e-14`
   relative/absolute tolerance on `mean_value`; identifiers, counts, dates and min/max remain
   exact, and geodesic distance retains its separate tolerance. No production SQL was changed.

The remaining reviewed changes preserve their intended boundaries: GBIF empty feedback requires
a settled successful current detail slice and qualifies row truncation; mode switches preserve
mounted panes and drafts; initial location alone does not count as unfinished work; explicit
confirmed close clears both sessions; hidden draw maps resize when revealed; the feed removes
its unsupported benefit claim; botanical SDK transport failures return a constant unavailable
reason while unexpected programming exceptions propagate. Fake-backend tests remain local.

## Gates remaining open

- No available browser or tab was reported by runtime inventory. Canvas, mobile, accessibility,
  keyboard/touch, authenticated roles, live drawing, streaming and selected-day browser journeys
  have no acceptance evidence. All 219 UI cases remain unpassed across 31 registry layers plus
  four separately mounted land-context groups.
- The botanical zoom-8 wide-viewport refusal remains reproduced; its support/bbox policy needs
  resolution. Zero identifications reconcile only the inspected manifest and release receipt,
  not all source mapping or admission requirements.
- Land-context still has source/reader and geometry prerequisites. Forecast admission,
  proposal-to-AI consent entry, real-human publication and other declared owning-track gates
  remain open. Code presence and synthetic tests do not establish those outcomes.
- Database integration variables were removed for checks. Migration/bootstrap, relation census,
  build/deployment, production data conservation, cold/warm traces and schedule burn-in were
  not validated. Public responses did not expose a deployed revision or known upstream cache state.
- The documentation scan recorded 141 local links and two missing references in pre-existing
  environmental handoff work. These remain explicit defects; no whole-document clean verdict
  is issued. The local source candidate is still uncommitted.

Continuation should use the [session ledger](runbook-session-20260914.md) and
[defect ledger](defects.md). Any further source change requires candidate-impact reconciliation;
only requirements supported by new evidence may advance to accepted status.
