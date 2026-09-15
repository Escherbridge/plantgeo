---
type: session-evidence
status: active
date: 2026-09-14
---

# Session 9: stale moderation and social authorization

This continues the [runbook](../../../RUNBOOK.md) with the [Session 8 intake](runbook-session8-20260914.md)
and the same isolated synthetic identities, PostGIS cluster, Redis and Martin services.
Root rechecks all three exact container IDs/images/loopback mappings and database system
identifier `7685488873518952482` before execution; the receipt is
`.omc/research/runbook-20260914/session9/preflight-services-20260914.json`.
Candidate 14 remains unchanged. The independent reviewer verifies all 15 harness inputs and
the actual runner diff against its preserved Session 5 parent before authorizing the run.

The pending-only diagnostic contract is a reviewed clarification: the canonical pending queue
can decide a proposal only while it remains pending. A stale second decision must preserve the
first committed outcome and report a conflict. No exposed or documented administrative override
was found. This does not claim that every historical specification explicitly defines first-write
semantics, or that a deterministic stale snapshot tests simultaneous transaction scheduling.

## First attempt

Attempt `2026-09-14T22-55-55-664Z-7f1b5fea` records two passes and two failures in 57.856 seconds.
Those outcomes have different meanings:

| Variant | Observed result | Interpretation |
| --- | --- | --- |
| Four normal identity resumes | Passed | Existing contributor, expert, admin and viewer identities obtain fresh normal sessions. No registration or role operator runs. |
| PGQA-C11 stale review | Failed | New consented Point `379934ad-4cee-4871-9f3f-ab753b853be9` is published by the expert, then the administrator's stale Reject returns HTTP 200 and persists `rejected` with the second review note. A fresh contributor read confirms reversal. D260914-27 is a reproduced application defect. |
| Comment ownership and moderator deletion | Passed | Expert creates one synthetic comment on the previously published Session 5 fixture. Another contributor has no delete control and receives 403 from the actual endpoint. Admin deletion succeeds, repeat deletion returns 404 and contributor reload/reopen shows absence. |
| Two-client likes/reload | Login prerequisite blocked | The eleventh credentials callback receives HTTP 429 after ten successful callbacks. Source budgets ten per IP and five per account per minute. No like baseline, mutation or restoration runs. This is not evidence of a likes behavior failure. |

All eleven final network attachments report zero analysis requests and zero blocked requests.
Cleanup stops the verified owned Next process, observes the other descendants already exited,
leaves no remaining/unverified processes and closes port 3128 at 22:56:58.842 UTC.
The report SHA-256 is `beca0e4016f6f3bf8d96eaf9ed701195d677e989c74e7aee09e626e9661bba4c`;
runner receipt SHA-256 is `a7f510b2b266ed3316f54ceebd25d07ef128bbc2e740799cb3822e2aa0c35d95`.
The proposed likes-only continuation will use the normal four-account prerequisite plus two actor
logins after the actual window expires. No limiter is disabled, bypassed or cleared.

## Automatic snapshot privacy defect

The sanitized thrown login error and explicit screenshot policy did not prevent Playwright's
automatic `error-context.md` from retaining the password textbox value. Independent inspection
found the exact known synthetic credential in that one text artifact only, with no occurrence in
the report, runner receipt, logs or decoded inline report bodies. This is D260914-28, a harness
artifact privacy defect; the original capture must not be described as fully withholding credentials.

Root redacted the one occurrence in place without printing the value. The attempt's
`privacy-redaction-receipt.json` records original and sanitized hashes/byte counts, one replacement,
and zero remaining exact known credential occurrences in the subsequent text/inline-body scan.
No unredacted copy is retained. The report and runner receipt are unchanged; the error-context
artifact is explicitly a post-run sanitized version, with the failed outcome preserved.
This exact-value scan is not a generic secret-audit certificate.

Before another authenticated run, the author must prevent automatic failure snapshots from
retaining credentials, including identity-prerequisite failures, and obtain independent review.
Executed harness inputs will be preserved before changing the current packet. The moderation
fix and D260914-26 date fix are being prepared as unapplied patches for one complete source batch;
no product change or integrated-check rerun has occurred yet.

## Reviewed likes-only continuation

The current harness was independently reviewed after preserving all 15 original manifest inputs
in the failed attempt's `executed-harness/`. The archive manifest hash is
`a09810d26412235f8d1db9a1cec636d2efce19223940d4267a9b24b375f75a48`.
The new 18-input manifest is `2db1cb4d723734cc11dadcd953a5ba7e4d4266e9c0dae78e681a4e24a2d683c8`.
It requires explicit resume and likes-only selection, safe field cleanup, and scoped
`PLAYWRIGHT_NO_COPY_PROMPT=1`. This disables automatic ARIA page snapshots; ordinary source/error
context can still be written. Session 5 inputs remain unchanged. No auth counter was reset.

A separate dummy-only browser probe verifies populated-field exception cleanup, closing an
unclearable page, and absence of the runtime dummy value or Page snapshot section after an
intentional failure with a populated context. Two tests pass and one fails as intentionally
expected. The first probe's collector used an incorrect relative report path; original outputs
remain preserved. The corrected receipt is
`.omc/research/runbook-20260914/session9-auth-validation/receipt.json`, SHA-256
`d63736c22b490758ec709051b7e5dc2c94e5e3e99ed331e1e1958fcb8bb936fc`.
This probe does not simulate a real HTTP 429: its first fixture can fail before Sign in activation.

The separate real attempt `browser-attempt-2026-09-14T23-31-52-300Z-616c9ccb` passes the identity
dependency and two-client likes scenario, 2/2 tests in 35.236 seconds. Four prerequisite logins and
two actor logins use normal authentication after the earlier minute window expired. Its report
SHA-256 is `c4411f6fb4177d9f0eaa71a6fa0d5a7fe0a01cec0753840a95d712b7a794badd`.
The runner stops its owned Next process, records two already-exited descendants, no remaining or
unverified owned processes, and closed port 3128 at `2026-09-14T23:32:32.886Z`.

The read-only post-run `privacy-scan.json` examines five text files and 28 inline report bodies,
finding zero occurrences of the four exact known private QA values. It changes no output and does
not certify arbitrary secrets. All 1,747 Candidate 14 source hashes match before and after this
run; this is an external coordinator binding. Each actor's own baseline is restored by the test
finalizer, but the final combined total is not separately reread. The canonical
[Session 9 receipt](check-receipt-session9-20260914.json) binds both attempts and supporting artifacts;
the [independent result review](independent-review-session9-20260914.md) verifies all 54 artifact
references and the bounded outcomes. Its SHA-256 is
`8ea77ed0e0572f9a68ab9f85a0ebaa8a29f5ad61b1982ad44e914b1b507802ed`.
No whole case or release is promoted.
