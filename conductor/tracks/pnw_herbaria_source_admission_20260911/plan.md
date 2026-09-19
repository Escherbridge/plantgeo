---
type: track-plan
track: pnw_herbaria_source_admission_20260911
status: active
---

# Plan

The September 11 metadata pass is complete; no specimen archive was downloaded.
The owner subsequently authorized a bounded data-only acquisition after the
pre-acquisition source gates pass. Those gates remain open. The
[packet](evidence/admission-packet.md) records the measured metadata, unmeasured
archive obligations and [handoff](evidence/requests-and-handoff.md).

The track is active under `b9b7bf4`; the two collection admissions are blocked.
The authorized future stage may inspect, hash and inventory permitted archives
in quarantine and prepare an ingestion handoff after the collection rights,
withholding and archive-control gates pass. No acquisition occurs during this
baseline synchronization. The existing two-archive ceiling is unchanged.
See [baseline synchronization](evidence/baseline-synchronization.md) for current
track relationships and the verification receipt for this merge.

## A0 — freeze the candidate set

- [x] Re-open the current portal download inventory and WTU/UBC provider pages.
- [x] Record exact collection keys, access URLs, advertised counts, dates and
  terms; keep images and non-admitted collections out of scope.
- [x] Define the intended PlantGeo use and distribution surface against which
  collection terms will be judged.

## A1 — bounded metadata and archive inventory

- [ ] Fetch EML, field mappings and response metadata under the documented caps.
  Partial: UBC v16.43 standalone EML and three HTML metadata receipts captured;
  a September 12 refresh captured seven metadata responses and confirmed the UBC
  EML hash. A second September 12 pass (redirect-refusing, GBIF-registry-
  corroborated) captured **release-bound WTU EML for two complete releases**
  (v1.0 2025-11-06, v1.1 2026-03-12, GBIF UUID `8310f570-f762-11e1-a439-00145eb45e9a`)
  from the actual distribution host `ipt.pnwherbaria.org`, and found the
  release-bound licence is CC-BY 4.0, not the CC0 the portal page implied — see
  [evidence/wtu-gbif-identity-20260912.md](evidence/wtu-gbif-identity-20260912.md).
  Both archive field maps remain unmeasured because archive transfer is gated.
- [ ] If terms permit, capture each complete archive once into quarantine and
  verify the ZIP/member safety, hashes, counts and schema.
- [ ] Reconcile accepted, out-of-envelope, excluded-by-rights, nonspatial,
  duplicate-native-record and quarantined counts.

## A2 — release and identity proof

- [ ] Compare at least two equivalent complete releases before treating a native
  identifier or source watermark as stable.
- [ ] Specify correction, withdrawal and source-shrink behavior without treating
  a partial export as a deletion feed.
- [ ] Issue one admission/refusal packet per collection and obtain independent
  governance review.
- [x] Keep the author and independent governance/archive-safety reviewer in
  separate task contexts; the reviewer owns the final admission verdict.

The downstream Parquet and experience tracks remain planned until A2 admits at
least one exact collection release.

## September 11 bounded evidence outcome

- [x] Produce separate WTU and UBC blocked admission decisions, source register,
  custody requirements, quarantine/reconciliation rules and unsent requests.
- [x] Identify UBC institutional v16.43 and v16.42 as comparison candidates;
  do not confuse the newer institutional release with the older portal copy.
- [x] Record the parent-owned hybrid species-profile boundary and historical
  agri.species evidence without editing the profile or occurrence implementation.
- [x] Independent reviewer owns the final verdict in evidence/independent-review.md:
  both admissions blocked; bounded metadata packet accepted.
- [x] One final documentation/JSON/local-link/hash/whitespace verification sweep; see evidence/verification.json.

The final report supplies the bounded commit and remaining gates to parent and
integration tasks; no merge or push is authorized by this packet.

Admission remains blocked on WTU coordinate-policy applicability and release-bound
terms reconciliation, UBC institutional coordinate-policy applicability, and both
collections' archive-custody, ZIP/member-safety, schema and native-ID stability
receipts. The occurrence-lane and species-profile implementation from the former
`claude/herbaria-botanical-lanes` worktree merged to main 2026-09-13 (PR #6); it
does not close these acquisition gates and admits no occurrence release.

## September 13 gate-narrowing pass

- [x] Re-checked UBC's EML `<methodStep>` in full (empty) and searched the
  Canadensys network's own homepage/about page for any network-wide
  coordinate-withholding or sensitive-locality statement; found none. See
  [evidence/coordinate-policy-search-exhausted-20260913.md](evidence/coordinate-policy-search-exhausted-20260913.md).
  This closes the *research* side of the coordinate-policy gate: it cannot be
  resolved by more public metadata, only by an institutional response or an
  explicit owner risk decision.
- [x] Corrected the WTU outreach draft's CC0 premise (it is CC-BY 4.0 in the
  release-bound EML) and re-scoped it to the real distribution host
  (`ipt.pnwherbaria.org`, not the `pnwherbaria.org/data` portal). See
  [evidence/wtu-gbif-identity-correction-20260913.md](evidence/wtu-gbif-identity-correction-20260913.md).
  Still unsent; sending remains an operator decision.
