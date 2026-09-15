---
type: independent-review
date: 2026-09-14
reviewer: /root/independent_verifier
status: accepted-with-scope-limits
check_receipt_sha256: f7f61cf2bb1b1e54513f6cbe11c960103052e5f5134b3fca6da0808352f098b4
overall_platform_qa: RED
---

# Independent review: Session 9 social and authentication evidence

Session 9 establishes a real stale-review defect, a passing comment ownership/moderator
deletion variant, and a separately passing two-client likes/reload variant. It also exposes
and remediates an authentication failure-snapshot privacy defect. These outcomes remain
separate: later likes success does not erase the earlier moderation failure or credential
capture. Accept the bounded evidence; whole-platform QA remains **RED**.

The frozen [check packet](check-receipt-session9-20260914.json) is 20,810 bytes, SHA-256
`f7f61cf2bb1b1e54513f6cbe11c960103052e5f5134b3fca6da0808352f098b4`.
Independent recursive verification checked **54 artifact references with zero hash/size
mismatches**. The reviewer also read the packet generator without executing it and had
already independently inspected both original reports, decoded factual/network attachments,
server logs, recorded cleanup, source contracts, archive hashes and privacy scans. This
review completes the pending handoff described in the [session ledger](runbook-session9-20260914.md).

Both real attempts belong to externally coordinated Candidate 14, manifest SHA-256
`79d15c03290945063dd228cce768e57ab0b2eb4d442ce22f9fa894c66de63685`, 1,747 paths.
The packet binds root's before/after source checks; runners did not embed the source
manifest. Subsequent Candidate 15 fixes and checks are separate evidence and cannot be
substituted for the source under these diagnostic runs.

## First attempt: distinguish the two failures

Attempt `2026-09-14T22-55-55-664Z-7f1b5fea` recorded two passed and two failed tests,
zero skips/flaky results, in 57.856 seconds. Its report hash is
`beca0e4016f6f3bf8d96eaf9ed701195d677e989c74e7aee09e626e9661bba4c`.

The normal identity prerequisite passed with four previously registered synthetic identities
and no registration/role operators. The stale-review test created consented Point
`379934ad-4cee-4871-9f3f-ab753b853be9` through the real contributor form. Both reviewers
loaded the pending row. Expert approval returned published with a null note; the
administrator's stale rejection returned HTTP 200 and replaced that decision with rejected
and the second note. A fresh contributor detail read confirmed the persisted reversal.
This is **D260914-27**, violating the explicit pending-only interpretation reviewed before
execution. It is a deterministic stale snapshot, not simultaneous transaction scheduling or
proof of a preexisting administrator-override policy.

The independent comment variant passed. An expert authored one synthetic comment on the
hash-pinned, already published Session 5 feature. A different contributor had no delete
control and received HTTP 403 from the real endpoint. Administrator UI deletion succeeded;
repeated deletion returned 404; contributor reload/reopen showed the comment absent.
These observations support ownership and moderator access, not the complete P04 team,
bbox, hidden-count or feed matrix.

The likes test did not reach its behavior. The Next log records ten successful credentials
callbacks followed by HTTP 429, and the captured DOM explicitly reports too many attempts.
Source limits are ten sign-ins per IP and five per account per fixed minute. No like baseline,
mutation or restoration ran. This is an authentication-budget prerequisite failure, not an
incorrect like count. No limiter was reset, disabled or bypassed.

All eleven final network attachments recorded zero analysis calls and blocked requests.
Cleanup stopped revalidated owned Next PID 54532, recorded two descendants already exited,
left no remaining/unverified owned processes, and closed port 3128 at 22:56:58.842 UTC.
The runner hash is `a7f510b2b266ed3316f54ceebd25d07ef128bbc2e740799cb3822e2aa0c35d95`.

## Authentication capture defect and correction

The original login helper's sanitized exception and explicit screenshot exclusion did not
prevent Playwright's automatic ARIA snapshot from retaining the password textbox value.
Independent inspection found it in one `error-context.md`, with none in the report, runner,
logs or decoded inline attachments. **D260914-28** is a harness artifact defect; the original
capture was not fully credential-safe.

Root sanitized that single occurrence after execution. The bound redaction receipt records
original/sanitized hashes and sizes, one replacement and no retained unredacted copy. The
reviewer independently verified the sanitized hash/size, absence of exact known credentials,
and unchanged report/runner receipts. The packet correctly references sanitized bytes and
preserves provenance instead of presenting them as the original capture.

All 15 original harness inputs were archived and independently rehashed before revision.
The revised 18-input packet was independently approved: normal identity resume only, safe
login cleanup, explicit likes-only selection, and `PLAYWRIGHT_NO_COPY_PROMPT=1` required by
runner/config/fixture. The bound installed Playwright implementation confirms that this
setting suppresses automatic ARIA snapshots on context close and test completion. Ordinary
source/error context may still be written. Password-field cleanup verifies empty fields or
closes the page; it does not replace the snapshot guard when teardown is interrupted.

The separately retained dummy probe reconciles to two passing checks and one intentional
expected failure, with no unexpected failures, page-snapshot section or runtime dummy marker
in outputs. It demonstrates populated-field exception cleanup, closing an unclearable page,
and suppressed snapshots with a populated context left open at failure. The first fixture
can fail before Sign in activation, so it does not simulate HTTP 429. The first probe's
report-path collection mistake remains retained. No real credentials or live authentication
were used in these probes.

## Separate likes-only pass

Attempt `2026-09-14T23-31-52-300Z-616c9ccb` passed the identity prerequisite and selected
likes journey, two passes with zero failures/skips/flaky results in 35.236 seconds. Report
SHA-256 is `c4411f6fb4177d9f0eaa71a6fa0d5a7fe0a01cec0753840a95d712b7a794badd`.
Every executed input matches the approved revision. Four prerequisite and two actor logins
produced six successful callbacks after the earlier window expired; no auth counter changed.

Both contributor and expert began unliked at count zero. The test asserted the first toggle's
count of one and second toggle's count of two. Both actual reload attachments independently
report their own liked state and count two, matching the UI and fresh query assertions.
Each actor then successfully restored its own original liked state. The final shared total
was not separately reread. Six network attachments show zero analysis calls/blocked requests;
each scenario context records its initial and restoration toggle.

The post-run exact-value scan and independent scan of retained text plus 28 decoded inline
attachments found no known credentials; no output was altered. The successful run's auth
cleanup fixtures report zero failures but zero remaining pages to clear or close. Thus this
run does not itself exercise failure cleanup; that evidence comes from the dummy probe.
Exact known-value scans are not generic secret audits.

Cleanup stopped revalidated owned Next PID 44864, recorded two descendants already exited,
left no remaining/unverified owned processes, and closed port 3128 at 23:32:32.886 UTC.
Runner SHA-256 is `4e564bbd0b72a15b72e955fc4fb16f9b275a9083d42022bd8cfe0b9a3d13d573`.

## Disposition

The [defect ledger](defects.md) must retain the observed moderation failure until the new
source candidate and fresh stale-review variant are validated. Authentication evidence must
retain the initial privacy failure and dated remediation. Accept the comment and likes
variants within their actual ownership/reload scope, with no complete-case promotion,
simultaneous transaction, mounted automatic refresh, physical-device, production-release,
real-human decision or model-streaming claim. This lane authored the review and read retained
artifacts only; it ran no tests, authentication requests, database writes or process cleanup.
