# Panels

Evidence checks retain requested dates separately from the reader's served dataset dates.
For a static snapshot the dataset date is its publication/release day, not a measurement at the
requested day. Never derive either date by truncating or localizing an observation timestamp.

## Regional evidence release labels

MTBS `publication_available_YYYY-MM-DD` is validated only for the MTBS source and
labeled as publication availability. It participates in the existing source-age
classification without being relabeled as ignition or source capture time. Invalid
calendar days and future availability remain unavailable.

The report's Sources disclosure recognizes `static_release_untimed` only for soil properties and
shows it as a "Static layer" row (the old panel-level freshness footer was folded into Sources
on 2026-10-04). Availability does not imply a recent observation. Fire perimeter
`snapshot_captured_YYYY-MM-DD` markers display their validated capture day, not a measurement
time, and retain the existing fourteen-day staleness limit. Invalid, future, and source-mismatched
markers remain unavailable. Keep these labels aligned with `src/lib/regional-intelligence.ts`.
Drought freshness retains its age checks but displays the publisher's calendar release day.
Its synthetic UTC midnight is not localized into the previous evening in western timezones.

## Regional report view-model

**2026-10-04, owner constraint: minimise UI complexity on the agent panel.** The report renders
from one view-model, `src/lib/regional-evidence-presentation.ts` (`buildReportView`), and the
Markdown export (`reportToMarkdown` → `reportViewToMarkdown`) reads that same view, never the raw
response. Row and finding lines go through shared formatters (`sourceRowSummary`, finding `meta`),
so the screen and the export cannot drift; a test asserts they are equal.

Visible by default: risk + headline, up to three findings ("value · day", with "nearest day, N d
earlier" / "nearest cell, N km" only when the evidence check carries `dayOffset` /
`cellDistanceKm`), up to three recommendations (title · timeframe + rationale), one consult line
derived from the recommendations' disciplines with a correct a/an, one footer block, one Export
menu (copy, Markdown, JSON). The footer block IS `AI_GENERATED_DISCLAIMER`, verbatim, once per
report, in small muted text (gray-600 / dark gray-400, both WCAG AA at 11 px): agri
agent/AGENTS.md calls that sentence legally load-bearing, so it is never paraphrased or shortened.
It replaced the older "values are published estimates, not measurements" note, which was wrong
for station observations. Everything else lives in ONE collapsed Sources disclosure:

- One row per lane. Local and history passes for the same source merge; repeated
  strategy-knowledge lookups merge into "Strategy literature ×N". The row shows the BEST status
  among its calls, limited to Found / Nearest day / Nearest cell / Static layer / Not published /
  Error. `governed_absence` is a real answer and reads Found; `answered_no_records` reads Not
  published but is not a gap. `not_queried` calls are not rows.
- Raw scope (stage, requested day, window, zoom to 1 dp, coordinates to 4 dp) only inside the
  row's expand.
- Gaps: one line per lane — the first `"<source>: …"` limitation the server wrote for that lane
  (`regionalLaneLimitation`; an optional `" [<read id>]"` after the source, from older saved
  reports, pins it to that read's lane), else the first failed-call reason (which is also a row
  detail); further attributed limitations become row details. A flow test feeds the workflow's
  real output into `buildReportView`. Unattributed limitations are deduplicated into Caveats. A gap on a row whose status is
  Not published or Error makes the report "partial" and shows one "Some sources unavailable — see
  Sources" note; a lane line on an answered row (static layer, nearest cell, governed absence) is a
  gap note only, or every report would read partial once the server's lane lines were filed.
- Initial-context `dataFreshness` entries merge into the matching lane (camelCase key →
  kebab lane id) or become their own row. Deferred model placeholders (`strategyRecommendations`,
  `carbonPotential`) stay hidden while they only say unavailable/pending.
- Literature citations (server-written, https links only) sit under the literature row.

