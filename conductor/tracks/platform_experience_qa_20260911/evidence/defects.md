---
type: defect-ledger
track: platform_experience_qa_20260911
recorded_on: 2026-09-14
---

# September 14 defect intake

These findings are scoped observations, not an integrated acceptance verdict. All browser cases
retain their existing unpassed status. The current source and receipts are bound in the
[session ledger](runbook-session-20260914.md).

| ID | Severity / requirement | Evidence and observed behavior | Owner / disposition |
| --- | --- | --- | --- |
| D260914-01 | High / botanical regional support | [Fresh request](public-read-requests-20260914.json) at bbox `-130,40,-110,50`, zoom 8, pinned release returns HTTP 400 `bbox_too_large_for_zoom`. | Botanical plane/experience; viewport policy question pending. No constant increase or silent crop. |
| D260914-02 | Medium / L31 | GBIF-only empty detail results and unsupported detail zoom previously had no visible explanation. | `/root/gbif_feedback_executor`; feedback and regression tests applied, browser acceptance open. |
| D260914-03 | Investigation / occurrence receipt | [Four object reads](botanical-read-20260914.json) confirm zero identification rows match manifest zero; release receipt has no extension rows or unread reasons. | Receipt concern resolved within this scope; source field-map/admission remains open. |
| D260914-04 | High / N01–N11 | Land reference reader remains placeholder, query adapter discards geometry, queries require explicit point/area selection. | Land reference/contact owners; data admission/reader and geometry fixes required. Viewport redesign is a separate decision. |
| D260914-05 | High / A18–A22 | Workspace could ignore new requested mode, erase pre-geometry work, mismatch preserved coordinates, and leave conflicting hidden-panel Escape handling; draw map lacked reveal resize. | `/root/workspace_social_audit`; bounded lifecycle fixes and regressions applied. Browser drawing/stream/mobile and non-explicit navigation persistence remain open. |
| D260914-06 | High / C17 | Every feed proposal displayed a hardcoded `+14% Soil Moisture retention` claim without any supporting field. | Unified intervention track; removed with regression. No efficacy claim is substituted. |
| D260914-07 | High / agent unavailable behavior | Unknown-release test selected configured storage; a connection failure escaped typed botanical unavailable handling. | `/root/qa_inventory`; isolation and narrow metadata transport normalization implemented, independently reviewed, and covered by passing full selected pytest. Live outage/browser acceptance remains open. |
| D260914-08 | Environment / Python checks | Initial uv cache access denied; pytest then hit denied default temp and malformed Windows basetemp arguments. | Root; workspace cache/temp and forward-slash paths supplied, historical failed logs retained. Retried checks are not new code evidence until candidate freeze. |
| D260914-09 | Blocking prerequisite / browser matrix | `cua.getBrowser` found none; `cua.getState` returned empty apps/browsers. | Session runtime/browser provisioning; no canvas/mobile/keyboard/touch/authenticated journey evidence is claimed. |
| D260914-10 | Medium / verification tooling | Full ESLint exited zero with 9,887 warnings, including historical `.claude/worktrees` vendor bundles. | Repository conformity owner; scope cleanup requires its own bounded change. This pass is zero-errors, not zero-warnings. |
| D260914-11 | Blocking handoff evidence / environmental recovery | The pre-existing dirty environmental plan links to missing `evidence/freshness-audit-20260913/audit.md` and `freshness-resolution-plan-20260913.md`; the local link scan found both absent. | Environmental serving owner; recover original evidence or author a dated replacement audit/plan. Existing plan changes are preserved, and no audit result is invented. |

Session 2 implements consent-preserving proposal-to-AI entry (A23), with passing engineering
regressions; live browser acceptance remains open. D260914-11 is partially recovered: both original
Markdown documents, the captured manifest and three checksum-verified JSON artifacts are restored.
Original coverage JSON/CSV and the launch note remain missing. See the [session 2 ledger](runbook-session2-20260914.md).
Public-request voting remains next-session work. The community label bridge still requires the separately recorded owner
decisions; QA does not invent consent, source admission, outcome labels or real-human submissions.

