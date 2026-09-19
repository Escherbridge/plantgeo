---
type: source-admission-owner-decision
status: decided
decided_on: 2026-09-19
supersedes_scope_of: evidence/owner-risk-decision-20260913.md
---

# Owner admission decision: UBC vascular v16.43 is ADMITTED

**Decision (project owner, 2026-09-19):** *"Record as admitted now."*

UBC vascular specimens release **v16.43** (`pnw:UBC:vascular`), published to production
Parquet as generation
`956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4`, is **admitted** to the
PlantGeo botanical-occurrences serving set.

This is an explicit owner call, recorded here so it is auditable, following the same
convention as [owner-risk-decision-20260913.md](owner-risk-decision-20260913.md). It is
**not** an inference drawn by a research or implementation pass from the earlier
acquisition decision.

## What this decision changes

[admission-decisions.json](admission-decisions.json)'s own
`admission_reconciliation_note` (as written 2026-09-13) set out exactly two ways the
serving-ahead-of-admission contradiction could be resolved, and left the choice to the
owner:

> (a) extend authorization to serve ahead of post-capture gate closure, recorded as new
> evidence with `owner_decision` re-pointed at it, or (b) require those two gates to close
> before continuing to serve.

The owner has taken **option (a)**. This file is that new evidence; `owner_decision` in the
ledger is re-pointed here, and the UBC v16.43 entry moves from `serving_but_not_admitted`
into `admitted_releases`.

Consequently, the "Scope of this decision" section of
[owner-risk-decision-20260913.md](owner-risk-decision-20260913.md) (which states that the
post-capture gates "are unchanged and still required before any occurrence release is
admitted for serving" and that "`admitted_releases` stays empty until those pass") is
**superseded for UBC v16.43 only**, by this later owner decision. That earlier file remains
correct and binding for everything else it decided — the coordinate-withholding position,
the custody/quarantine/retention proposal, the permission-manifest authority, and the
one-transfer-at-a-time budget that defers WTU.

## What admission rests on

Each item below is a closed gate with the artifact that closed it:

1. **Permission.** CC0 1.0 stated in UBC's standalone release-bound EML, package id
   `07fd0d79-4883-435f-bba1-58fef110cd13/v16.43`, sha256
   `e73735e4eafcb1a233514948f72631ceced3dd22e30445d45f34e593f7293654` —
   [ubc-permission-manifest.json](ubc-permission-manifest.json), `permission_verdict:
   granted`. Publisher/rightsholder is the University of British Columbia Herbarium (UBC),
   dataset DOI `10.5886/rtt57cc9`, distributed via the Canadensys institutional IPT
   ([admission-packet.md](admission-packet.md) lines 96-97).
2. **Pre-acquisition gates** (institutional coordinate/withholding policy applicability;
   reviewed custody and archive-control preflight) — cleared by
   [owner-risk-decision-20260913.md](owner-risk-decision-20260913.md).
3. **Archive terms agreement.** The in-archive `eml.xml` sha256 matches the standalone EML
   verified in prior research — [ubc-inspection-receipt-20260913.json](ubc-inspection-receipt-20260913.json),
   `eml_sha256_matches_prior_research: true`.
4. **Archive/member safety and hashes.** `inspection.outcome: release_accepted` with an
   empty `reasons` array; three members (`meta.xml`, `eml.xml`, `occurrence.txt`),
   161,964,724 decompressed bytes; archive sha256
   `277a46aea3c25c0a9315cc6e7e5801a84a2892aac33cff8286317b69527ce847`, 31,110,199 bytes —
   [ubc-inspection-receipt-20260913.json](ubc-inspection-receipt-20260913.json).
5. **The admitted bytes are the served bytes.** Generation
   `956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4` is `_COMPLETE`, its
   manifest reads back at sha256
   `6fecc4a911d4ee5b357cc17fd56d29e4898d13e6a553773e23275e14ae704110`, and since 2026-09-19
   it is named by the checksum-bound `availability/_LATEST.json` pointer
   (`pointer_kind: latest_v1`), verified on the wire at both the parquet-api `/current`
   endpoint and the `plantgeo.aevani.com` proxy —
   [pointer-advance-20260919.md](pointer-advance-20260919.md).

## What admission does NOT rest on — still open

Admission here means **cleared to serve by owner decision**. It does **not** mean fully
verified. Two post-capture gates named in
[owner-risk-decision-20260913.md](owner-risk-decision-20260913.md) and
[ubc-inspection-receipt-20260913.json](ubc-inspection-receipt-20260913.json)
(`remaining_post_capture_gates`) are **still open on the admitted release**:

- **Field-map reconciliation** — a `normalize.py` field map reconciled against the raw
  `occurrence.txt` row count. The publisher advertises 192,948 records and the published
  manifest counts `raw_occurrences: 192948`
  ([pointer-advance-20260919.md](pointer-advance-20260919.md) §1), but no pass has
  reconciled the field map and complete population against the raw member itself.
- **Two-release native-ID stability comparison** — v16.42 (advertised 192,783 records,
  published 2026-07-29) has `acquired: false` in
  [admission-decisions.json](admission-decisions.json), so `identity_stability` remains
  `unmeasured` and no v16.42-vs-v16.43 native-identifier comparison has been run.

These are recorded on the admitted-release entry itself
(`open_verification_at_admission`), so the ledger cannot be read as claiming a verification
that has not happened. Closing them does not require a further admission decision; it
updates the entry's status in place.

## Out of scope

**WTU is untouched.** `pnw:WTU:vascular` remains pre-acquisition-cleared and deferred behind
UBC under the one-transfer-at-a-time budget from
[owner-risk-decision-20260913.md](owner-risk-decision-20260913.md). This decision admits
one release of one collection and says nothing about WTU.

The browser-side provisional label
(`src/lib/environmental/botanical-governance-status.ts`, `PROVISIONAL_BOTANICAL_NOTICE`) is
a hand-maintained mirror, not a reader of this ledger. It is deliberately **left in place**
for `pnw:UBC:vascular` by this pass: its notice text names precisely the two verifications
that are still open, and dropping a user-facing consent/quality caveat is an owner call in
its own right, not a consequence of this one. Removing it is an open follow-up.
