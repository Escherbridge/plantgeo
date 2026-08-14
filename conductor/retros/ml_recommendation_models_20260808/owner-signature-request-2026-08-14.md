---
type: signature-request
---

# Owner signature request: literature label release 2026-08-14

**What is being asked for.** Your countersignature (or rejection) of one label
release. Nothing in the service can grant it: `agri.expert_label.review_state =
'approved'` requires an `owner_signature_reference`, enforced by both a CHECK
constraint and `agri.guard_expert_label_review_change`, and no code path in this
lane supplies one. Until you sign, every artifact, receipt and served response
carries `label_review_tier = 'agent_reviewed_pending_owner_signature'`.

## The release

| Field | Value |
| --- | --- |
| `release_key` | `literature-labels:2026-08-14:96cfec27be6fe1c7` |
| Harvest document | `conductor/tracks/ml_recommendation_models_20260808/label-harvest-2026-08-14.json` |
| Harvest document sha256 | `96cfec27be6fe1c7…` (full digest stored in `expert_label_release.harvest_document_checksum`) |
| Workflow | literature-label-harvest, 12 agents: 6 harvest slices + 6 adversarial citation verifiers, refute-by-default |
| Harvested | 2026-08-14 |
| Review tier | `agent_reviewed_pending_owner_signature` |

## Counts

| Review state | Labels | Meaning |
| --- | ---: | --- |
| `agent_reviewed` | **28** | Citation verified against the cited source; trainable as evidence, pending your signature. |
| `rejected` | **2** | The adversarial verifier refuted the claim against its own source. Stored, not deleted, so the catch is on the record. |
| `draft` | 0 | Every loaded label was moved on in the same transaction. |
| `approved` | **0** | Unreachable without your signature. |
| Not loadable | **2** | Kept by the verifiers but stating **no condition envelope**, so no governed stream can be intersected with them. Reported with reasons by the loader; not stored. |

29 distinct cited works, all DOI-identified, all resolving per the verifiers.

### By slice, kind, state and outcome

| Slice | Kind | State | Outcome | Labels | Sources | Subjects |
| --- | --- | --- | --- | ---: | ---: | ---: |
| species-cover-crops | species_fit | agent_reviewed | fit | 2 | 2 | 2 |
| species-cover-crops | species_fit | agent_reviewed | marginal | 1 | 1 | 1 |
| species-cover-crops | species_fit | agent_reviewed | unfit | 1 | 1 | 1 |
| species-cover-crops | species_fit | **rejected** | fit | 1 | 1 | 1 |
| species-cover-crops | species_fit | **rejected** | marginal | 1 | 1 | 1 |
| species-cover-crops | strategy_outcome | agent_reviewed | ineffective | 1 | 1 | 1 |
| species-trees-shrubs | species_fit | agent_reviewed | marginal | 4 | 4 | 4 |
| species-trees-shrubs | species_fit | agent_reviewed | unfit | 2 | 2 | 2 |
| strategy-agroforestry | strategy_outcome | agent_reviewed | mixed | 3 | 3 | 2 |
| strategy-biochar-grazing | strategy_outcome | agent_reviewed | effective | 1 | 1 | 1 |
| strategy-biochar-grazing | strategy_outcome | agent_reviewed | ineffective | 1 | 1 | 1 |
| strategy-biochar-grazing | strategy_outcome | agent_reviewed | mixed | 3 | 3 | 2 |
| strategy-reforestation-fire | strategy_outcome | agent_reviewed | ineffective | 1 | 1 | 1 |
| strategy-reforestation-fire | strategy_outcome | agent_reviewed | mixed | 3 | 3 | 1 |
| strategy-water-harvesting | strategy_outcome | agent_reviewed | effective | 3 | 3 | 1 |
| strategy-water-harvesting | strategy_outcome | agent_reviewed | mixed | 2 | 2 | 1 |

Reproduce this table with
`SELECT * FROM agri.expert_label_release_summary('literature-labels:2026-08-14:96cfec27be6fe1c7')`.

### The two refuted labels

Both were refuted for the same class of reason — the DOI resolved and the paper
matched, but the verbatim source text did not support the extracted tuple:

- *Panicum miliaceum* (proso millet), `10.2134/agronj2005.0300`-class dryland
  forage claim: the abstract identifies a different species pairing than the
  extraction asserted.
- *Sorghum bicolor*, Holman/Obour/Assefa, Crop Science 2021: the abstract states
  a result the extraction overstated.

They are stored with `review_state = 'rejected'`, `citation_check_refuted = true`
and the verifier's full reason, and no query path can train on them.

## What signing changes, and what it does not

Signing moves labels to `approved` and lets a future training run record
`label_review_tier = 'owner_signed'`. It does **not** make any model output a
causal claim, and it does not authorize publication: the training receipt table
carries `CHECK (evaluation_only)` and `CHECK (NOT publication_authorized)`, so an
owner-signed release still produces evaluation-only artifacts.

## What I recommend you weigh before signing

1. **Confidence is thin.** Of 28 agent-reviewed labels, 16 are `low` confidence,
   11 `medium`, 1 `high` (the ordinals are the harvest's own; the numeric weights
   0.3/0.6/0.9 used as sample weights are a declared modelling choice recorded in
   every artifact, not a source value).
2. **One label per species.** All 10 species labels name distinct species, so no
   species has corroboration and per-species generalization is unmeasurable — see
   the decision record's Model A result.
3. **The verifiers refuted 2 of 32 and flagged 2 more as envelope-less.** That is
   a working adversarial pass, not a rubber stamp, which is the main reason to
   trust the remaining 28 as evidence.
4. **These are recommendation labels, not outcome labels.** They say what a
   source recommends under stated conditions. Signing does not assert that
   following them produces the stated outcome at Boise.

## To sign

Record a signature reference (a commit SHA, a signed note, or an issue URL) and
apply it to the release and its labels; the guard trigger will accept the
`agent_reviewed -> approved` transition only with one present. To reject, move
the labels to `rejected` instead — the same trigger accepts that transition and
refuses any edit to their content.
