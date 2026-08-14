# Track 12: Auth & Teams — Implementation Plan

## Phase 1: NextAuth Setup
- [x] Install next-auth@5, @auth/drizzle-adapter, bcryptjs
- [x] Create `src/app/api/auth/[...nextauth]/route.ts` — credentials + Google/GitHub OAuth
- [x] Add auth tables to schema.ts: users (with platform_role, verified), sessions, accounts, verification_tokens
- [x] Create `src/lib/server/auth.ts` — getServerSession, hashPassword, verifyPassword

## Phase 2: RBAC
- [x] Add platform_role enum to users: 'public' | 'contributor' | 'expert' | 'admin'
- [x] Create tRPC procedure guards: protectedProcedure, contributorProcedure, expertProcedure, adminProcedure
- [x] Create `src/middleware.ts` — protect /dashboard and authenticated API routes
- [x] Update tRPC context to include session + user role

## Phase 3: Teams
- [x] Add teams table: id, name, slug, description, created_by, created_at
- [x] Add team_members table: team_id, user_id, team_role ('owner'|'member'|'viewer'), joined_at
- [x] Add team_id (nullable) to layers, features, drawings tables
- [x] Create `src/lib/server/trpc/routers/teams.ts` — create, invite, removeMember, updateRole, listMyTeams
- [x] Create `src/components/teams/TeamSwitcher.tsx` — dropdown for active team context
- [x] Create `src/components/teams/TeamPanel.tsx` — member list, roles, invite form

## Phase 4: Contribution Validation
- [x] Add status field to features/layers: 'draft' | 'pending_review' | 'published' | 'rejected'
- [x] Add review_note field for expert rejection feedback
- [x] Create `src/lib/server/trpc/routers/contributions.ts` — submit, review, publish, reject
- [x] Create `src/components/panels/ContributionQueue.tsx` — expert review UI with approve/reject actions

## Phase 5: User Management & API Keys
- [x] Create `src/components/auth/LoginForm.tsx` — email/password + OAuth buttons
- [x] Create `src/components/auth/RegisterForm.tsx` — registration with email verification
- [x] Create `src/components/panels/UserPanel.tsx` — profile, teams, contributions, API keys
- [x] Add api_keys table: id, key_hash, user_id, team_id, name, permissions, rate_limit, last_used
- [x] API key middleware for /api/v1/* with Redis rate limiting
- [x] Add audit_logs table, log significant actions

## Phase 6: Integration
- [x] Update all existing tRPC routers to use appropriate procedure guards
- [x] Scope all layer/feature queries by team_id (when team context active) or public
- [x] Create `src/stores/auth-store.ts` — session, platform role, active team
- [x] Add MapLibre transformRequest for protected tile sources
