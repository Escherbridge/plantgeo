---
type: session-evidence
status: active
date: 2026-09-14
---

# Session 5: isolated social and workspace lifecycle

The previous session made verified progress; the full RUNBOOK goal remains active. This session
continues from [candidate 8](source-candidate-20260914-8.json), preserving all source hashes and
pre-existing changes. Application source is unchanged at intake. Root owns infrastructure,
execution and shared records; the existing workspace author owns the local browser harness,
the QA inventory author owns the narrowly scoped viewer fixture, and the independent verifier
reviews each concrete execution boundary and final evidence separately.

## Isolated database prerequisite

The root-owned scratch packet is `.omc/research/runbook-20260914/session5/`. Its environment plan
records identities before creation. The verified cached PostGIS image lacked pgvector, so a new
QA-only derivative was built with the signed PostgreSQL package repository. It contains
PostgreSQL 16.15, PostGIS 3.4.3, pgvector 0.8.6, pgcrypto and btree_gist. The image ID is
`76e9379c83d68a48877acb8b48108778bbc352932887debc2240e48d45488a18`.

The disposable database is `plantgeo_qa_session5` at `127.0.0.1:5547`, container
`plantgeo-qa-session5-20260914`, volume `plantgeo_qa_session5_20260914`, with container ID
`7c70921abb2b5923f646ac7eb458444b69c120fdce2e0c4c3d84e1785cd9f943`.
Its cluster system identifier is `7685488873518952482`. Generated credentials stay in ignored
private scratch files; none belongs in review receipts. Existing local databases and volumes
were not reused or modified. Extensions were enabled explicitly after the package gate.

The initial internal bridge accepted a host TCP connection but PostgreSQL handshakes timed out.
The migration subprocess reached its 180-second limit. No orphan migration process remained.
Only this QA container moved to the separately named `plantgeo-qa-session5-20260914-bridge` network;
its container and volume identities stayed fixed. A successful host query then confirmed the
cluster identity, no Alembic/agri migration objects, and zero other client backends. The original
failed attempt is retained. Any resume is explicit and checks that state before issuing migrations.

## Journey preparation

The authored browser harness uses real local registration, credentials login, consented request
and proposal submission, social mutations and expert/admin review. Martin will read only this
QA database's published intervention tiles. Basemap fixtures are identified separately; social
tRPC and intervention tiles must not be mocked. AI/model endpoints are blocked and counted.
Authentication bodies, tokens, cookie state, traces and auth-page captures are excluded.

The viewer fixture is an authenticated but contributor-ineligible negative role. Registration
and the supported production operator do not offer that platform role; it is distinct from
team viewer membership. Its QA-only helper requires the exact previously registered synthetic
identity, environment-plan hash and cluster identifier, defaults to read-only, and permits one
contributor-to-viewer field update after separate review. This does not introduce a production
role policy or forge a session.

At this preparation checkpoint, execution, screenshots, outcomes and final independent reconciliation were pending. No completed
matrix cases, engineering pass, production acceptance or runbook closure are inferred from setup.

## Bootstrap defects and frozen correction batch

The real fresh-database exercise exposed two independent search-path defects. The Alembic
baseline clears `search_path`, so its unqualified version-ledger insert failed and the transaction
rolled back. The reviewed environment now explicitly locates its ledger in `public` in both online
and offline modes. A subsequent real attempt successfully committed revision `20260912_0000`.
Drizzle then failed after its own baseline cleared the path: the next unqualified table creation
is in `0003_land_context.sql`. The application migration transaction rolled back, leaving an empty
Drizzle ledger and no application users. Historical SQL, journal entries and readiness pins remain
unchanged.

The shared correction establishes the canonical public schema between migration files while
preserving their original hashes and the installed Drizzle transaction implementation. Bootstrap,
deployment and the local migration command share that implementation. Candidate 10 includes
the corrected Alembic regression and the entire reviewed migration batch; its manifest additionally
covers Alembic configuration, untracked tooling, documentation and `.dockerignore`, omitted from
earlier manifest scopes. Earlier `preexisting_untracked_sources` fields described files untracked
at freeze and must not be interpreted as proof that all those files predated this run.

Candidate 9's Python sweep failed: Ruff import ordering and the new offline regression's missing
`as_sql` option were corrected. An existing concurrent local-storage CAS test also failed with a
path-containment error. One bounded diagnostic performed 1,000 synthetic CAS operations and 2,001
path resolutions without reproducing it. CPython's Windows extended-prefix validation and
FileLock's close-then-unlink behavior provide a plausible race mechanism, but the original returned
path was not captured. The defect remains unresolved; no containment guard was weakened and no
production storage change is claimed. Candidate 10's integrated checks and explicit Drizzle resume
are pending at this checkpoint. All failed attempts remain in the ignored Session 5 evidence packet.

## Verified bootstrap and engineering checkpoint

