---
type: verification-evidence
track: intervention_boundary_authoring_20260911
status: accepted
---

# Candidate verification and independent review

Base commit: `89e8494422b8232c8f16dbffdcf2321c7ea17bc8`.
Base tree: `777fe20689dd68c337677cc49e3fd4e63bfd4bcc`.
All database execution in this task uses a newly initialized isolated loopback cluster
at `127.0.0.1:55439`, PostgreSQL 17.5 with PostGIS 3.5.2. The supplied native PG16
login did not authenticate and its extension directory had no PostGIS. Native PG16
settings/data were not changed. No Railway or production database was accessed.

## Initial integrated batch

The requested `test:changed -- --base 89e8494422b8232c8f16dbffdcf2321c7ea17bc8`
selector chose the full suite because deleted runtime files and new E2E infrastructure
trigger its conservative fallback. Type checking passed. Lint reported zero errors
and 545 warnings. The data-boundary gate caught a client preview importing the pure
geometry schema through a server directory; the schema moved unchanged to a client-safe
module, with the original server import retained as a compatibility re-export.

Initial tests: 148 files passed, three failed, two skipped; 2,311 tests passed, seven
failed, 13 skipped. The failures were the MapView render-count fixture lacking the
new child stub, an incorrect expected anonymous expert-gate code, and the worker
fixture omitting HTTP GET. All seven real community PostGIS cases passed.

After that batch of corrections, the full suite passed on September 12 UTC
01:35:57–01:38:30: **151 files passed, two skipped; 2,321 tests passed, 13 skipped**.
Data-boundary checks also passed. The skipped tests are the existing four opt-in
PostGIS spatial cases and nine climate SQL contract cases; their unrelated DSNs were
intentionally unset. This receipt predates the independent review correction below.

## Independent reviewer — initial verdict

A separate subagent context inspected the candidate and independently read the green
full-suite and actual PG/PostGIS receipts. Verdict: **request changes** for one P1
stale-review issue and incomplete browser evidence. No additional blocking code
finding was identified in the bounded pass.

The P1 sequence: reviewer A displays a pending card; reviewer B requests revision;
the author changes the geometry and resubmits; A publishes from the old card. A
fresh mutation-time SELECT and SQL race guard cannot prove A saw the changed site.
The required correction is a server-generated token for the displayed review version,
required by publish/reject/request-revision, checked before acting, while retaining
the SQL predicate against races within the RPC. A real-database stale-card regression
must prove refusal of the old token and acceptance after fresh review.

The independent follow-up **accepted the code correction** with no further actionable
code finding. Queue and individual review reads now derive the same SHA-256 token
from identity/layer/status/properties/review note/full PostgreSQL timestamp; every
review decision requires it. The exact timestamp/properties SQL predicate remains.
On conflict the client clears the obsolete note, refetches and preserves the error.
The new real PostgreSQL test covers the complete stale-card sequence. The reviewer
also ran `git diff --check` successfully. The final execution evidence follows.

The post-review full sweep completed September 12 UTC 01:45:32–01:48:46:
**151 test files passed, two skipped; 2,328 tests passed, 13 skipped**. The community
PostGIS suite now passes **eight cases**, including the stale-card regression,
missing/invalid historical author identities, and queue provisioning. The existing
unrelated opt-in database skips are unchanged. Type checking and data-boundary
checks passed. Final type checking completed at 01:51:21 UTC and lint at 01:51:50
UTC: **zero errors, 545 warnings**. Temporary runner/trace artifacts moved to the
existing ignored `.tmp` directory; no product lint configuration changed.

## Browser acceptance

The isolated harness uses real credentials, auth/community APIs, original-geometry
validation, persisted features, and the actual `geo.intervention_tiles` function.
A local HTTP adapter serves those tile bytes; this is not execution of the Martin
binary. Basemap and unrelated environmental responses are fixtures. The browser
uses the shipped service worker and requires an unchanged warm viewport to display
the published feature. Tests cover keyboard and mobile touch authoring as well.

After harness startup, module interop, basemap fixtures and readiness corrections,
the final browser run passed **three of three tests, exit zero, in 1.2 minutes**:

- Contributor Polygon submission, pending outcome, expert publication, unchanged
  warm viewport rendering, basemap style replacement and contributor published
  outcome: 30.6 seconds.
- Mobile touch rectangle, save, edit, clear and cancel retaining the saved draft:
  16.3 seconds.
- Keyboard rectangle, undo, clear, finish, edit and Escape/cancel: 16.5 seconds.

The first case asserts actual cached empty tile bytes before publication, nonempty
revision tile bytes afterward and the named feature's rendered hover tooltip. Its
history disclosure stays collapsed without changing camera padding. The acceptance
agent visually inspected the publication and style-swap images; the root also
inspected the same-viewport image. Selected screenshots are preserved in `images/`.

All disposable fixture databases were removed and browser service ports 3307/3308
were clear. The task-owned PostgreSQL cluster on 55439 was then stopped successfully;
all three ports have no listeners. Native PostgreSQL settings/data remain untouched.

## Evidence and limits

`verification-receipt.json` preserves commands, timestamps, counts and SHA-256
hashes of the local execution logs. Exact committed head/tree are supplied in the
final integration handoff; a committed document cannot contain its own commit hash.
The independent final verdict is recorded below before committing the candidate.

This verifies PostgreSQL 17.5 / PostGIS 3.5.2, not the unavailable local PG16/PostGIS
combination. It uses baseline-derived community DDL rather than a full migration
replay, a local HTTP tile adapter rather than Martin, and fixture basemap/environmental
responses. Cross-tab notification is within one browser profile; no cross-device
push claim is made. No production records, current production counts, deployment,
or community-to-ML bridge were part of this acceptance.

## Final independent verdict

**ACCEPT — bounded local intervention boundary/publication candidate.** The reviewer
independently read the final full-suite, PostGIS, boundary, type, lint and browser
receipts and inspected the warm-empty, published-same-viewport and style-swap
screenshots. No open actionable code finding remains; the P1 stale-card issue is
resolved. Publication style replacement has actual browser evidence; draft style
restoration and prior-handler-state cleanup retain focused mock-test evidence.
Final Git commit identity and integration remain the root task's closeout steps.
