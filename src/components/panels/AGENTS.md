# Panels

Evidence checks retain requested dates separately from the reader's served dataset dates.
For a static snapshot the dataset date is its publication/release day, not a measurement at the
requested day. Never derive either date by truncating or localizing an observation timestamp.

## Regional evidence release labels

MTBS `publication_available_YYYY-MM-DD` is validated only for the MTBS source and
labeled as publication availability. It participates in the existing source-age
classification without being relabeled as ignition or source capture time. Invalid
calendar days and future availability remain unavailable.

The AI footer recognizes `static_release_untimed` only for soil properties and displays
"Static release (undated)". Availability does not imply a recent observation. Fire perimeter
`snapshot_captured_YYYY-MM-DD` markers display their validated capture day, not a measurement
time, and retain the existing fourteen-day staleness limit. Invalid, future, and source-mismatched
markers remain unavailable. Keep these labels aligned with `src/lib/regional-intelligence.ts`.
Drought freshness retains its age checks but displays the publisher's calendar release day.
Its synthetic UTC midnight is not localized into the previous evening in western timezones.

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
every query and mutation `ContributionQueue` makes, independent of how it got
mounted.

`rejectContribution` writes a `reviewNote` that the submitter later sees on
their own rejected recommendation (`CommunityDetails`'s
`listMySubmissions` rendering). A rejection with no note is not actionable by
whoever submitted it, so the queue disables the Reject button until the
reviewer types one — this is enforced client-side only, as a UX nudge; the
router's `reviewNote` field stays optional server-side.

## ModerationPanel states an absent estimate; it never fabricates one

**2026-09-02.** `ModerationPanel` (mounted by `src/app/moderation/page.tsx`, which renders it and
not `ContributionQueue`) used to render a "Causal Benefit Score (tau_est)" card reading
`+18% [11%, 25%]` beside Approve & Publish / Reject / Set ACTIVE. Those three numbers were
literals assigned inside the `.map()` under the comment "Simulated ML causal benefit score" — no
evaluated result, no provenance, and `interventions.listProposed` returns no effect field at all.
A moderator publishing to the public map was reading an invented benefit as if it were evidence.

The rule that replaced it: **absence is a rendered value, not a gap and not a stand-in.** The card
is now `EffectEvidenceNotice`, driven by a typed `EffectEvidence` discriminated union whose only
member today is `{ kind: "unavailable", reason: "no_evaluated_estimate" }`. It is deliberately
zinc, not emerald — a moderator must not read the notice as a positive signal. When a real
estimate exists it arrives as a new union member with its own provenance and evaluation window,
and the `if (evidence.kind === "unavailable")` branch stops being the only one; no tRPC field is
wired for that yet, so nothing here reads server state it cannot justify.

Do not re-add a number, a bar, a range or a percentage to this panel from any source that cannot
name the evaluation that produced it. `interventions.proposeIntervention` used to default
`causalTauEst: input.causalTauEst ?? 0.15` on submission; that default was removed on 2026-09-02,
so a proposal now carries an estimate only when a caller supplied one. **Rows already written with
the invented `0.15` are still in the table and are still suspect** -- the removal stops new ones,
it does not clean up old ones, and this panel may render neither.
Pinned by `src/__tests__/components/ModerationPanel.test.tsx`.

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

**Correction, 2026-09-13.** `ModerationPanel` was unmounted from `/moderation` again. Its "Approve
& Publish" button calls `interventions.castModerationVote`, which sets `status="approved"`, not
`"published"` — only `contributions.publishContribution` (called by `ContributionQueue`) does
that, and Martin's `geo.intervention_tiles` only serves `status='published'` rows. With both
panels mounted, a reviewer had two queues on one page and picking `ModerationPanel` silently
published nothing. `ContributionQueue` is the sole reachable moderation surface again; the
component and its pinned test above are untouched.

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
