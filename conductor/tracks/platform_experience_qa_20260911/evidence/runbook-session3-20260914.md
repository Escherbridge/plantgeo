---
type: session-evidence
status: active
date: 2026-09-14
---

# Session 3: drawing recovery and environmental read parity

This continues [Session 2](runbook-session2-20260914.md) under the full active RUNBOOK scope.
The prior turn was progress: authored changes, independently reviewed checks and fresh physical
evidence. This session preserves the prior working tree and keeps platform QA RED.

## Workspace recovery candidate

[Candidate 6](source-candidate-20260914-6.json) binds 1,666 files at aggregate SHA-256
`042fd11caab175b2ab321688f96500543d7e9483f2ccccdd454d2dc5ddfd26e6`.
Saved proposal Point/Polygon geometry and fields now rehydrate into a new drawing session after
non-explicit navigation unmount. A replacement map centers the saved proposal location, even when
the new map action is elsewhere. Tab switches continue using the existing drawing instance.
Explicit discard clears the draft; recovery failure retains saved geometry, reports an error and
blocks submission until repaired. This is in-memory recovery, not persistence across page reloads
or restoration of a destroyed drawing engine's undo history.

TerraDraw attaches only when the map style is ready. Independent review caught that an initial
`load` listener alone could wait forever on an already-loaded map during temporary style work;
the revised code also checks recurring `render` events and removes both readiness listeners after
attachment or cleanup. The integrated regression exercises real workspace/form/control wiring
with a fake drawing engine. It does not prove actual browser canvas fidelity.

Source review approved the complete batch before the integrated sweep. The selector chose full
frontend tests: 191 files and 2,509 tests passed. Type checking and data-boundary checks passed;
full lint exited zero with 9,887 warnings. Exact logs and exits are recorded in the
[Session 3 check receipt](check-receipt-session3-20260914.json).
Python runtime/tests are unchanged from the preceding reviewed candidate and were not rerun.
Local changes remain uncommitted and undeployed.

## Expanded physical evidence

The [physical sample packet](physical-samples-session3-20260914.json) adds twenty physical streams
to Session 2's ten. Sixteen have verified time-axis availability generations. The remaining four
are static versioned lanes: their declared contract uses an object census rather than an
availability pointer. Initial missing-pointer probes for those four are retained and explicitly
superseded by the appropriate static inspection, not counted as missing time-axis publication.

The additional samples cover the latest published day and latest indexed-absence day where one
exists, plus the latest discovered static version. Static discovery completed without reaching
its 2,000-key limit. This is sampled physical evidence across thirty streams over Sessions 2/3,
not full history, all API/UI layers, geometry quality or source admission.

| Session 3 rung category | Count | Meaning |
| --- | ---: | --- |
| Full completion part metadata matched | 54 | Downloaded parts match declared keys, sizes, hashes and row counts. |
| Availability-bound digests and counts matched | 15 | Legacy completion markers omit part metadata; availability supplies part digests. |
| Counts-only legacy evidence | 5 | Measured part digests have no publication-side digest binding. |
| Budget-limited published-part inspection | 2 | Burn severity z13 has one unread large part; watershed z13 has eight unread parts. |
| Indexed/marker-stated absence, source unproven | 20 | Five days across four rungs; legacy database-export zero rows do not prove upstream absence. |
| No physical objects in sampled rung | 3 | SSURGO sampled version has no z0/5/9 publication. |
| Raw parts without completion | 1 | SSURGO z13 has 481 parts but no completion marker; 433 parts were not downloaded within budget. |

All 105 sampled source/terminal evidence objects match their declared checksums. Independent
review also compared terminal/index identity, day, rung, state, count and receipt references.
Checksum agreement binds those statements; it does not establish their upstream truth.