## September 14 session 3 clarification

The [voting scope reconciliation](public-request-voting-scope-session3-20260914.md) supersedes
the preceding interpretation of dormant voting as already-defined implementation work. Existing
votes remain retained; replacement auth/eligibility/count policy must be specified by its owner.
Defined request, like/comment and proposal-consent QA remain required.

| ID | Severity / requirement | Evidence and observed behavior | Owner / disposition |
| --- | --- | --- | --- |
| D260914-12 | High / workspace recovery | Stored fields/geometry survived navigation unmount, but a new TerraDraw session did not import them; style readiness could also prevent attachment. | Session 3 restores Point/Polygon geometry and waits on recurring map readiness. Independent source review approved; exact check receipt and browser gates remain separate. |
| D260914-13 | High / sensor publication | [Incident evidence](sensor-investigation-session3-20260914.md) records positive September 5/6 incoming rows rejected by existing absent-state guards; September 6 still serves governed_absence. | Gapless/environmental owners; recover exact candidate/source archive, review pinned correction and writer quiescence, then separately address held scheduler. No production repair performed. |
| D260914-14 | High / absence truth | [Physical packet](physical-samples-session3-20260914.json) binds twenty indexed/marker absence samples based on legacy database exports; none is upstream-source-proven here. | Environmental/gapless governance; reconcile source evidence, with the sensor sample positively contradicted. Hash agreement does not establish source truth. |
| D260914-15 | Open data prerequisite / SSURGO | Latest discovered August 28 version has 481 z13 raw parts, no completion marker and no z0/5/9 objects. HTTP reports lane_never_written. | Soil survey source-direct/admission owners; raw remnants do not establish a completed publication. Keep the layer withheld pending the full contract. |
| D260914-16 | High / public-request disclosure, R03 | Session 3 found private-request promises in `src/app/community/page.tsx`. The reviewed Session 4 batch corrects metadata/hero, `CommunityLedger`, `RequestSubmitModal` consent, and `CommunityDetails` help to disclose public location/title/description, authenticated comment reads, and contributor write access; it also removes anonymous-commenting promises and clarifies proposal review visibility. | `/root/workspace_social_audit`; bounded source fix independently reviewed. Candidate 7's full frontend run had 2,511 passes and one stale disclosure-assertion failure. [Candidate 8](source-candidate-20260914-8.json) corrects only that assertion; independent review and all 11 focused file tests passed. [Session 4](runbook-session4-20260914.md) retains both attempts and the bounded browser evidence. No Candidate 8 full-suite pass, live/authenticated request acceptance, or full closure is claimed. |
| D260914-17 | Medium / fixture automation cleanup | Session 4 Chromium fixtures passed, but Next teardown required root intervention. Session 5 attempts 1–3 retain their separate inspection/cleanup failures. Attempts 4 (desktop) and 5 (mobile) each passed three tests and exited zero; receipts record only revalidated-owned-process stop/already-exited outcomes, no remaining or unverified owned processes, and port 3128 closed at 21:25:06 and 21:33:49 UTC respectively. | QA harness owner; corrected cleanup verified for these two bounded runs. Earlier failures remain evidence and are not retroactively clean passes; broader unattended reliability is not certified. See [Session 5](runbook-session5-20260914.md). |
| D260914-18 | High / clean database bootstrap | Session 5's Alembic baseline cleared `search_path`, causing the unqualified ledger insert to fail; its transaction rolled back. | Explicit `public` version-table schema implemented and independently reviewed. Real subsequent bootstrap committed revision `20260912_0000`; Candidate 11 passed the corresponding Python checks while retaining its separate historical-spool failure. Candidate 13 passes frontend/tooling/type/lint/boundary gates. Original failed bootstrap remains preserved; the full Python gate remains failed. |
| D260914-19 | High / clean database bootstrap | After successful Alembic bootstrap, Drizzle's baseline cleared `search_path` before the first unqualified `accounts` table creation in `0003_land_context.sql`; application migrations rolled back, leaving an empty ledger. | Shared public-schema boundaries independently reviewed across bootstrap/deployment/local runners. Actual pinned PostgreSQL resume passed, all six ledger hashes/timestamps match the original SQL packet, and deployment migration no-op left the snapshot unchanged. Candidate 13 passes 12 tooling tests, 192 frontend files / 2,512 tests and type/lint/boundary gates. Historical SQL and failed attempts remain preserved. |
| D260914-20 | Medium / intermittent local CAS verification | Candidate 9's full selected Python sweep failed one existing two-thread CAS test with a local-root containment error. A single 1,000-operation diagnostic did not reproduce it. | Reviewed Windows namespace correction and deterministic containment/security regressions passed in Candidate 11, including the original concurrent CAS test. The cause of the original intermittent failure remains unproven; preserve its failure and diagnostic without weakening security checks. The separate historical-spool failure keeps the full Python gate failed. See [Session 5](runbook-session5-20260914.md). |
| D260914-21 | Medium / Windows historical artifact verification | Candidate 10 failed a historical NASA directory replace; Candidate 11 passed that case but failed a separate historical spool directory rename, both with Windows access denied. | Root/QA investigation; shorter paths did not eliminate directory-rename failures. No cause or source fix inferred, no tests skipped, full Python gate remains failed. |
| D260914-22 | High / local application build boundary | Browser attempt 2 passed `/api/ready` but `/register` compilation failed while Tailwind/Turbopack scanned an inaccessible historical `.omc` pytest directory. The reviewed `source("../")` import now restricts class detection to `src`; attempt 3 completed registration and attempts 4/5 passed the bounded desktop/mobile lifecycle tests with rendered application controls. | Styles/build owner; source-boundary correction verified within these local development journeys. Original failure is preserved, no filesystem permissions were changed, and no full visual/style or production build acceptance is inferred. [Candidate 13](source-candidate-20260914-13.json) also passes the full frontend/type/lint/boundary checks. |
| D260914-23 | Medium / development map controls | Browser 3's screenshot shows the Next development badge covering Map manager; normal clicks were intercepted by `nextjs-portal`, with no runtime error panel shown. Supported `devIndicators: false` removes that badge while preserving compile/runtime error overlays. Attempts 4/5 passed normal desktop clicks and mobile touch taps on Map manager throughout both bounded journeys. | Development configuration correction verified for desktop and 390×844 mobile journeys; preserve the original collision evidence. No forced clicks, DOM removal, or error-overlay suppression. Broader visual/accessibility acceptance remains separate. See [Session 5](runbook-session5-20260914.md). |
| D260914-24 | Accessibility audit / community landmark | Browser 3's community snapshot renders the public disclosure but has no `main` element; `ApplicationShell` uses `div#application-content`. The harness incorrectly assumed a main element. | Correct the disclosure locator independently; retain a separate layout/landmark audit before choosing a wrapper change, avoiding nested main landmarks on other routes. No accessibility closure claimed. |
| D260914-25 | High / workspace route teardown; bounded fix verified | Browser attempt 6 created an unsent Point and switched panes, but client navigation to About raised `getSource` on a removed MapLibre style in `InterventionDrawControl`'s `draw.stop()` cleanup. The actual runtime overlay and stack remain retained. | Candidate 14 layout-effect cleanup precedes map removal; independently reviewed regressions cover workspace and standalone owners under StrictMode. Integrated frontend checks pass, including 2,514 Vitest tests. Actual desktop browser 7 and mobile browser 8 each pass identity plus unsent Point/Polygon navigation recovery and confirmed discard, with zero analysis/tRPC mutations and clean owned teardown. Physical-device, focus restoration and full accessibility acceptance remain open. See [Session 7](runbook-session7-20260914.md). |
| D260914-26 | High / unavailable layer date truthfulness; correction applied, verification open | Session 7 environmental browser 4 passes its bounded controls/response assertions, but independent final-frame/DOM inspection finds SSURGO typed unavailable and never published while its date status shows September 14 and the map summary lists it among four visible dated layers. Only weather, moisture and watersheds have served data. | Candidate 15 applies independently reviewed selected/served-date separation while preserving agent selection and retained/paused semantics. Its integrated sweep exposes eight climate/layer-manager fixture failures and a test-helper type error, now under correction. Actual unavailable-layer browser revalidation remains required. Cases PGQA-T01/T03/T05 and L10 remain open. See [Session 7](runbook-session7-20260914.md) and [Session 11](runbook-session11-20260914.md). |
| D260914-27 | High / stale moderation overwrites decision; correction applied, verification open | Session 9 normally submits a consented Point, expert publishes, then admin's stale Reject returns 200 and persists rejected with the second note. Fresh contributor read confirms reversal. | Candidate 15 adds the independently reviewed atomic pending-status guard, conflict feedback and queue refresh; moderation regressions pass within its otherwise failed integrated suite. Fresh consented stale-review browser proof remains required, preserving the original diagnostic row. PGQA-C11 simultaneous-transaction coverage remains separate. See [Session 9](runbook-session9-20260914.md) and [Session 11](runbook-session11-20260914.md). |
| D260914-28 | High / automatic QA artifact retains credential; bounded remediation verified | The rate-limited Session 9 login's automatic Playwright error context retained its password textbox value despite the sanitized thrown error and explicit screenshot restrictions. | Root sanitized the single occurrence with original/sanitized hash provenance and no unredacted copy. Independently reviewed safe auth cleanup and automatic ARIA snapshot suppression pass a dummy failure probe; the real likes-only run passes with zero exact known credentials in saved text/inline bodies. Ordinary error/source context remains; this is not a generic secret certificate. Product rate limits remain enabled. See [Session 9](runbook-session9-20260914.md) and its [independent review](independent-review-session9-20260914.md). |