- [x] Wrote a custody-and-archive-control preflight proposal grounding the
  "reviewed custody" pre-acquisition gate in the controls already
  implemented and tested in `pipeline/direct/botanical_occurrences/`
  (allowlist, HTTPS-only, redirect refusal, explicit per-URL permission
  gate, byte/attempt/time caps, exhaustive archive-safety checks, no
  extraction to disk, no automatic scheduling). Narrows the gate to five
  named operator decisions (custody owner, quarantine location, retention
  duration/trigger, withdrawal record, and the permission manifest's actual
  authority) rather than an open question. See
  [evidence/custody-and-archive-control-preflight-proposal-20260913.md](evidence/custody-and-archive-control-preflight-proposal-20260913.md).
  Does not appoint an owner or approve a retention policy; those remain
  operator calls.

Admission remains blocked. A two-release UBC pilot consumes the two-archive
budget and must defer WTU rather than silently widening acquisition.

## September 13 owner risk decision and first acquisition

- [x] Owner explicitly accepted the coordinate-policy risk (per-record
  `informationWithheld`/`dataGeneralizations` is sufficient; no
  institutional reply required) and adopted the custody proposal as
  written, naming the five previously-open decisions. See
  [evidence/owner-risk-decision-20260913.md](evidence/owner-risk-decision-20260913.md).
  This clears the **pre-acquisition** gates for both WTU and UBC; the
  **post-capture** gates (archive safety, field-map reconciliation,
  two-release identity comparison) are unchanged.
- [x] Per the one-transfer-at-a-time budget, acquired UBC v16.43 first
  (simpler CC0 terms), using
  [evidence/ubc-permission-manifest.json](evidence/ubc-permission-manifest.json)
  as the exact-URL permission grant. Transfer receipt: 31,110,199 bytes,
  sha256 `277a46ae...ce847`.
- [x] Ran `inspect_archive` against the quarantined bytes: `outcome:
  release_accepted`, zero reasons, `eml.xml` sha256 matches the EML already
  verified in prior research. See
  [evidence/ubc-inspection-receipt-20260913.json](evidence/ubc-inspection-receipt-20260913.json).
- [ ] WTU acquisition deferred to a subsequent pass (two-archive budget is
  one transfer at a time).
- [ ] Field-map reconciliation over `occurrence.txt` and the v16.42/v16.43
  native-ID comparison remain open before any occurrence release is
  admitted. `admitted_releases` stays empty.

## Ledger note — September 13, two ledgers tracking separate concerns

The governance JSON `evidence/admission-decisions.json` tracks **owner-signed admissions** (which
releases are cleared to serve per the governance gates in `evidence/owner-risk-decision-20260913.md`),
while the Parquet `current.json` pointer and generation timestamp `956c0be7...` track **published data**.
These are two separate ledgers for a reason:

- `admitted_releases` in the governance file stays empty because the **post-capture gates** (field-map
  reconciliation, v16.42/v16.43 native-ID stability comparison) remain open, per line 584-590 of
  RUNBOOK (September 13 update, Critical finding). The `serving_but_not_admitted` array records the
  honest state — UBC v16.43 is live in production Parquet per line 610 — without claiming it has passed
  gates it has not yet passed.
- `admission_reconciliation_note` in the governance file (lines 585-591 of RUNBOOK) documents this
  discrepancy and leaves the decision to extend authorization or require the gates to close first
  **explicitly to the owner** rather than guessing.
- Filling `admitted_releases` with `956c0be7...` is an owner signature, not an engineering change, and
  should not happen automatically at publication time. It is an explicit, owner-authorized flip once the
  deferred gates close.

See RUNBOOK lines 574-670 (botanical handoff) and the evidence folder for the full context.

## Ledger note — September 19 update: the owner signed, gates still open

The section above is kept as written because its reasoning held on September 13. One thing in it
has since changed, and one has not.

**Changed.** The owner signed. On 2026-09-19 the project owner decided "Record as admitted now"
([evidence/owner-admission-decision-20260919.md](evidence/owner-admission-decision-20260919.md)),
taking option (a) of the reconciliation note verbatim: extend authorization to serve over the open
post-capture gates, record it as new evidence, re-point `owner_decision` at it.
`admitted_releases` now holds UBC v16.43 / generation
`956c0be71910469005fb494d92aac035223be49d1f5c895c0b1206a716b16ac4` and
`serving_but_not_admitted` is empty. The two ledgers — governance admission, and the production
`availability/_LATEST.json` pointer advanced on the same day
([evidence/pointer-advance-20260919.md](evidence/pointer-advance-20260919.md)) — now name the same
generation.

**Unchanged.** The flip was still "an owner signature, not an engineering change": no gate result
changed and no verification was run to produce it. Field-map reconciliation over the real
`occurrence.txt` and the v16.42-vs-v16.43 native-ID comparison are **still open**, and are carried
explicitly on the admitted entry under `open_verification_at_admission` so that "admitted" can
never be misread as "fully verified". The one sentence above that is now superseded is "once the
deferred gates close" — the owner admitted ahead of them, deliberately, and recorded why.