Removed on purpose: per-item origin chips ("AI inference" etc.), strategy chips, AI confidence,
risk factor chips, per-claim "Cited evidence:" restatements, per-bubble copy/share toolbars, the
panel-level freshness footer and the duplicate input-area disclaimer. The Markdown export ends
with the same verbatim `AI_GENERATED_DISCLAIMER` footer, so it travels with the export too.

`buildReportView` tolerates an unknown risk level, missing arrays and a null `dataFreshness`;
it throws `MalformedReportError` only when there is no headline to draw. `RegionalIntelligenceReport`
wraps each report in `ReportErrorBoundary`, the panel has its own boundary, and `src/app/error.tsx`
is the route-level fallback.

## Narrow dock controls and status copy

The shared tab list wraps when its labels do not fit the dock. Tabs retain enough intrinsic
width for their icons and labels, and the list grows in height rather than clipping a fixed
row. Keep one opacity control on each layer row; details sections must not duplicate it.

Water status messages state the available data, selected day, or action the reader can take.
Keep the distinction between a confirmed absence, an unpublished day, a partial result, and
a request failure. Storage engines, internal endpoints, fallback policy, and row budgets belong
in diagnostics and directory documentation, not the normal panel flow.

## These are dock sections, not panels

**2026-08-08.** Every `*Details.tsx` in this directory is the body of one section of the map's
left-edge manager (`src/components/map/layer-panel/`), rendered by `DockDetails.tsx` and mounted
only while that section is expanded. None of them owns a `Sheet`, an `open` prop, a close
button or a layer switch any more — the manager's shell, its disclosure state and its layer rows
own those. The rationale for the merge, and for each thing it deleted, is in
`src/components/map/AGENTS.md` §"One manager, no floating surfaces"; the rule that matters when
editing one of these files is short:

- **Mounted means open.** Do not add an `open` prop or an `enabled: open && …` gate back. A
  collapsed section is unmounted, so a query written here runs exactly when the reader is
  looking at it. The same rule is why no keyboard shortcut may be registered from a section —
  see `MapKeyboardShortcuts`.
- **No scroll container.** The manager's body is the one scroller (`panel-scroll.ts` rule 2).
  A `max-h-*` + `overflow-y-auto` wrapper in here is a second scrollbar inside the first. Its
  scrollbar is unpainted (`scrollbar-hidden`), which is a paint decision only: every scroll
  gesture and key still reaches it, so nothing in here needs to compensate.
- **No layer switch.** `map-store.activeLayers` is written by the manager's `LayerRow` eyes.
  A switch here would be a second control over one value, and half of it would be out of sight.
- **No render-mode control either.** Basemap, terrain, projection and tilt are the View
  section's, and it deliberately owns no layer switch; putting one on either side of that line
  in a report re-opens the "render mode never touches a layer" rule.

`ContributionQueue`, `LayerUpload` and `UserPanel` are not manager sections; they are mounted by
routes.

### The workspace is the one named exception

**2026-09-13** (`conductor/tracks/ai_intervention_workspace_20260913/`). `RegionalIntelligencePanel`
and `InterventionSubmitModal` are no longer two independent floating surfaces the dock convention
merely tolerates. They are the two modes of ONE surface,
`src/components/map/AiInterventionWorkspace.tsx` — a right-edge shell `MapView` mounts while a
location action is live, with a two-way switch ("AI analysis" / "Propose intervention"), its own
close, and a confirmation before a close that would discard any unfinished proposal fields or an
in-flight analysis. OQ-2 of that track resolved this deliberately as a second control surface
rather than a third dock section: the 304px dock is built for compact controls, and a chat
transcript plus an embedded drawing map are neither.

The exception is narrow, and these are its terms:

- **Both modes stay mounted.** The inactive one is hidden with the `hidden` attribute, never
  conditionally rendered (OQ-1(a), true keep-alive). An unmount would destroy an in-flight stream
  and, once the embedded MapLibre draw instance lives here, the terra-draw session with it.