Session 5 follow-up: D260914-18/19 have reviewed source fixes and actual fresh local bootstrap plus
deployment no-op evidence preserving all original migration hashes. D260914-20's reviewed namespace
correction and deterministic security regressions passed in Candidate 11, including the original CAS
test; the precise cause of the original intermittent failures remains unproven. D260914-21 keeps the
full Python gate failed. D260914-17's first three Session 5 attempts retain their inspection/cleanup
failures; later process exit or port closure did not convert them into clean-run passes. Attempt 4
and the separate mobile attempt 5 now supply the bounded successful cleanup evidence recorded above.

## September 15 first live smoke follow-up

The first production attempt `attempt-20260915-0025` retains four failures: the
product assertions completed, but final network guards rejected the injected
Cloudflare analytics script. This is not recorded as four passes. See the retained
[independent live review](../../../../.omc/research/runbook-20260914/release/live-smoke/independent-live1-review.md)
and its hashed local bindings; raw screenshots and response evidence remain local.

| ID | Severity / requirement | Evidence and observed behavior | Owner / disposition |
| --- | --- | --- | --- |
| D260915-29 | Medium / proposal-publication disclosure | Both live anonymous feed screenshots display an Access notice saying contributors consent to publication on PlantGeo, "not to the open internet." `src/app/feed/InterventionFeed.tsx` retains this sentence, while the community page and proposal consent explain that approved proposals become public. The anonymous pending-feed gate itself works. | Social/publication copy owner; clarify that the access restriction concerns proposals awaiting review. Open for a bounded copy correction and review; no authorization policy change inferred. |
| D260915-30 | Evidence gap / mobile weather and proposal-map visual readiness | First live mobile latest-weather screenshot is dark/blank despite a ready six-row September14 response and completed control/date assertions. A later historical frame shows the basemap. Both immediate proposal-entry screenshots have blank drawing canvases. | Live QA owner; no proven product rendering defect or drawing-map acceptance follows from these early frames. Retain the original images and obtain a separately reviewed bounded settling capture before classifying rendering. Desktop weather cells/labels and historical removal are visible; those observations do not fill the mobile/drawing gap. |

