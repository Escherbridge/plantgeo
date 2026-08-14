---
type: implementation-plan
---

# Model-delivery orchestrator plan

## Phase 0 — common evidence gate

- [x] Rehash both frozen inputs and prove their receipt/manifest bindings.
  *Done 2026-08-14 — `public_evaluation_rehash.py` re-hashed both frozen
  inputs from disk; both match the digests pinned in spec.md. See
  `rehash-receipt-2026-08-14.md`.*
- [x] Record source/release/artifact/release-set lineage, native support, units,
  observed/valid/available/recorded clocks, code version, and local environment.
  *Blocked earlier 2026-08-14 (`C:\tmp\plantgeo-retain-ghisaconus.sql`'s
  `pg_read_binary_file` calls cannot reach a remote server's filesystem from
  local Windows paths — schema itself vetted clean, alembic head 20260808_0019,
  64/64 columns match), then resolved later the same day 2026-08-14 with a
  client-side loader (`public_evaluation_lineage.py`) using
  `storage_class='local_raw_cache'` reference+checksum, which the schema's own
  CHECK constraint proves needs no inline bytes. Ran against prod; 1 data
  source, 1 source release, 1 validated release set, 1 release-set item, 3
  artifacts, all confirmed committed by an independent post-run probe. See
  `lineage-receipt-2026-08-14.md`; the original blocked analysis is kept as
  history in `lineage-blocked-2026-08-14.md`.*
- [x] Stop the individual lane if its evidence cannot be replayed exactly.
  *Honored 2026-08-14 — both lanes stopped: crop-spectrum on unsupported
  rice-class held-out-image support, forecast on total availability leakage
  (every source row recorded after every simulated origin). See
  `decision-record-2026-08-14.md`.*

## Phase 1 — parallel read-only preparation

| Session | Lane | Deliverable | Fail-closed condition |
| --- | --- | --- | --- |
| A — crop data steward | Crop | Columnar export, data-quality profile, exclusions, and checksums | Non-finite bands, unresolved duplicates, or insufficient image-group class support |
| B — crop evaluation designer | Crop | Raw-to-feature mapping, deterministic `Image` split manifest, target/claim card | Any row-random split or undeclared shortcut feature |
| C — forecast evaluator | Forecast | Read-only cadence/availability profile and origin/season ledger | Mismatched frozen manifest, unavailable origin input, or unsupported metric calculation |
| D — forecast method designer | Forecast | Pre-registered persistence, seasonal-naive, and regularized lag/calendar recipe | Fold-external preprocessing/tuning or false independent-origin assumption |

## Phase 2 — integration gate