The explicit `sourcefix2` resume passed against the same pinned local database. All six Drizzle
ledger hashes/timestamps match the original SQL packet; the subsequent deployment migration no-op
left the snapshot unchanged. There were zero users/features, eleven total reference layers and
the eight readiness-required layers. Independent review confirmed receipt SHA256
`3eea760395e9a9c39fe33e97c507720a6724309c323e31342d1e95bf5a5208d1`.
Host checks returned authenticated Redis `PONG`, Martin health HTTP 200 and a catalogue containing
only `geo.intervention_tiles`. A fresh relation census is captured for separate boundary classification.

Candidate 10 passed twelve tooling tests and all 2,512 frontend tests across 192 files, plus type
and data-boundary checks. Lint initially failed to enumerate two inaccessible historical pytest
scratch directories. The retained retry with the explicit `.omc/**` CLI exclusion passed with zero
errors and 9,887 warnings; no tracked JavaScript/application source is under that exclusion.

Candidate 11 changes only the reviewed Windows local-storage implementation, its directory
rationale and deterministic path tests. It preserves containment/symlink guards and the original
concurrent CAS test. Its Python sweep passed format/lint/mypy and 4,497 tests, with 146 skipped and
one expected failure, but failed one historical spool directory rename with Windows access denied.
The earlier CAS and NASA materialization tests passed in this sweep. That does not prove the prior
failures' causes: the namespace mechanism was characterized with simulated native results, and
directory-rename failures remain unresolved. No full Python quality receipt is issued.

## First browser attempt and recovery

The first attempt reached the real local app but no browser tests ran. Its isolated environment
blanked `WEATHER_LAYER_ID`; the source uses a nullish default, so readiness counted seven of eight
layers despite all eight being seeded. Canonical environment values were restored after the attempt.
The runner also timed out during process inspection, leaving cleanup unknown and port 3128 occupied
in its final receipt at 20:57:04 UTC. Later root inspection found all four recorded processes already
exited, so no manual termination occurred; a 20:58:51 UTC check confirmed the port closed. This later
observation does not convert the attempt into successful unattended cleanup. The reviewed runner
is being corrected to clean up known identities even when fresh discovery fails. D260914-17 remains
open pending an actual successful bounded run. No synthetic identities or contributions were created
by this attempt.

## Browser attempts 2 and 3: retained failures

Attempt 2 passed readiness but `/register` compilation failed when Tailwind's automatic discovery
entered an inaccessible historical `.omc` pytest directory. The original report and logs remain in
`browser-attempt-2026-09-14T21-04-09-088Z-c706c659/`. Root stopped the exact revalidated Playwright
process after the known compile failure; an earlier inspection guard failed before issuing a stop.
The runner's own receipt still records failed cleanup and an occupied port. Root's later 21:11:10
UTC closed-port observation is separate evidence, not an unattended cleanup pass.

The independently reviewed CSS correction sets `source("../")` in `src/styles/globals.css`, limiting
automatic class discovery to `src`. The audit found production templates under that root; MapLibre's
explicit stylesheet and Next's generated font styles retain their own compilation paths. No evidence
directory was deleted and no filesystem permissions were changed to hide the failure.

Attempt 3 passed normal registration and role setup for all four synthetic identities. Point and
Polygon drawing/consent checks, both real submissions, expert publication and admin rejection reached
successful responses and assertions. The proposal test then failed because the Next development
indicator covered Map manager. Its final screenshot showed the indicator collision without an error
panel. The request test failed at an assumed `main` locator even though the community snapshot showed
the public disclosure; no request/social mutation occurred in that test. The runner recorded an
inspection timeout even though no owned processes remained and the port was closed, so cleanup was
not classified as a clean-run pass. Full original evidence remains in
`browser-attempt-2026-09-14T21-14-28-408Z-096c590c/`, with decoded bounded evidence in
`browser3-extracted/` in the ignored Session 5 packet.

The reviewed corrections disable only the supported Next development indicator, retaining compile
and runtime error overlays, and target the actual `#application-content` disclosure container. The
missing main landmark remains a separate layout accessibility audit finding; no shared wrapper was
blindly changed. A resume-only setup branch pins the original identity receipt, browser report and
Candidate 12 manifest, then verifies the same users and roles through fresh normal logins. Resume
does not register users, run role operators, import cookies or reset database records.

## Candidate 13 and completed bounded desktop lifecycle

[Candidate 13](source-candidate-20260914-13.json) binds 1,747 source files with manifest SHA-256
`31e1850a87258d7baa1264c077d5ba992fc1ae39f36b3e602dbdd7bf146e2db0`.
Its expanded scope includes Next/PostCSS configuration, service Docker configuration, service
database SQL and the root directory rationale. The complete frontend gate passes all 192 files /
2,512 tests plus 12 tooling tests. Type checking, data-boundary checking and lint all exit zero.
Lint retains 9,887 warnings and the explicit `.omc/**` CLI exclusion for inaccessible historical
scratch directories; this is not a zero-warning claim.