Second live attempt `attempt-20260915-0031` passes all four automated cases in
36.101 seconds with the exact analytics script intentionally blocked and accounted
for separately. Independent review of sixteen stage images resolves the early
blank **basemap and proposal-map entry** captures: the delayed mobile map shows the
basemap, and both proposal canvases show real imagery plus Point/Polygon/Clear
controls. No geometry interaction was exercised. D260915-30 remains open for
**settled weather overlay visibility on both desktop and mobile**: September14
responses contain 18/6 rows and the date summary is present, but the later images
show no weather cells or numeric labels. First-attempt desktop cells were visible.
This observation requires renderer/viewport investigation; it is not proof of a
specific product cause, and four passing control/data smoke cases do not accept
weather rendering. See the retained
[second independent live review](../../../../.omc/research/runbook-20260914/release/live-smoke/independent-live2-review.md).

Session 16 refines D260915-30 from an unclassified visual gap to a reproduced weather
installation defect on `d167e7f0231804827422f0788f3b0604393a8ba8`: weather remained
enabled without its source or four layers after global style readiness, through the
five-second post-response capture. A normal off/on installed them and produced visible
cells and labels. The original failed frames remain evidence; this bounded desktop
trace does not establish every earlier blank frame's cause.

The separately reviewed weather fix and D260915-29 disclosure correction are applied
locally. The integrated frontend suite (193 files / 2,545 tests plus 12 tooling tests),
type and boundary checks pass. Lint excluding `.omc` reports zero errors and 9,887
warnings; the unchanged Python receipt verifies. Both defects remain open for the
new deployment and post-fix live checks. The approved harness requires first-enable
weather rendering, missing-day clearing and Latest restoration before any recovery
toggle, and the corrected anonymous feed disclosure on desktop and emulated mobile.
See [Session 16](runbook-session16-20260915.md) for exact source and evidence bindings.