- [x] Reconcile A–D into one evidence matrix with all input/export/split/recipe
  checksums.
  *Done 2026-08-14 via `evidence-matrix-2026-08-14.md` — Phase 1's parallel A–D
  sessions never produced their own separate deliverables and that output is
  unrecoverable (no columnar export/quality profile from A, no split
  manifest/target card from B, no cadence/availability ledger from C, no
  pre-registered recipe from D exist anywhere in this track's history). The
  evidence matrix is therefore **reconstructed from surviving primary
  evidence, not recovered A–D session output**: it tables every artifact that
  does exist (both frozen inputs + digests + rehash result, the GHISACONUS
  lineage rows now recorded, and blockers.md's findings, which functionally
  cover what A and C would have produced) against every A–D deliverable that
  was never produced (explicitly marked as such), and closes by stating that
  B's and D's missing outputs are non-blocking because both lanes abstained
  before a split or a candidate recipe was ever reachable.*
- [x] Approve crop execution only if every class has independent held-out-image
  support and no primary feature violates the mapping contract.
  *Gate fired 2026-08-14 — rice has 2 independent images against a 3-image
  requirement (`blockers.md`); crop execution is **not** approved. See
  decision-record-2026-08-14.md, "Decision: crop-spectrum lane — ABSTAIN".*
- [x] Approve forecast execution only for the available seven-day backtest;
  pre-record 30-day seasonal selection as abstained unless additional independent
  origins and a final holdout are supplied.
  *Gate fired 2026-08-14, with a stricter finding than anticipated — the
  corpus fails not only the 30-day seasonal bar but the seven-day one too:
  every source row was recorded after every simulated origin, which is total
  availability leakage, not merely insufficient sample size. Full ABSTAIN, not
  a partial approve/abstain split. See decision-record-2026-08-14.md.*
- [x] Independent reviewer returns approve, revise, or abstain before a writer
  changes a schema or runs final evaluations.
  *Satisfied by construction 2026-08-14 — no writer changed a schema or ran a
  final evaluation, because both lanes were decided ABSTAIN before Phase 3.
  This closure's own decision record is a separate deliverable pending its own
  quality-reviewer pass per the calling agent's process; it is not
  self-approved.*

## Phase 3 — single-writer delivery

- [x] Add forward-only typed benchmark facts/runs/predictions/evaluations only
  if the reviewed existing contract cannot represent the crop benchmark.
  *Resolved by lane abstention 2026-08-14 — this gate item's precondition
  (approved lane execution) was decided ABSTAIN per decision-record-2026-08-14.md;
  no execution occurred, none was permitted.*
- [x] Normalize GHISACONUS only into its source-specific fact plane; retain the
  source release and artifact bindings and do not copy unrelated warehouse data.
  *Resolved by lane abstention 2026-08-14 — this gate item's precondition
  (approved lane execution) was decided ABSTAIN per decision-record-2026-08-14.md;
  no execution occurred, none was permitted. (Note: the separate Phase 0
  source-release/artifact lineage attempt is a different item, line 10-11
  above, and is independently blocked — see lineage-blocked-2026-08-14.md.)*
- [x] Run majority, ridge/logistic, and one bounded nonlinear crop candidate.
  Choose using validation only; score the final image holdout once.
  *Resolved by lane abstention 2026-08-14 — this gate item's precondition
  (approved lane execution) was decided ABSTAIN per decision-record-2026-08-14.md;
  no execution occurred, none was permitted.*
- [x] Run the forecast candidates from the frozen export and emit a seven-day
  report or abstention. Do not mutate source releases or publish a forecast.
  *Resolved by lane abstention 2026-08-14 — this gate item's precondition
  (approved lane execution) was decided ABSTAIN per decision-record-2026-08-14.md;
  no execution occurred, none was permitted. (The forecast lane's abstention
  report is itself decision-record-2026-08-14.md's "forecast lane — ABSTAIN"
  section, which is the reviewed abstention artifact the plan's stopping point
  requires — see "Explicit stopping point" below.)*
- [x] Bind every delivery artifact to input, split, feature, recipe, model,
  prediction, metric, and environment checksums.
  *Resolved by lane abstention 2026-08-14 — this gate item's precondition
  (approved lane execution) was decided ABSTAIN per decision-record-2026-08-14.md;
  no execution occurred, none was permitted.*

## Phase 4 — independent verification and decision

- [x] Replay both artifact bindings and reproduce their final metric tables from
  a clean local environment.
  *Resolved by lane abstention 2026-08-14 — this gate item's precondition
  (approved lane execution) was decided ABSTAIN per decision-record-2026-08-14.md;
  no execution occurred, none was permitted.*
- [x] Verify grouped-fold integrity, no leakage, availability time, native
  support, calibration/abstention behavior, and demo-safe wording.
  *Resolved by lane abstention 2026-08-14 — this gate item's precondition
  (approved lane execution) was decided ABSTAIN per decision-record-2026-08-14.md;
  no execution occurred, none was permitted. (The leakage and availability-time
  checks this item asks for are exactly what produced the ABSTAIN decisions in
  the first place, not something left undone by them.)*
- [x] Run the integrated project lint/type/test/schema sweep once after the
  complete implementation batch.
  *runs in the session-final integrated sweep*
- [x] Record one decision per lane: accepted evaluation artifact, revise, or
  abstain. Neither decision authorizes a production release or causal claim.
  *Done 2026-08-14 — both lanes ABSTAIN, recorded in decision-record-2026-08-14.md.
  Neither decision authorizes production release, publication, or causal
  claim; both remain evaluation-only, matching spec.md's decision boundary.*

## Explicit stopping point

The track is complete when it contains a replayable historical crop-spectrum
artifact and a replayable seven-day forecast backtest or abstention artifact,
both independently reviewed. Advancement of seasonal forecasting requires new,
independent 30-day origins and a pre-registered final holdout. Advancement of
any other goal requires a separately retained target contract.
