---
type: evidence-receipt
slug: outcome-label-source-audit-2026-09-12
observed_at: "2026-09-12T00:00:00-06:00"
repository_commit: 0953de4bdfa0d95cae7ad40a3a92e3943f42fe65
repository_tree: 33c4d55f9f0edc25237719b45616c1a8eadb7d9d
status: blocked
---

# Intervention/control outcome-label source audit

## Verdict

**No intervention/control outcome-label source is admitted.** The only source
shape the retained causal benchmark may accept is a named, immutable
`intervention_outcome_evidence` release with a complete
`strategy_label_source_mapping_v1` mapping. The checked-in mapping worksheet is
intentionally all `null`, has no covariates and identifies no source or release.

This is a source-admission abstention. It is not a causal, feasibility, planting
or strategy recommendation. No label release, episode, benchmark, model,
training receipt or selection receipt was created during this audit.

## Scope and authority

The audit used the current repository root and retained, dated evidence only.
It reviewed the originating `Locate PlantGeo outcome labels` task
(`019f9cdc-db04-7601-be22-c3ef874a2e11`), the July 25 label audit, current
Conductor governance and botanical tracks, generated schema, migration
contracts, mapping/training code and dated production-census receipts.

It did **not** connect to PostgreSQL, `pgt`, Railway, production, object storage,
writers, schedulers or deployments. It did not read or infer a credential.
The current checkout contains no `.env`, `.env.local` or `.railway` link at the
root or under `services/agri-data-service`; only checked-in example/configuration
files are present. Therefore this receipt makes no claim about current Railway
row counts, schema parity or source population.

The retained [September 11 botanical census receipt](../../tracks/botanical_species_profile_lookup_20260911/evidence/railway-production-botanical-census-2026-09-11.md)
independently records the same authorization boundary: no operator-authorized
Railway reader configuration, authenticated linked target or read-only DSN was
available, so it made no connection attempt or production population claim.

The retained July audit is authoritative for what it inspected at that time,
not for current production. The August 14 decision record is authoritative for
its recorded expert-label counts and its production counts of zero
`strategy_label_episode` and zero `strategy_label_release` rows, but it did not
admit an intervention/control release and is not a current causal-plane census.

## Source disposition

| Candidate | Evidence actually admitted | Outcome-label decision |
| --- | --- | --- |
| Boise forecast actuals, residuals and iteration receipts | Forecast-error and availability-aware signal evidence | Rejected as intervention effects. They do not encode treatment assignment, eligible controls or matured pre/post intervention outcomes. |
| Boise/Hillside-to-Hollow Census, OSM and USFS WUI captures | Versioned boundary, classification, context and known-gap evidence | Rejected as labels. The July audit records no strategy rows, assignment, untreated risk set or pre/post outcome in these captures. |
| Community intervention submissions and moderated map features | Contributor-authored proposals and publication/lifecycle state | Not an outcome source. Proposal, review, publication or completion state does not establish an intervention effect or an eligible control episode. |
| August literature `expert_label` release | Expert recommendation labels with citation and review custody | Kept in its separate evaluation-only plane. The track explicitly forbids writing these rows into the `0013` treatment/control plane; the retained signature request records zero owner-approved labels and says signing would still not make them causal outcomes. |
| Botanical occurrence and profile sources | Planned specimen-occurrence, taxonomy, trait and establishment evidence | Not an outcome source. The current tracks keep documented occurrence, establishment compatibility and objective effect separate, and require a separately admitted species-objective-effect release before recommendation work. |
| Environmental signals, vegetation indices, fire, soil and weather streams | Observations, forecasts, contextual features and possible future outcome measurements | Not labels by themselves. A measurement stream becomes usable only when an admitted study release binds independent subjects, assignment, treatment/control risk set, windows, availability and custody. |

The existence of tables named `strategy_outcome_definition`,
`strategy_label_release` or `strategy_label_episode` is storage capability, not
source admission. Synthetic fixtures and schema tests are contract evidence and
must not be promoted as real episodes.

## Current schema and release boundary

The current generated schema retains the causal storage tables, checksum
functions and exact `strategy_labels_v1` export. Migration `20260803_0018`
removed the two `finalize_strategy_*` functions, their release/receipt change
guards and parent-state insert guards. Consequently the database no longer
contains the old blanket finalizer rejection for `effect_candidate`.

The remaining receipt CHECK only constrains `effect_candidate` to
`execution_mode = 'publishable'`; it does not authorize or safely finalize that
state. For the current release contract:

- `effect_candidate` remains disabled and prohibited;
- no direct SQL or replacement finalizer may be used to bypass the retired
  guards;
- `strategy_label_release`, `strategy_label_episode`,
  `strategy_selection_receipt` and `strategy_selection_candidate` remain
  write-closed for this task;
- literature recommendations, botanical evidence and forecast errors remain in
  their own planes; and
- no causal or planting recommendation may be emitted from this evidence.

## Minimum admissible handoff

Before a future normalization or benchmark task can begin, a source owner must
provide all of the following for independent review:

1. A stable source name, kind and locator; immutable release ID and SHA-256;
   lineage URI; licence/custody evidence; and a reviewable row extract.
2. An approved outcome definition with metric, unit, benefit direction,
   smallest meaningful effect, aggregation/transform, exact baseline and
   outcome windows, and eligibility policy.
3. Governed strategy identity/version for treatment rows; genuine eligible
   untreated controls with `strategy_id = NULL`; the common risk-set rule; and
   the pinned strategy taxonomy release/checksum.
4. Stable independent subject and episode keys; cohort and assignment time;
   assignment mechanism/probability; intervention interval/exposure; and a
   predeclared spatial-block scheme and key.
5. Same-metric raw baseline and fully matured outcome measurements with source
   release IDs, record locators/checksums, observation intervals and first
   availability times.
6. Ordered covariate fields with value, evidence/release lineage and proof each
   value was available no later than assignment.
7. A complete direct-field `strategy_label_source_mapping_v1` manifest that
   passes the database-free preflight and produces a canonical mapping checksum.

Mapping preflight is necessary but not sufficient. It proves only that a
complete direct-field contract was declared for a named release; it does not
prove rows exist, that the semantics are true, or that treatment/control
support gates pass.

## Remaining release gates

No database step is authorized until an operator supplies an explicitly
authorized PlantGeo target and read-only census scope. A future census must
identify its target without exposing credentials, use `SELECT`/catalog queries
only, and record exact causal-plane relation counts and release identities.
Any later write requires separate authorization, a disposable target first, a
reviewed mapping and source extract, restored enforcement or an equivalently
reviewed safe writer, exact checksum receipts, the evaluation-only estimator
and abstention diagnostics, and an independent release review.

This bounded documentation continuation may be archived after its exact commit
and independent review are recorded. The strategy-selection governance blocker
must remain open until the source-admission and authorization gates above are
actually satisfied.