After deployment of `286eb91b62aeb2a53c8484184d5adec1336f5c82`, the four approved
desktop/emulated-mobile cases passed in 47.396 seconds with no retries or skips.
Independent review of the six weather map stages, six control stages and both feed
screenshots confirms the corrected disclosure and visible first-enable weather,
missing-day clearing and Latest restoration. Renderer source/rendered counts are
26/16 -> 0/0 -> 26/16 on desktop and 8/8 -> 0/0 -> 8/8 on mobile, with all four
layers present and no recovery toggle. These are renderer counts, not conservation.

**D260915-29 is resolved for the disclosure correction**: both paragraphs are source
reviewed; the anonymous paragraph is verified live on both viewports. Authenticated
feed workflow acceptance remains separate. **D260915-30 is resolved for the reproduced
weather installation/visibility defect** and its bounded missing-day/Latest regression.
Earlier proposal-map entry captures were already resolved as described above; geometry
interaction and full map/workspace acceptance remain open. Original failures are retained.
Four aborted asset requests remain in the raw evidence; no HTTP errors, page errors or
blocked product requests were captured. No whole formal QA case or plan checkbox is closed.

## Session 17 — Fire and Water admission

| ID | Severity / requirement | Evidence and observed behavior | Owner / disposition |
| --- | --- | --- | --- |
| D260915-31 | Rendering lifecycle / PGQA-L01, PGQA-L03, PGQA-M12 | Source inspection on `286eb91b` shows Fire/Water initial installation waiting for all-source isStyleLoaded after style.load may already have fired. The hook observes styledata, while tile completion emits sourcedata; existing data effects cannot create the skipped sources. Earlier tests encoded the wrong completion event. This is a concrete source path, not yet a newly reproduced live Fire/Water failure. | `/root/qa_inventory` authored the four-file correction; `/root/workspace_social_audit` independently approved it. Root applied exact bytes and froze 1,751 source files. Integrated checks and current-data live discovery are in progress. No layer or whole-case closure. |

See [Session 17](runbook-session17-20260915.md) for ownership, candidate identity and
actual results as they arrive. Reserved empty groundwater remains unpublished; mounting
its empty source does not satisfy populated-well or groundwater-data acceptance.

