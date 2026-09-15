---
type: qa-evidence
date: 2026-09-14
status: scoped-deferred-owner-contract-required
author: /root/qa_inventory
coordinator: /root
base_commit: 0f16e40dae3cce1d3b6d4ac00138254a968d974f
---

# Public-request voting scope reconciliation

Public-request votes are a preserved product concept whose replacement writer and UI were explicitly deferred. The current owning track defines public submission, visibility, display names, likes and comments; it does not define a complete replacement voting flow. This dated finding corrects the interpretation that the earlier QA note naming voting as “next-session work” establishes an implementation contract. It preserves that historical note and does not reduce the runbook goal: the defined request, like and comment journeys still require full QA.

This lane inspected specifications, implementation plans, schema, router, aggregate reader and existing tests. It made no runtime edits, test executions, real submissions, database writes or external application changes. This artifact is an investigation handoff, not a passing QA receipt or authorization to activate voting.

## Current decisions versus stale tasks

The controlling owner decision is the resolved OQ-C paragraph in the [public-request specification](../../public_strategy_requests_20260913/spec.md), lines 117–120, repeated in the [implementation plan](../../public_strategy_requests_20260913/plan.md), lines 35–36: votes and likes remain separate, votes have no toggle-off, their count remains denormalized, and the vote foreign key moves to the public feature ID. This explicitly overrides the older recommendation to merge votes into likes.

The same documents retain contradictory older text: spec OQ-C at lines 189–199 recommends replacing votes with likes; FR-3 at lines 319–325 and plan Phase 3 at lines 84–98 still describe dropping `request_votes`. Those older tasks are stale in light of the dated resolution. They do not authorize deleting the retained table or combining votes with likes.

The actual implementation follows preservation. The [schema](../../../../src/lib/server/db/schema.ts), lines 373–392, retains `request_votes` with a feature/user composite primary key and a foreign key to `geo.features`. Its explicit disposition at lines 381–384 is **DORMANT**: the old writer was removed with the private flow; no replacement is wired; wiring belongs to a future track. The [retirement regression](../../../../src/__tests__/security/strategy-request-retirement.test.ts), lines 45 onward, checks that the table survives and references the feature ID.

The current defined social work is narrower than activating votes. Spec FR-2, lines 304–315, and plan Phase 5, lines 146 onward, require the existing feature-social like/comment procedures to work for request features and the shared detail panel. They contain no replacement vote mutation, read-state contract or voting UI acceptance criteria. The retired private `community.voteOnRequest` procedure cannot be restored unchanged: the private/team-scoped request path was deliberately removed.

## Runtime facts and unresolved contract

| Subject | Established behavior | Decision needed before activation |
| --- | --- | --- |
| Vote identity | One retained row per feature/user; separate from `feature_likes`; no writer is mounted. | Confirm a replacement public-vote flow belongs to the currently authorized implementation scope and give it explicit acceptance criteria. |
| Count | [Community activity](../../../../src/lib/server/services/community-activity.ts), lines 47–49, uses `count(*)` on retained vote rows. The old denormalized column disappeared with `strategy_requests`. | Reconcile the owner’s denormalized-count decision with the surviving count-derived aggregate. Do not invent a mutable property count or silently replace the decided semantics. |
| Target eligibility | Community activity includes only published, geometrically located request-kind features on the interventions layer. The vote table's foreign key alone allows any feature ID. | Define which request states can receive a vote and enforce request kind/layer in the mutation. Published-request-only eligibility is a possible contract, not an already implemented rule. |
| Authorization and privacy | [Intervention social](../../../../src/lib/server/trpc/routers/intervention-social.ts) uses authenticated reads, contributor writes and visibility checks covering published features, own/team rows and consented review proposals. | Define vote-specific read/write eligibility. Reusing every social visibility branch would silently permit voting on unpublished proposals or non-request features. Preserve existing proposal privacy and do not infer a new voter identity directory. |
| Interaction | Votes are explicitly one-way; likes are toggles. | Specify disabled/already-voted behavior, repeat-request idempotence, truthful count refresh and failure state. A generic like button is not the decided voting interaction. |

The existing public request submission and social authentication decisions remain settled within their defined scope. This finding does not reopen those decisions or require extra approval for their QA. It identifies the absent replacement contract for a separate, deferred operation.

## QA ledger disposition and next work

[Case ledger](cases.md) C03 retains the historical vote/repeat/reload scenario, while R03 still names the retired private/team ledger. The “next-session work” sentence in [defects](defects.md) is a historical planning note, not proof of a defined public voting API. The coordinator should add a dated clarification and bind these cases to the current architecture without erasing their history. Voting must not receive a PASS based on preserved schema or empty aggregate results.

Recommended next action: reconcile the owning spec and plan around the dated OQ-C resolution, explicitly retaining dormant voting until the replacement eligibility, authorization, count and interaction contract is accepted. Then a bounded implementation can connect the existing table and aggregate machinery, with meaningful idempotence, unauthorized/ineligible target, privacy and UI-state regressions. No runtime activation is recommended in this session.

Meanwhile, continue the already defined public-request, likes, comments, display-name and proposal-consent QA journeys. Deferring an undefined voting replacement does not close those journeys or the long-horizon runbook goal.
