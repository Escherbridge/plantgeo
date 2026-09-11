---
type: planning-synchronization
status: active
updated_on: 2026-09-11
---

# Botanical baseline synchronization

This successor merges planning baseline
`b9b7bf4fcc0c58556d10fc58522edced8962b869` with the reviewed source packet
`f19678c9609c49ad517b5e03554d1cd17bf4ed4e`. The source track remains **active**;
its WTU and UBC collection-admission decisions remain **blocked** and
`admitted_releases` remains empty. Active work does not authorize acquisition.

The three source-track conflicts were resolved by retaining completed metadata
work, the detailed pending gates, conditional pilot scope and source-owned
evidence boundaries, while adopting the baseline's active track status. No
baseline files outside this source track are edited by the resolution. The
baseline's occurrence, experience, profile, recommendation and registry changes
are inherited unchanged.

## Preserved evidence

All files present under this track's evidence directory at `f19678c`, including
the [admission packet](admission-packet.md), [decisions](admission-decisions.json),
[independent review](independent-review.md), [handoff](requests-and-handoff.md)
and [original verification receipt](verification.json), remain byte-identical.
Only the historical instruction to keep the track blocked is superseded by the
owner's newer active-track instruction. Evidence/admission status fields and
collection verdicts remain blocked; restrictions and findings remain current. The original
receipt and verification recipe bind the earlier tree and are not represented
as validation of this successor. No earlier source capture or research recipe is
rewritten. Current integrity checks are in the
[synchronization receipt](synchronization-verification.json).

No source was fetched again, provider contacted, term accepted, occurrence
downloaded or ingested, runtime changed or production state mutated. WTU still
needs exact-release EML/version evidence; UBC's institutional public-coordinate
policy remains unresolved. Both still lack measured archive/schema/native-ID
proof. A two-release UBC comparison consumes both archive slots and defers WTU
under the existing ceiling; this merge does not expand that ceiling.

## Current botanical relationships

- [Occurrence plane](../../botanical_occurrence_parquet_lane_20260911/spec.md):
  source-admitted specimen occurrences, support and aggregates stay in Parquet;
  the admission handoff remains blocked. Missing traits do not remove specimens.
- [Species-profile lookup](../../botanical_species_profile_lookup_20260911/spec.md):
  parent-owned nonspatial curation may use reviewed database authoring, followed
  by one approval/export transition to immutable `botanical-species-profile`
  Parquet. Serving pins that release, with no live database fallback. Per-value
  source, licence, dates, review state and lineage remain required.
- [Occurrence experience](../../botanical_occurrence_experience_20260911/spec.md):
  preserves specimen evidence semantics and the independent profile lookup.
- [Recommendation validation](../../botanical_species_recommendation_validation_20260911/spec.md):
  composes occurrence, establishment and objective-effect evidence separately;
  an occurrence is not a suitability verdict and a growth match is not an effect.

Occurrence and profile share the canonical taxon authority/version/concept key.
The profile is a related track, not a new dependency that blocks source admission
or otherwise valid occurrence publication. A second trait source remains
deferred enrichment under explicit per-value priorities.

## Review, verification and integration

The [independent synchronization review](synchronization-review.md) evaluates the
resolved tree before the one bounded final check. The
[verification recipe](../../../../.omc/research/pnw-admission-sync-verify-20260911.py)
checks documentation/JSON/links/whitespace, immutable evidence preservation,
baseline inheritance and active-track/blocked-admission consistency. It performs
no network access or application tests.

Integrate the final reported merge commit as a descendant of both inputs. A
checkout at `b9b7bf4` can fast-forward to it if it has no later commits; otherwise
merge that exact successor. Do not cherry-pick the merge with an assumed mainline
or reapply `f19678c` separately. Keep botanical planning outside the runtime
release matrix and do not mark either collection admitted or the track complete.