Independent review of the first actual discovery now confirms the Water half of
D260915-31 on predecessor `286eb91b`: 26 ready aggregate rows, enabled Water controls,
globally ready parsed style, but all three sources and four layers absent. Fire's empty
September 13 northern viewport is not classified as an installation failure. A broader
Fire discovery and two Water detail searches subsequently found real populated locations.
Independent review of that second discovery additionally confirms missing installation
in the populated refined Fire viewport and both populated Water detail viewports, with
sources absent at globally ready snapshots. Broad Fire does show a visible cell. The
defect concerns the missed lifecycle path, not a claim that Fire never renders.

The final local Candidate 2 passes 193 files / 2,550 tests and 12 tooling tests, with
type/boundary/zero-error lint and unchanged Python receipt verification passing. Candidate 1's
single outdated test expectation and its full failed sweep remain retained. D260915-31

## Session 17 terminal outcome — deployment and live regression

`ec172e881e4aa640231ae073a1d04408fd05ad5a` deployed: frontend `69fbe03b-3c2e-452d-aa9d-0eaa6d28b9d6`
SUCCESS at 2026-09-15T02:24:14.199Z, Martin `de34dea0-dccb-4916-b251-4e1e8c01e8ec` SUCCESS at
02:19:25.401Z. Data-API and job-executor deployments are SKIPPED for unchanged scope and retain their
successful `d167e7f0` revision. Production build recorded 193 files / 2,550 tests plus 12 tooling
tests, successful compilation, up-to-date migrations and readiness.

The approved eight-case live regression at `live-regression/attempt-20260915-0224` produced
**4 passes, 4 failures, 0 skips, 0 retries in 127.490632 seconds**; report SHA-256
`e4f3f16959f575e25b7068c7026dcf8e769d3d7b14dc71f470bdc404bc0b3a25`; root terminal exited 1.
All four Fire desktop/mobile aggregate/detail journeys passed. All four Water journeys passed
their native first-enable source/layer installation and populated rendering assertions — the
Session 17 correction's own subject matter was exercised and held — then failed at
`regression.browser.ts` line 63, which asserted September 6 was a missing day. September 6 was
in fact ready, with matching selected/served/observed day and positive data: 122 aggregate rows /
1 detail row desktop, 62 aggregate rows / 1 detail row mobile. The missing-day and Latest-restoration
portions of those journeys were never reached and remain unexercised, not failed-on-the-merits.

**D260915-32** | Harness / live-regression anchor selection | The approved eight-case harness
selected September 6 as a Water missing-day candidate; the currently retained Water capabilities
advertise September 6 as the *only* coverage gap, with no governed absences, `coverageGapsTruncated=false`,
and zero additional eligible gap dates in this capture — but the requested rung was in fact ready
and populated at that date, so the harness's coverage-gap read did not match served reality. This is
a harness anchor defect, not a product defect: the retained Water correction's first-enable and
rendering assertions held. A discovery limitation (no eligible current gap candidate in this
capture) is not missing-day acceptance; the harness must never manufacture a missing date, use an
arbitrary future day, or prompt alteration of governed publication to satisfy this test. |
`/root` owns live execution and this record; a separately reviewed capability-discovery pass for
new gap candidates is needed before the missing-day/Latest-restoration Water journeys can be
re-exercised. Not closed.

**D260915-31 status update**: deployed and bounded-live-exercised, **not closed**. The bounded
correction's first-enable/rendering subject matter passed for both Fire and Water on `ec172e88`.
The missing-day clearing and Latest-restoration behavior remain unexercised for Water (blocked by
D260915-32, not by the correction itself). Root viewed four images total — Fire desktop aggregate
first-enable, Fire mobile aggregate missing-day, Water mobile detail first-enable, Water desktop
aggregate first-enable — and a complete independent result/image review across all eight cases is
still pending. Groundwater remains explicitly unpublished and returns an empty array; source
installation does not establish populated-well or groundwater acceptance, and stays separate from
this Water-gauge result. Fire perimeters and burn severity remain separate toggles/readers and
distinct full-runbook obligations. No whole QA case or runbook checklist item is promoted; the
formal matrix remains at its existing 0/220 whole-case state.
stays open until the new revision is deployed and its bounded live renderer checks pass.