- **Mode-switch is not close.** Switching modes never calls `onClose` and never resets either
  store. `regional-intelligence-store`'s `hidePanel`/`showPanel` exist for the visibility half;
  `closePanel` still means "the user is done" and still clears.
- **Analysis entry still requires consent.** A proposal-first workspace shows the shared
  `AgentInteraction` precision and Send controls inside its AI pane. Cancel returns to the
  proposal. Starting analysis preserves the draft and map, and uses the proposal's saved
  location even if a later map click targeted somewhere else. The AI pane names the rounded
  coordinates actually sent; opening a pane alone never starts a request.
- **A deliberate close discards both sessions.** Escape and the close button share the same
  confirmation. Declining preserves the draft and stream; confirming clears the draft and
  closes the AI session. Escape inside another dialog must not close the workspace beneath it.
- **New location actions preserve unfinished fields.** A draft counts as unfinished before its
  first geometry if its form fields differ from defaults. New actions select their requested
  pane, but cannot reset that draft or its original coordinates. The drawing map stays alive
  between tabs and resizes after its hidden container becomes visible.
- **Navigation restores saved drawing values.** The proposal form passes the draft geometry to
  the drawing control for initial recovery on a fresh map. Restoration errors remain visible
  and block submission without clearing the draft; a successful recovery clears a prior error.
  A deliberate close still discards the draft. No storage beyond the existing in-memory store
  is introduced, and undo history belongs to the live instance only.
- **It owns nothing the dock owns.** The workspace's compact layer strip is a second VIEW over
  `map-store.activeLayers`, never a second copy of it: it writes through the same `useToggleLayer`
  the dock's `LayerRow` eye uses, so it cannot drift (OQ-3). Adding any state of its own here —
  render mode, opacity, a per-layer date — re-opens exactly the defect the dock convention exists
  to prevent, and is not permitted.
- **It does not touch `LayerPanel`.** The dock's open/closed state and disclosure state are
  untouched by opening, closing or switching the workspace, and vice versa (FR-5). Right edge for
  the workspace, left edge for the dock, bottom-left for `ManagerRail`.

`src/components/search/` kept only `ReverseGeocode` after 2026-08-09: `SearchBar`,
`SearchResults`, `RecentSearches` and `CommandPalette` became
`src/components/map/layer-panel/SearchDockSection.tsx`. `ReverseGeocode` stays because it is not
a control surface — it is a right-click/long-press popup anchored to a point on the canvas.

## ContributionQueue is mounted by a route, not a panel

`ContributionQueue` renders the expert moderation queue for community-submitted
interventions (`contributions.listPendingReview` / `publishContribution` /
`rejectContribution`, all `expertProcedure`). It is mounted at
`src/app/moderation/page.tsx`, not by `PanelManager`
(`src/components/map/PanelManager.tsx`).

Two reasons, both of which survived the 2026-08-08 merge that turned the panels
into dock sections:

- None of the dock's eight sections is role-gated. Moderation is the first
  surface in this app that only some signed-in users may see; folding it in
  would mean either gating the whole dock on role (touching seven sections that
  don't need it) or leaving one gated section among seven ungated ones, which is
  easy to get wrong later.
- A moderation queue is not map-adjacent work. The dock exists to stay in view
  while a reader keeps working the map underneath. There's no map context a
  reviewer needs while approving or rejecting a submission, so fighting the map
  for screen real estate buys nothing.

The role gate lives on the route (`src/app/moderation/page.tsx`), which reads
the session server-side and redirects before rendering anything — a non-expert
hitting the URL directly gets sent to `/`, not a broken or empty shell. That
gate is presentation only. `contributionsRouter`'s `expertProcedure`
(`src/lib/server/trpc/init.ts`) is the actual authority and is re-checked on
every query and mutation `ContributionQueue` (and `ModerationPanel`) makes,
independent of how it got mounted.

`rejectContribution` writes a `reviewNote` that the submitter later sees on
their own rejected recommendation (`CommunityDetails`'s
`listMySubmissions` rendering). A rejection with no note is not actionable by
whoever submitted it, so the queue disables the Reject button until the
reviewer types one — this is enforced client-side only, as a UX nudge; the
router's `reviewNote` field stays optional server-side.

## Moderation — one review queue, one lifecycle manager (resolved 2026-10-26)

`/moderation` (`src/app/moderation/page.tsx`, expert/admin server gate) mounts two surfaces with
disjoint jobs, so there is never a second queue over the same rows:

| Surface | Reads | Writes | Owns |
|---|---|---|---|
| `ContributionQueue` | `contributions.listPendingReview` | `publishContribution` / `rejectContribution` | `features.status`: `pending_review` -> `published` / `rejected` |
| `ModerationPanel` | `interventions.listLifecycle` | `interventions.transitionLifecycleState` | `properties.lifecycleStage`: `published` -> `active` -> `monitored` |

"Approve & publish" lives only in `ContributionQueue` and writes `status='published'`, the one value
`geo.intervention_tiles` serves. Before 2026-10-26 `ModerationPanel` had its own "Approve & Publish"
over `interventions.listProposed` that called `castModerationVote` and wrote `status='approved'` —
published nothing (2026-09-13 correction). That vote path, and the uncalled `proposeIntervention`,
were deleted; `listProposed` stays because `/feed` and the drafts overlay read it.

**Why the lifecycle is a property, not a status.** Writing `features.status='active'` would drop the
row off `geo.intervention_tiles`, `getInterventionDetail`'s public arm and `wildfire.getInterventions`
(all `status='published'`). So `status` stays `published` and the stage lives in
`properties.lifecycleStage` (absent = `published`). `contributions.submitObservation` reserves the key.

**State machine** (`INTERVENTION_LIFECYCLE_TRANSITIONS` in `routers/interventions.ts`), forward-only:
`published -> active -> monitored`. The client sends `fromStage` (what it saw) and `toStage`:
- illegal pair -> `BAD_REQUEST`, before any query;
- the UPDATE is conditioned on `status='published'`, not a strategy request, and
  `coalesce(lifecycleStage,'published') = fromStage` (optimistic concurrency) -> two racing
  moderators get one transition and one `CONFLICT`; a missing row is `NOT_FOUND`.
`listLifecycle` returns `nextStages` from the same map, so the panel renders only legal moves and
never mirrors the vocabulary. Both procedures are `expertProcedure` (same expert/admin set as before).

**Effect evidence.** The panel used to render an invented `tau_est` (2026-09-02). It now shows no
effect number at all; do not add one from any source that cannot name the evaluation that produced
it. Rows written with the old `causalTauEst: 0.15` default are still suspect.

Known gaps: the machine-ingress upsert (`intervention-store.ts`) spreads partner `properties`, so a
re-ingest can overwrite or reset `lifecycleStage`; no lifecycle history/audit row is kept; no backward
move (e.g. `monitored -> active`) exists. `getInterventionDetail` does not surface the stage yet.
Pinned by `src/__tests__/trpc/interventions.test.ts` ("interventions lifecycle transitions") and
`src/__tests__/components/ModerationPanel.test.tsx`.

## Published analysis controls — 2026-09-10

`RegionalIntelligenceReport` is the shared presentation for a parsed live report
and a validated saved report. The saved-conversation server boundary validates its
historical JSON before calling it; exporting a stored report is a browser action
and does not rerun the model or mutate the saved conversation.

VegetationDetails presents measured/satellite NDVI controls directly. Forecast and empty land-cover
tabs are deferred, and opacity is controlled once on the layer row. SoilDetails keeps soil-property
point reads and observed soil layers; unpublished erosion/carbon-effect tabs no longer mount or
issue suitability requests. Organic carbon is still a measured property. Backend implementation
is retained; restoration criteria are in the Parquet pivot track's deferred analysis UI section.

## Community publishing route restored — 2026-09-10

Current public requests publish their location, title, and description without review. Request
consent must disclose that signed-out readers can read those fields. Comments/like-state reads
require sign-in; request, comment, and like writes require contributor access. Recommendation
copy distinguishes visibility to signed-in readers during review from public publication after
approval. The complete request disclosure contract lives in `src/app/community/AGENTS.md`.

The map's intervention caption claimed nothing invoked publishing, while `publishContribution`
remained callable and `ContributionQueue` had its approve/reject controls. The queue had been
replaced at `/moderation` by a different `ModerationPanel`, making the community publisher
unreachable from that route. Both now mount under the existing expert/admin server role gate.
Their backend workflows remain separate and unchanged. Community forms, submission history and
intervention map rendering remain supported; captions state review/publication and privacy rules
without describing internal database systems or asserting the map must always be empty.

**Resolved, 2026-10-26.** The 2026-09-13 unmount (two queues, one of which published nothing) is
superseded: see "Moderation — one review queue, one lifecycle manager" above.

## Private chat controls (2026-09-10)

The regional panel links to owned chat history and the current saved conversation.
New chat resets the local transcript at the same location without an automatic request.
Copy/share controls act only on an explicit click: native share receives report or
transcript text, never a public conversation URL; its fallback copies text for the
user to distribute. Exports and historical reports retain source citations and dates.
AI confidence labels explicitly distinguish model assessment from measured success.

MessageFeedback appears for persisted assistant IDs only. Its protected mutation
owns authorization and idempotent storage; the UI does not fabricate ratings or mutate
report evidence. History resumes from ownership-checked server messages. Activity
lists actual request/context/search/saved/completed/error events in this browser
session; it is not a reconstructed model reasoning trace or a persistent audit log.

## Recorded evidence review (2026-09-12)

The optional `analysisEvidence` field is a server-produced tool ledger, attached after
model report validation and preserved on saved reports and exports. The report tool cannot
author it. The disclosure presents recorded stages and the source, day, location, refusal,
confirmed absence or missing query of each check. A completed stage means its checks ran,
not that every source had evidence or that an intervention was validated. Missing historical
audit fields stay missing; the renderer never reconstructs checks from claim citations.

The freshness footer is labeled "Initial context sources" because its legacy roster is
only the initial context subset. "No dated evidence in initial context" neither means the
source was queried nor that there was a confirmed absence. Additional governed tool reads
belong in the evidence review with their requested days and comparison locations. Streaming
audit state is reset between requests and locations; completed reports retain their own audit.

Claims can carry up to eight `evidenceReadIds` pointing to that report's recorded reads.
Render the resolved stage, source, requested and actual dates, and coordinates directly beside
the risk summary, observation, or recommendation and retain that association in Markdown.
An unknown reference or missing historical ledger does not become a displayed raw ID or an
invented evidence scope. JSON exports retain the original references and their recorded audit.

## Stale contribution review decisions

The pending queue's Approve and Reject controls do not override an earlier reviewer. Server
CONFLICT means no decision was applied; refresh the queue and show that explanation even if
the refresh removes the last row. Other mutation errors must remain visible and must not be
presented as success. Keep an unsent rejection note until a successful rejection. The server
owns the atomic pending-status guard; client invalidation is feedback, not concurrency control.
A failed query refresh must retain any mutation explanation. Only explicit FORBIDDEN or
UNAUTHORIZED errors are described as access denial; other query errors say the queue could
not load. A failed refresh must not clear the unsent review note from component state.


## Land context boundary and office provenance (2026-09-20)

The selected surface record remains the first evidence entry when public-office contacts load.
Office identities come from intersected jurisdiction polygons, not from surface feature keys.
Dissolved BLM management areas have no source-native parcel identifier and must not display a
fabricated one. Route scope retains the source's documented meaning and geographic limitations.

The Relevant parties section reports contact loading, transport failures, bounded refusals and
the reader's non-matched coverage explanations independently of the selected boundary evidence.
An unsuccessful office read must not become an empty-office claim. Stale contacts are discarded
after a failed query, while the boundary's source record remains visible.

The panel selects the stored result metadata before deriving optional coverage notices. Returning
a newly allocated empty array from a Zustand selector makes an absent-metadata snapshot unstable
and can loop React updates. The real-store panel tests deliberately retain absent metadata.

## Historical weather transport failures

`WeatherHistoryReport` withholds readings and its map-marking action on a query error, even
when a failed refetch retains an earlier ready result in the query cache (React Query v5 keeps
the last successful `data` by default). Its unavailable notice must not accompany cached values.

It also withholds a governed absence whose `servedDay` does not match `requestedDay` -- weather is
a window-policy reader, so a governed-absence result can describe a wider unpublished window than
the single day it was requested for, and presenting that as a same-day "confirmed empty" answer
would misreport a day nobody actually checked. Treated the same as a transport error: retry stays
offered.

Gate readings on the debounced `settledDate` `useDebouncedLayerDay("weather")` returns, not on a
second, raw layer-day subscription. The query's `requestDate` is itself derived from `settledDate`,
so comparing the response to that same value keeps the day label and the data it describes in sync
through a scrub; comparing against the raw, undebounced selection instead can desync the two for
the length of the settle window.

## The 44px mobile floor reaches these bodies too — 2026-10-10

Until this date the tap-target floor was a fact about the dock's **chrome** only. `max-sm:min-h-11`
was applied across `src/components/map/layer-panel/**`, `dock-disclosure.ts:33` called it "the 44px
mobile tap target every control on this map holds to", and `MapManagerMobileLayout.test.tsx`
asserted it on three sections — while a grep for `max-sm:` across all 33 files of this directory
returned **zero**. The layer tree held the floor and the reports it mounts did not: the soil
property chips were `px-2.5 py-1 text-xs`, about 26px tall.

A render test could not have caught that, and this is the load-bearing lesson. It covers the
controls its own mocks expose, so a control in an unmocked state — or a control added next
month — is invisible to it. Enforcement is therefore
`src/__tests__/components/mobile-touch-target-contract.test.ts`, which **scans the source** of
this directory for raw `<button>`, `<input>` and `<select>`, plus
`role="switch"|"tab"|"checkbox"|"radio"`, and fails on any that carries neither the floor nor a
stated exemption. `<textarea>` is deliberately NOT size-scanned — its HTML default is `rows=2`,
so it always clears 44px and the floor on it is a no-op — but it IS covered by the suite's
separate `NO_IOS_FOCUS_ZOOM` assertion. Adding a panel costs no harness; adding a control without the floor fails.

Rules when editing a body in here:

- **Import the contract, don't retype it.** `TOUCH_TARGET`, `TOUCH_TARGET_SQUARE` and
  `NO_IOS_FOCUS_ZOOM` live in `src/components/ui/touch-target.ts` with their rationale.
- **`<Button>`, `<Input>`, `<Select>` and `<TabsTrigger>` already carry it** in their base
  classes, so adding it at those call sites is noise. The floor is for **raw** elements.
- **Never add `max-sm:grid-cols-1` in here.** The dock is `w-[19rem]` (304px) on the desktop and
  `max-sm:w-full` on a phone, and a phone viewport at 320px and up is wider than 304px — **a body
  in this directory has at least as much horizontal room on a phone as on a laptop.** Collapsing its columns makes the phone
  layout worse than the desktop layout it was copied from. Column collapses belong only where the
  container is the viewport: the dashboard, the admin tables, `src/app/**`.
- **`min-height` does nothing to a `display: inline` box** (CSS 2.1 §10.5). The floor is a no-op
  on a citation link sitting mid-sentence, and giving one `inline-block` to make it apply reflows
  the paragraph around it. Leave those links and record the reason.
- **Dense data type stays dense.** The owner's 2026-10-10 decision was "fix targets only": the
  10px/11px lines carrying units, cell sizes, source releases and model-estimate provenance keep
  their size on every viewport. The sole font-size exception is `NO_IOS_FOCUS_ZOOM` on a native
  field, because mobile Safari zooms the whole document on focus of a field under 16px and does
  not zoom back out — which on a full-bleed map strands the reader with the dock off screen.
- **A checkbox or radio is floored by its `<label>`, never by the box.** A press anywhere on the
  wrapping label toggles the control natively, so the label — which carries the text and the
  padding — is the tap surface. `TOUCH_TARGET_SQUARE` on the input itself paints a 44px checkbox.
  The contract test exempts `type="checkbox"`/`type="radio"` for this reason, and that exemption
  is a stated blind spot: a regex cannot prove the enclosing label is large enough, so review it
  by eye.
- **Adding the floor is never a reason to remove one.** Three buttons in `WeatherHistoryReport`
  already carried an unconditional `min-h-11`; rescoping those to `max-sm:` would lower them to
  26px for desktop touchscreens, which is this pass running backwards.

Removed 2026-10-25: `UserPanel.tsx` -- zero callers and its password-change handler posted to a route (`/api/auth/password`) that does not exist (dead-code audit).
Removed 2026-10-25: `LayerUpload.tsx` and the `layers.create` mutation it was the only caller of -- zero callers (dead-code audit).

## Alerts panel (2026-10-26 rejoin)

`AlertsDetails.tsx` is the map dock's Alerts section: the caller's own unread alerts (mark one or
all read), the locations they watch (add the current map centre or a clicked point, same
`useMapQueryPoint` arming `SoilDetails` uses — see `src/components/map/AGENTS.md` "Picking a
point to query"), and per-location/per-type email + in-app subscription toggles. It is the
front end for `alertsRouter`, which was already real CRUD with zero UI callers before this
change — `src/lib/server/jobs/alert-dispatcher.ts` produces the rows every 30 minutes by checking
fire proximity, drought escalation and critically low streamflow against each watched location,
and also publishes to the SSE channel `alerts:${userId}` that `/api/stream/[layerId]` serves;
this panel does not open that stream itself, since `getAlerts`/`getUnreadCount` already refetch on
their own interval and a live feed is a reasonable follow-up rather than required for a first cut.

**Every query is `protectedProcedure`, so a signed-out reader gets a sign-in prompt and issues no
query at all** — `enabled: authenticated` from `useSession()`, the same contract
`InterventionLikeButton` holds for `interventionSocial.getLikeState`. The unread count repeats as
a small badge on the section's own collapsed row (`DetailsSection`'s re-added `badge` prop,
wired only for `"alerts"` in `DockSections.tsx`'s `UnreadAlertsBadge`), because a reader should
see there is something waiting before they expand the section, not only after.

**`getSubscriptions` is a new, narrow read**, added alongside the panel rather than guessed
around: `alertsRouter` had `addWatchedLocation`/`updateSubscription` (an upsert) but nothing to
read a location's EXISTING subscription rows, so a toggle UI would otherwise either fabricate a
"saved" state or ask the reader to re-decide settings they already set. It returns the caller's
own `alertSubscriptions` rows for one `locationId`, gated the same way as every other procedure
here.

**Deferred, named rather than silently dropped**: deleting a watched location (the router has no
`deleteWatchedLocation`); a live SSE subscription feeding the badge/list without a manual
refetch; and reconciling the shared `queryPoint` between this section and `SoilDetails` if both
are expanded at once — both arm `useMapQueryPoint(map, true)` independently, which is the
existing multi-consumer design the hook was built for, but a click while both sections are open
sets the one shared pin for both rather than asking which panel it was meant for.
