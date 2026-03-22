# Track 12: Authentication & Teams

## Goal
Build authentication, authorization, and self-organizing team support for PlantGeo — a shared public platform where anyone can contribute, form field teams, and collaborate. No tenant isolation or on-prem deployment. All users share the same hosted instance.

## Platform Model
- **Public (unauthenticated):** read-only access to public layers and map data
- **Verified contributor:** submit field observations, draw zones, annotate
- **Expert:** validate and publish contributions, manage authoritative layers
- **Admin:** platform-wide management
- **Teams:** any user can create a team and self-organize (NGO, fire brigade, research group, enterprise field ops)
- Team roles: `owner | member | viewer` (within a team)
- All data in shared tables scoped by `team_id` — no database-per-tenant

## Features

1. **Authentication**
   - NextAuth.js v5 with credentials (email/password) and OAuth (Google, GitHub)
   - Email verification for new accounts
   - JWT session management

2. **Platform RBAC**
   - Roles: `public | contributor | expert | admin`
   - tRPC procedure guards: `protectedProcedure`, `contributorProcedure`, `expertProcedure`, `adminProcedure`
   - Public data readable by all; personal/team data requires auth

3. **Teams**
   - Any user can create a team and invite members
   - Team roles: `owner | member | viewer`
   - Layers, drawings, fleet, and alerts scoped by `team_id` (nullable = public/personal)
   - TeamSwitcher UI to set active team context

4. **Contribution Validation**
   - Contributors submit observations as `pending_review`
   - Experts publish or reject with a note
   - Status flow: `draft → pending_review → published | rejected`
   - ContributionQueue panel for expert review

5. **API Security**
   - API keys scoped to user or team
   - Rate limiting per key via Redis
   - MapLibre `transformRequest` token injection for protected tile sources

6. **User Management**
   - Profile, password change, email verification
   - My teams, my contributions, my API keys
   - Audit log for significant actions

## Files to Create/Modify
- `src/app/api/auth/[...nextauth]/route.ts` — NextAuth config
- `src/lib/server/auth.ts` — auth helpers, session utils
- `src/lib/server/db/schema.ts` — users, sessions, teams, team_members, api_keys, audit_logs; add team_id + status to layers/features
- `src/lib/server/trpc/routers/teams.ts` — team CRUD, invite, membership
- `src/lib/server/trpc/routers/contributions.ts` — submit, review, publish, reject
- `src/components/auth/LoginForm.tsx` — login UI
- `src/components/auth/RegisterForm.tsx` — registration UI
- `src/components/teams/TeamSwitcher.tsx` — active team context switcher
- `src/components/teams/TeamPanel.tsx` — manage team members and roles
- `src/components/panels/ContributionQueue.tsx` — expert review queue
- `src/components/panels/UserPanel.tsx` — profile, teams, API keys
- `src/middleware.ts` — protect authenticated routes
- `src/stores/auth-store.ts` — auth state + active team

## Acceptance Criteria
- [ ] Users can register, verify email, and log in
- [ ] OAuth providers work (Google, GitHub)
- [ ] Platform RBAC enforced on all tRPC procedures
- [ ] Any user can create a team and invite members
- [ ] Layers/features correctly scoped by team_id or public
- [ ] Contributors can submit observations for review
- [ ] Experts can publish or reject pending contributions
- [ ] API keys gate access with Redis rate limiting
- [ ] Audit log captures significant actions
- [ ] No org isolation, no on-prem config, no custom domains