The twenty absence samples are drought February 24, 2026; fire detections February 16, 2025;
sensors September 6, 2026; vegetation August 27, 2026; and burn severity August 31, 2026. Their
base-rung absence bodies cite old PostgreSQL/geo.features exports. In the sensor case, retained
positive incoming rows directly contradict an empty-source interpretation. None is accepted here
as source-proven absence. Raw SSURGO files similarly do not establish a completed/admitted layer.

## Selected-day HTTP observations

The [API packet](api-samples-session3-20260914.json) binds 74 responses: 37 layer/day cases queried
twice at rung 5 and bbox `[-124,47,-122,49]`. All responses are HTTP 200, all requested days match,
no response reports truncation, and all 37 consecutive pairs have identical body hashes.
The states are 58 published, ten governed_absence, four day_not_written and two lane_never_written.
The receipt preserves actual served-day fields; unavailable responses have no inferred served day.

Weather September 6 and shortwave June 1 return day_not_written. SSURGO August 28 returns
lane_never_written even though incomplete raw parts exist; no completed lane is proved. Sensor
September 6 returns governed_absence, confirming storage/API agreement on the disputed claim,
not validating that claim. Some published samples have zero rows in this viewport; that does not
mean the global source is empty. Evacuation's discovered September 14 version has 57 global rows
at each rung and zero in this test viewport; the earlier September 11 catalogue capture remains
a historical observation.

Consecutive requests do not establish controlled cold/warm caches. These calls do not exercise
the frontend router/render pipeline, camera/style transitions, temporal neighbours, desktop/mobile
layout, agent final answers or authenticated social writes. Those acceptance gates remain open.

## Sensor incident and social scope

The [sensor investigation](sensor-investigation-session3-20260914.md) binds retained primary logs:
September 6 had 2,377 incoming rows and September 5 had 2,850, rejected by the existing absent-state
guard. Five failed attempts in one run are distinct from the breaker's three unsuccessful buckets.
The exact source archive/candidate custody, correction request and writer/retry-worker quiescence
are not established. The existing date-pinned correction operator remains the narrow preparation
path once its required artifacts are recovered; scheduler supersession is a separate reviewed step.
No correction, supersession, activation or production write was performed.

The [public-request voting scope reconciliation](public-request-voting-scope-session3-20260914.md)
corrects the earlier next-session interpretation. Votes were retained as distinct product intent,
but their replacement writer is explicitly dormant and lacks resolved target/auth/count semantics.
This session does not activate it by copying like policy. Defined request, like/comment, display-name,
proposal-consent and authorization journeys remain required. The dated case correction retains
the old R03 wording as history, aligns its expectation with public requests, blocks C03 on the
replacement contract and adds A24 navigation recovery. Current inventory is 220 cases: 178 not_run
and 42 blocked. Inspection also found stale private-request assurances in the `/community` page
heading/body/metadata, contradicting its public ledger component; D260914-16 belongs in the next
complete source batch, alongside related disclosure review.

## Next bounded session and remaining gates

1. Reconcile the disputed legacy absence evidence with environmental/gapless owners and source
   custody. Preserve original receipts; avoid wholesale marker deletion or invented replacement data.
2. Continue all-layer router/UI/agent selected-day parity, interval/neighbour/error/rung cases and
   botanical/land-context/forecast admission prerequisites. Thirty sampled physical streams do not
   close the 31-registry-layer and four land-context-group matrix.
3. Exercise defined social and workspace journeys in their authorized isolated setting once the
   needed runtime is available; real-human acceptance remains separate. Dormant voting requires
   an owning contract before implementation.
4. Bind an immutable candidate and actual browser runtime, then perform drawing/render fidelity,
   rapid sliders, desktop/mobile, accessibility, mixed-layer performance and final release QA.
5. Obtain sustained scheduled advances and production/operator evidence before release acceptance.

The browser inventory remains unavailable. The 220 journey cases retain their open statuses; no
browser PASS, production acceptance, GREEN, archive or runbook closure is issued by this session.
