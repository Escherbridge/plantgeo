# Panels

## These are dock sections, not panels

**2026-08-08.** Every `*Details.tsx` in this directory is the body of one section of the map's
left-edge dock (`src/components/map/layer-panel/`), rendered by `DockDetails.tsx` and mounted
only while that section is expanded. None of them owns a `Sheet`, an `open` prop, a close
button or a layer switch any more — the dock's shell, its disclosure state and its layer rows
own those. The rationale for the merge, and for each thing it deleted, is in
`src/components/map/AGENTS.md` §"One dock, no sheets"; the rule that matters when editing one
of these files is short:

- **Mounted means open.** Do not add an `open` prop or an `enabled: open && …` gate back. A
  collapsed section is unmounted, so a query written here runs exactly when the reader is
  looking at it.
- **No scroll container.** The dock's body is the one scroller (`panel-scroll.ts` rule 2).
  A `max-h-*` + `overflow-y-auto` wrapper in here is a second scrollbar inside the first.
- **No layer switch.** `map-store.activeLayers` is written by the dock's `LayerRow` eyes.
  A switch here would be a second control over one value, and half of it would be out of sight.

`RegionalIntelligencePanel`, `ContributionQueue`, `LayerUpload`, `UserPanel` and the two submit
modals are not dock sections; they are mounted by routes or by other components.

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
