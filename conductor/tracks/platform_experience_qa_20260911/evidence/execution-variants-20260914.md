---
type: evidence
status: active
date: 2026-09-14
---

# September 14 bounded execution variants

This index separates executed portions of the [case inventory](cases.md) from whole-case
acceptance. The full matrix remains 220 cases: 178 not-run and 42 blocked, with zero whole-case
passes. A passed variant below covers only its stated actions, candidate and environment.
The owning session receipts retain original failures, service identities, raw captures and hashes.

| Variant | Related cases | Result and exact scope | Evidence and remaining dimensions |
| --- | --- | --- | --- |
| `contribution.desktop.synthetic-local` | PGQA-C04, C06–C10 | Candidate 13; actual registered contributor/expert/admin/viewer identities; consented Point publication by an expert, Polygon rejection by an admin, contributor outcome and anonymous public-map boundaries; Session 5 browser 4 passes 3/3 including identity prerequisite. | [Session 5](runbook-session5-20260914.md). Bounded journey includes public request/social interactions and real Martin reads. Full role/state/concurrency, privacy/team, accessibility, agent parity and real-human dimensions remain open. |
| `contribution.mobile-emulated.synthetic-local` | PGQA-C04, C06–C10 | Candidate 13; equivalent expert-publish/admin-reject workflow at 390×844, touch emulation and actual map tap; Session 5 browser 5 passes 3/3 including identity prerequisite. | [Session 5](runbook-session5-20260914.md). Not physical-device or full mobile visual/accessibility acceptance; retained popup overlap requires separate evaluation. |
| `workspace.desktop.unsent-navigation` | PGQA-A20, A22, A24 | Candidate 14; unsent Point and Polygon, real About/Map client navigation, fresh canvas restoration, pane switching, canceled close/Escape, confirmed discard; Session 5 browser 7 passes 2/2 including identity prerequisite. No analysis or submission mutation. | [Session 7](runbook-session7-20260914.md). Starts with the proposal pane visible; hidden initialization (A21) is not exercised. Covers proposal-side state only; no live model conversation, transient/failure restoration, complete screen-reader/focus or all role dimensions. |
| `workspace.mobile-emulated.unsent-navigation` | PGQA-A20, A22, A24 | Candidate 14; same unsent recovery/discard at 390×844 with native taps plus injected Escape/Tab; Session 5 browser 8 passes 2/2 including identity prerequisite. No analysis or submission mutation. | [Session 7](runbook-session7-20260914.md). Focus after closing was BODY; physical-device, touch-only and full accessibility acceptance remain open. |

The desktop environmental scenario in [Session 7](runbook-session7-20260914.md) also completes
its bounded response/control assertions for weather, three moisture depths, withheld SoilGrids,
unpublished SSURGO and static watersheds. Independent frame review confirms the weather gap
removes weather labels while preserving moisture, then restores weather and changes depth values.
However, its final unavailable-SSURGO date-summary variant **fails**: D260914-26 records a
fabricated served-date implication in cases T01/T03/T05 and L10. This prevents acceptance of
the complete mixed-date/static scenario despite the one automated test passing. Its three earlier
harness failures remain retained, not relabeled as passes.

The workspace author and independent screenshot reviewer are distinct tasks. The canonical
[Session 5 review](independent-review-session5-20260914.md) binds contribution evidence;
the [Session 7 independent review](independent-review-session7-20260914.md) retains the successful
scoped environmental assertions and the separate date-summary defect. The full acceptance verdict remains RED.

Session 9 adds separate Candidate 14 social variants: comment ownership denial, administrator
deletion and reload absence pass in the first attempt; the stale-review variant fails with a
persisted reversal. The initial likes prerequisite hits the normal login limit, then a reviewed
likes-only attempt passes two-client own-state and shared-count reload checks. Both actors restore
their own baselines; no independent final-total reread is claimed. Its original automatic credential
snapshot is explicitly sanitized after capture. See the [Session 9 receipt](check-receipt-session9-20260914.json).

Session 10 adds Candidate 14 temperature/VPD desktop and mobile control/read variants, including
four temperature depths, independent September 4/5 dates, latest return, supported strength and
separate visibility controls. Desktop temperature numeric values are visually reviewed. Mobile
map rendering and isolated VPD labels remain unaccepted because the manager obscures the mobile
canvas and the combined view retains temperature labels. The original unsupported zero-strength
expectation is a retained harness failure. The [Session 10 independent review](independent-review-session10-20260914.md)
binds the exact captures and limits. No entire layer, case or rung-conservation result is promoted.
