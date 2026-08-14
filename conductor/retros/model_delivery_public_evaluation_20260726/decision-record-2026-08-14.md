---
type: decision-record
---

# Model-delivery public evaluation: lane decisions — 2026-08-14

This is the Phase 4 decision record the plan requires ("Record one decision
per lane: accepted evaluation artifact, revise, or abstain. Neither decision
authorizes a production release or causal claim."). Both lanes ABSTAIN.

A verification pass on 2026-08-14 established that stub modules
(`method/ml/crop_spectrum_evaluation.py`, `forecast_backtest_evaluation.py`,
`tests/test_sprint_integration.py`) had fabricated the metrics this track was
supposed to produce. They have been **deleted**; nothing in this record, or in
this track's history, relies on them. The genuine evidence base is
`conductor/review-packet-20260726/blockers.md`, produced independently of
those stubs, and this record's decisions rest entirely on it.

## Decision: crop-spectrum lane — ABSTAIN

**Cited evidence:** `blockers.md`, "Crop-spectrum benchmark: insufficient
independent rice groups." The GHISACONUS CSV has 6,988 signatures across 99
source images; `Crop` is the primary target with `Image` as the mandatory
leakage-control group (spec.md, "Crop lane"). Rice appears in only **2**
independent images. The plan's own gate (spec.md: "A class that cannot occupy
each required independent partition is unsupported; it is never silently
merged or randomly split by row") and a train/validation/final split both
need at least 3 independent images per class. 2 < 3. Nothing about a stub,
an implementation shortcut, or additional engineering effort changes an image
count; this is a data-availability fact, not a code defect.

**What is intact:** source hashing (see rehash receipt below), row/band
completeness, and the historical-crop target itself are all fine. This is not
a "the pipeline is broken" abstention — it is "the reviewed target has an
unsupported class under the reviewed leakage-control rule."

**Decision: ABSTAIN.** Per plan Phase 2 ("Approve crop execution only if every
class has independent held-out-image support"), crop execution (Phase 3
candidate ladder: majority / ridge-logistic / bounded nonlinear) is not
approved to run. See `blockers.md`'s own next-option table (acquire another
rice image; define a four-class benchmark; build an evidence explorer first)
for what would need to change before this lane could re-open — none of those
were exercised in this session, and none was in scope for it.

## Decision: forecast lane — ABSTAIN

**Cited evidence:** `blockers.md`, "Weather backtest: unavailable at
simulated forecast origins." The frozen export (1,462 daily NASA POWER
observations, 98 scored outcomes from 14 seven-day origins) is byte-for-byte
valid, but **every source row was recorded on 2026-07-21, after every one of
the 14 simulated origins.** That is total availability leakage: every
"forecast" origin in the corpus would score against values that were not
knowable at that origin's simulated issue time. Spec.md's forecast-lane
section requires "identical expanding origins" evaluated with only data
available as-of each origin, and the plan's Phase 0 gate ("Stop the
individual lane if its evidence cannot be replayed exactly") is exactly this
condition.

**Additional scope limit, also from `blockers.md`:** this is one Denver NASA
POWER point at 55,660 m support — neither field-level nor regional coverage —
and 14 seven-day origins cannot support a 30-day seasonal-selection claim
regardless of the availability issue. Spec.md's forecast-lane section already
pre-declares 30-day seasonal selection out of scope on this corpus ("The
current corpus is explicitly insufficient for a 30-day seasonal-selection
claim").

**Decision: ABSTAIN.** Per plan Phase 2 ("Approve forecast execution only for
the available seven-day backtest; pre-record 30-day seasonal selection as
abstained unless additional independent origins and a final holdout are
supplied"), even the seven-day backtest cannot run honestly against this
corpus, because the corpus itself violates the as-of/availability contract
this whole track is built to enforce ("Time-honest or it does not ship").
Running persistence/seasonal-naive/ridge against it would be a retrospective
curve fit wearing a forecast evaluation's clothes — precisely what `blockers.md`
warns against.

## Evidence matrix (compact form, per plan Phase 2's reconciliation ask)

| Item | Crop lane | Forecast lane |
| --- | --- | --- |
| Frozen input | Kaggle GHISACONUS v1 CSV, 6,988 rows / 131 bands / 99 images | NASA POWER frozen export, `manifest.json` |
| Bound digest (spec.md) | `e2f5a21b24fac00e930520ba959ab54cc8a3f8c56368f8e0a1868bbf3e3377d5` | `1bb6a6a707b432f2036edba86a426a32c1c04304b350af4caaec14a48cb20d09` |
| Rehash result 2026-08-14 | **match** (11,540,638 bytes) | **match** (2,394 bytes) — see `rehash-receipt-2026-08-14.md` |
| Lineage recorded in prod | **Update, later 2026-08-14:** recorded via a client-side loader (`public_evaluation_lineage.py`), `storage_class='local_raw_cache'`, no bytes crossed the wire. 1 data_source, 1 source_release, 1 release_set (validated), 1 release_set_item, 3 artifacts — see `lineage-receipt-2026-08-14.md`. Originally blocked earlier the same day; history kept in `lineage-blocked-2026-08-14.md`. | N/A — spec.md names no equivalent forecast-lane retention write in this track |
| Class/group support (crop) or availability-clock (forecast) | Rice: 2 independent images, 3 required | Every row recorded 2026-07-21, after all 14 simulated origins |
| Gate that fires | Phase 2 "Approve crop execution only if every class has independent held-out-image support" — **not met** | Phase 0 "Stop the individual lane if its evidence cannot be replayed exactly" — **not met** |
| Phase 3 (candidate models) | Never executes | Never executes |
| Phase 4 (replay/verification) | Never executes — nothing to replay | Never executes — nothing to replay |
| Decision | **ABSTAIN** | **ABSTAIN** |

## Why every downstream execution item never fires

Phase 3 ("single-writer delivery") and Phase 4 ("independent verification and
decision") are conditioned on Phase 2's approval gate. Phase 2 approved
neither lane. There is therefore no typed benchmark plane to add (Phase 3 line
1 is itself conditional: "only if the reviewed existing contract cannot
represent the crop benchmark," and no crop execution was approved to need
representing), no candidate model run, no forecast candidate run, no
artifact-binding checksum set, and nothing to replay or reproduce in Phase 4.
This is not skipped work — it is work whose precondition (an approved lane)
was never satisfied, and running it anyway would itself be the leakage/support
violation this track exists to prevent.

## Disposition of the fabricated stubs

`method/ml/crop_spectrum_evaluation.py`, `forecast_backtest_evaluation.py`,
and `tests/test_sprint_integration.py` were deleted 2026-08-14, prior to this
session, by a separate verification pass. They previously reported metrics
for both lanes without the underlying execution ever having run, and without
the support/availability failures above ever being surfaced. Nothing in this
decision record, `rehash-receipt-2026-08-14.md`, or `lineage-blocked-2026-08-14.md`
depends on, references, or recreates them.
