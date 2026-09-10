# src/components/onboarding

Reusable pieces for the post-registration onboarding flow and the public
`/invite/[token]` and `/join/[code]` landing pages.

## Optional organization setup

The personal dashboard requires authentication, not organization membership.
Middleware authenticates every `/dashboard/**` and `/onboarding` request;
organization APIs still authorize membership and role on the server. Do not
restore an organization gate or a browser-local skip cookie for personal pages.
The organization overview already offers setup when no membership exists.

`OnboardingPage` keeps **Continue individually** outside the organization query
and Suspense boundary, so loading, errors, and every create/join step have an
exit to `/dashboard`. Bare `/onboarding` sends existing members to their org;
`?create=1` intentionally opens another organization setup. Neither requires
an organization mutation to leave.

`ConsoleShell` owns a viewport-height scroll surface because the map keeps
body scrolling disabled. Auto block margins center short cards and collapse
for long forms, keeping both the first action and final form controls reachable
on short/mobile viewports.

- `ConsoleShell` centralizes the "geospatial access" visual identity (Fraunces
  display italic + IBM Plex Mono, zinc-950/emerald palette, corner-bracket
  card frame, waypoint background) shared by `onboarding/layout.tsx`,
  `invite/layout.tsx`, and `join/layout.tsx`, all outside this directory. This
  intentionally continues the `(auth)` route group's aesthetic rather than the
  dashboard's `hsl(var(--*))` token system, since these are the same
  auth-adjacent journey; `dashboard/org/**` reuses the same palette for full
  continuity from onboarding into ongoing org management.
- `InvitePreviewCard` owns the loading/invalid/error/valid state machine
  shared by three call sites (`JoinOrganizationForm`, `/invite/[token]`,
  `/join/[code]`) so each page only has to build the `InvitePreviewState`.
- `OrganizationTypePicker` is intentionally styling-neutral (zinc/emerald) so
  it drops cleanly into both the onboarding wizard and the dashboard settings
  form.
- `OneTimeCodeReveal` is only ever rendered once, immediately after
  `createJoinLink`/`rotateJoinLink` resolve. The raw code is never persisted
  or re-fetchable — treat any surrounding state as ephemeral.
- Slug availability in `CreateOrganizationForm` is soft (client-side
  slugify only); the frozen `teams` contract has no dedicated availability
  check, so real conflicts surface as a `createTeam` mutation error.
