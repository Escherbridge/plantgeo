# src/app/dashboard/org

Organization management: overview, members, invitations/join-links, settings.

- `useActiveOrganization` is the single source of truth for "which org, what
  role" across every page here. It prefers `session.user.activeTeamId` and
  falls back to the first `listMyTeams` membership so pages don't flash empty
  while a just-switched session is still propagating.
- Role gating here is UX-only (hide actions a role can't perform); the server
  is the actual authority. Don't add client-only checks that aren't backed by
  a matching server rule in `teams.ts`/`access-control.ts`.
- `listInvitations`/`listJoinLinks` row shapes are coded defensively
  (`acceptUrl` falling back to a `token`-derived URL, optional
  `allowedEmailDomain`/`maxUses`/`usedCount`/`revokedAt`) because the exact
  fields weren't finalized when this UI was built against the frozen `teams`
  contract. Reconcile field names against the actual router before relying on
  anything beyond what's read here.
- `layout.tsx` gives its `<main>` the scroll surface (`min-h-0 flex-1
  overflow-y-auto`) rather than letting the page scroll: `globals.css` keeps
  `body { overflow: hidden }` for the map, and scrolling the whole shell would
  move the header — which holds the only exit link back to `/dashboard` — off
  screen. `min-h-0` is load-bearing; without it the flex child refuses to
  shrink below its content and the overflow never engages.
- Switching organizations always goes through `teams.setActiveTeam` (persists
  server-side) *and* `useSession().update({ activeTeamId })` (refreshes the
  JWT immediately) — see `TeamSwitcher`. Skipping either leaves the UI and the
  middleware's org gate out of sync.
