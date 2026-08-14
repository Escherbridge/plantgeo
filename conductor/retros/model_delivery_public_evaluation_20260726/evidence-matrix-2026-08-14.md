---
type: evidence-receipt
---

# Reconciled evidence matrix — 2026-08-14

Plan Phase 2 asks to "Reconcile A–D into one evidence matrix with all
input/export/split/recipe checksums." Phase 1's four parallel sessions (A —
crop data steward, B — crop evaluation designer, C — forecast evaluator, D —
forecast method designer) never produced their own separate deliverables in
this track's history, and there is no way to retroactively produce the
session-shaped artifacts the plan describes (a columnar export from A, a
deterministic `Image` split manifest from B, a cadence/availability ledger
from C, a pre-registered recipe from D) without re-running work that the lane
abstentions below make moot.

What follows is **reconstructed from primary evidence that does exist** —
the frozen inputs, their rehashes, and `blockers.md`'s findings — not
recovered A–D session output. Every row below is marked with which of those
two it is.

## Evidence that exists

| # | Artifact | Source | Value | Status |
| --- | --- | --- | --- | --- |
| 1 | GHISACONUS CSV | `C:\tmp\plantgeo-kaggle-ghisaconus-v1\GHISACONUS_2008_001_speclib.csv` | 11,540,638 bytes, SHA-256 `e2f5a21b24fac00e930520ba959ab54cc8a3f8c56368f8e0a1868bbf3e3377d5` | **EXISTS** — pinned in spec.md, rehashed 2026-08-14 (match) |
| 2 | GHISACONUS distribution archive | `C:\tmp\plantgeo-kaggle-ghisaconus-v1.zip` | 5,225,446 bytes, SHA-256 `3bb701ab61eb2069c09ae7cc9cb66fbd0995165f127478af2b6afb9d406abac0` | **EXISTS** — pinned in the accepted intake receipt, rehashed 2026-08-14 (match) |
| 3 | GHISACONUS source metadata | `C:\tmp\plantgeo-kaggle-ghisaconus-metadata.json` | 8,877 bytes, SHA-256 `42480207bdeed7b40753637f4b24b07d44232ba4ed6f8455fabdecd4c1b6b220` | **EXISTS** — pinned in the accepted intake receipt, rehashed 2026-08-14 (match) |
| 4 | Frozen NASA POWER forecast export manifest | `C:\tmp\plantgeo-frozen-forecast-20260726\manifest.json` | 2,394 bytes, SHA-256 `1bb6a6a707b432f2036edba86a426a32c1c04304b350af4caaec14a48cb20d09` | **EXISTS** — pinned in spec.md, rehashed 2026-08-14 (match) |
| 5 | Crop-lane data-quality finding | `blockers.md`, "Crop-spectrum benchmark: insufficient independent rice groups" | 6,988 rows / 99 images / rice in 2 independent images against a 3-image requirement | **EXISTS** — this is the same substance Session A ("data-quality profile, exclusions") would have produced, recorded 2026-07-26 |
| 6 | Forecast-lane availability finding | `blockers.md`, "Weather backtest: unavailable at simulated forecast origins" | 1,462 daily observations / 98 scored outcomes / 14 origins, every source row recorded 2026-07-21 after every simulated origin | **EXISTS** — this is the same substance Session C ("cadence/availability profile and origin/season ledger") would have produced, recorded 2026-07-26 |
| 7 | GHISACONUS lineage rows | `agri.data_source` / `agri.source_release` / `agri.release_set` / `agri.release_set_item` / `agri.artifact` (prod) | See `lineage-receipt-2026-08-14.md` for row counts and keys | **EXISTS** as of 2026-08-14 — recorded via `public_evaluation_lineage.py` |
| 8 | Bound digest table | spec.md, "Immutable inputs" | Crop benchmark and forecast backtest rows, both re-verified | **EXISTS** — the two rows this whole track's Phase 0 gate is built on |

## Session deliverables that were never produced

| # | Deliverable | Owning session | Status | Why it does not block |
| --- | --- | --- | --- | --- |
| 9 | Raw-to-feature mapping and a deterministic `Image` train/validation/final split manifest | B — crop evaluation designer | **NEVER PRODUCED** | Moot: the crop lane cannot be split at all — rice has 2 independent images against the 3-image requirement a valid split needs, so no split manifest could be produced honestly regardless of B's absence. |
| 10 | Target/claim card for the crop candidate ladder | B — crop evaluation designer | **NEVER PRODUCED** | Moot: no candidate ladder runs; the crop lane abstains before model selection is reachable. |
| 11 | Pre-registered persistence/seasonal-naive/regularized lag-calendar recipe | D — forecast method designer | **NEVER PRODUCED** | Moot: no forecast candidate runs; the forecast lane abstains on total availability leakage before any recipe would be fit. |
| 12 | Fold-external preprocessing/tuning audit for the forecast recipe | D — forecast method designer | **NEVER PRODUCED** | Moot for the same reason as #11 — there is no recipe to audit. |
| 13 | A single reconciled A–D evidence matrix with input/export/split/recipe checksums, as a formal session artifact | Phase 2 reconciliation (this document's literal ask) | **NOT PRODUCED IN THAT FORM** | This document is the closest honest substitute: it reconciles what exists (rows 1–8) against what a full A–D pass would have added (rows 9–12), and states plainly that rows 9–12 are unrecoverable, not silently missing. |

## Closing note: why the missing rows are non-blocking

Rows 9–12 all sit downstream of a Phase 2 approval this track never granted.
`decision-record-2026-08-14.md` records both lanes ABSTAIN:

- **Crop lane** — rice's 2-independent-image support fails the plan's own
  3-image leakage-control minimum. A split manifest, a target/claim card, and
  a candidate ladder run all require a split that this class cannot honestly
  receive. Producing any of them now, retroactively, would not fix the
  underlying image-count shortfall; it would just be work performed after the
  fact on an already-abstained lane.
- **Forecast lane** — the frozen export's every source row postdates every
  simulated origin (total availability leakage). A pre-registered recipe and
  its fold-external-tuning audit both assume a scoreable corpus; this one
  cannot be scored honestly at all, so there is nothing for D's recipe to be
  fit against.

Because both lanes stopped at Phase 0/Phase 2's own gates, the missing B and
D deliverables were never on the critical path to this track's actual
stopping point (an accepted crop-spectrum artifact and an accepted or
abstained forecast artifact — plan.md, "Explicit stopping point"). The
abstentions themselves are the plan's valid terminal outcome; this matrix
documents that nothing further is missing that would need to be produced
before the track could close.
