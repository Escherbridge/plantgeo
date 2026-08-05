# Panels

## ContributionQueue is mounted by a route, not a panel

`ContributionQueue` renders the expert moderation queue for community-submitted
interventions (`contributions.listPendingReview` / `publishContribution` /
`rejectContribution`, all `expertProcedure`). It is mounted at
`src/app/moderation/page.tsx`, not by `PanelManager`
(`src/components/map/PanelManager.tsx`).

Two reasons:

- `PanelManager` currently mounts six panels and none of them is role-gated.
  Moderation is the first surface in this app that only some signed-in users
  may see; folding it into `PanelManager` would mean either gating the whole
  panel system on role (touching five panels that don't need it) or leaving an
  ungated seventh panel next to six ungated ones, which is easy to get wrong
  later.
- A moderation queue is not map-adjacent work. Panels exist to stay in view
  while a moderator keeps working the map underneath. There's no map context a
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
their own rejected recommendation (`CommunityPanel`'s
`listMySubmissions` rendering). A rejection with no note is not actionable by
whoever submitted it, so the queue disables the Reject button until the
reviewer types one — this is enforced client-side only, as a UX nudge; the
router's `reviewNote` field stays optional server-side.
