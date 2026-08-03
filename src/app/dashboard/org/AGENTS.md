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
- Switching organizations always goes through `teams.setActiveTeam` (persists
  server-side) *and* `useSession().update({ activeTeamId })` (refreshes the
  JWT immediately) — see `TeamSwitcher`. Skipping either leaves the UI and the
  middleware's org gate out of sync.