The latest complete Python sweep remains Candidate 11's FAILED result: one failed test,
4,497 passed, 146 skipped and one expected failure. The failure is
`tests/test_historical_export.py::test_spools_sorted_bounded_chunks_and_a_recoverable_checkpoint`,
a Windows access-denied directory rename. Format, lint and mypy passed, but no full Python quality
receipt or release pass is issued. Candidate 13 frontend success does not supersede that failure.

Browser attempt 4 used the same isolated database and fresh normal logins to the previously verified
synthetic accounts. The report at `browser-attempt-2026-09-14T21-24-09-624Z-08bfadac/browser-report.json`
records all three tests passed, zero skips and zero flaky results, in 50,160.838 ms. It started at
21:24:12.511 UTC; the runner exited zero and finished clean custody/closed-port teardown at
21:25:06.058 UTC. Its cleanup receipt records only an exact revalidated owned-process stop or
already-exited processes, with no remaining or unverified owned processes. All original reports
remain preserved; fifty decoded safe attachments and a bounded summary are in `browser4-extracted/`.

| Journey | Duration | Bounded result |
| --- | ---: | --- |
| Identity resume | 7,590 ms | Same canonical contributor/expert/admin/viewer IDs, names, emails and roles verified through fresh normal credentials logins; no registration or role operators. |
| Proposal lifecycle | 21,368 ms | Real Point/Polygon drawing, publication consent, AI-tab cancellation with retained draft/canvas, expert publish, required-note admin rejection, contributor outcomes, anonymous public Point geometry and real Martin rendering, rejected-feature anonymous 404. |
| Request/social lifecycle | 19,334 ms | Explicit immediate-public disclosure/consent, real public request, comment and like, reload persistence, anonymous detail/rendering with comment sign-in boundary and comments 401, then unlike and comment deletion. |

The published proposal is `c2533f2d-8942-419e-8348-696612c692d4`; the rejected proposal is
`2f26db95-2710-4321-9cdc-a5fea8ec0719`; the public request is
`eee79988-e994-472b-b3e8-bd4f5d0513aa`. Existing attempt 3 records remain intact. Basemap assets
are fixtures, while application/tRPC mutations and Martin intervention tiles are real. No
authentication bodies, cookie state, traces or secrets are included in the extracted evidence.

These are bounded desktop subsets of C01, C04–C10, R03 and A20–A23, not whole-case closure. No case
counts or acceptance states are changed. At this desktop checkpoint, mobile execution was pending
the separate reviewed harness selection and touch scenarios below; live AI streaming, navigation-unmount browser recovery, the full
authorization/team matrix, environmental layers, sliders and release acceptance remain open.

## Separate bounded mobile lifecycle

Browser attempt 5 selected only the `mobile` project plus its required identity-setup dependency,
with explicit identity resume. The 390×844 mobile lifecycle contexts enabled `isMobile` and
`hasTouch`; controls, consent selections, map feature hits and drawing points used native touch
taps. Proposal entry tapped an empty map point through MapView's existing click handler to open
Location actions, then selected Propose intervention here. It did not use a mouse context-menu
shortcut, the separate Community +Recommend modal, forced clicks or DOM mutation. Long-press and
other mobile context-menu gestures are not covered. The identity dependency retains its desktop
viewport; subsequent contributor/reviewer/anonymous journey contexts use mobile settings.

The report at `browser-attempt-2026-09-14T21-33-04-594Z-658183c4/browser-report.json` records 3/3
expected tests passed, zero skips and zero flaky results, in 35,189.750 ms from 21:33:12.138 UTC.
Identity resume took 7,261 ms, mobile proposal lifecycle 15,648 ms and mobile social lifecycle
10,190 ms. The runner exited zero and completed cleanup at 21:33:49.648 UTC, with no remaining or
unverified owned processes and port 3128 closed. Desktop results and prior failures are preserved.
Fifty safe decoded artifacts and a bounded summary are in `browser5-extracted/`.

The mobile scenario used request centre `[-121.8051, 45.5152]`, Point centre `[-121.84, 45.53]` and
Polygon centre `[-121.82, 45.53]`, avoiding existing desktop records without hiding or deleting them.
Published proposal `a522da02-26b4-4367-83a4-8f3629dc3423`, rejected proposal
`879f8436-3c65-44d9-b801-a01b6f7b6de9`, and request `cd55c571-0fe4-4455-905d-174ac9963269`
are bound in the corresponding JSON attachments. The same bounded geometry/consent/review/public
visibility/social-persistence assertions passed. Public Point geometry matched submitted geometry;
the rejected anonymous detail returned 404, request detail 200 and anonymous comments 401. Final
social evidence records the synthetic comment and like removed. Drawing evidence records retained
canvas and truthful site caption after AI-tab cancellation, with no analysis dispatch.

Visual inspection confirms a visible mobile drawn Polygon and the final cleared social controls.
The final social screenshot also retains a small map hover popup above the detail panel, so this
pass is not a complete visual or accessibility audit. This supplementary mobile result changes
neither case counts nor whole-case acceptance. Full role/team coverage, live analysis, navigation
recovery, environmental/slider validation and the failed Python gate remain open.
