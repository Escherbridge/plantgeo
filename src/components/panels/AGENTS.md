# Panels

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

`RegionalIntelligencePanel`, `ContributionQueue`, `LayerUpload`, `UserPanel` and the two submit
modals are not manager sections; they are mounted by routes or by other components.

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
name the evaluation that produced it. `interventions.proposeIntervention` defaults
`causalTauEst: input.causalTauEst ?? 0.15` on submission
(`src/lib/server/trpc/routers/interventions.ts:325`) — that default is the same class of invention
and is tracked by the conformity track; it is not a source this panel may render.
Pinned by `src/__tests__/components/ModerationPanel.test.tsx`.
