# Track 12: Authentication & Multi-Tenancy

## Goal
Build enterprise-grade authentication, authorization, and multi-tenancy for the platform, controlling access to layers, features, and APIs per organization.

## Features
1. **Authentication**
   - NextAuth.js with multiple providers
   - Email/password (credentials)
   - OAuth (Google, GitHub)
   - JWT token management
   - Session persistence

2. **Authorization**
   - Role-based access control (RBAC)
   - Roles: Admin, Editor, Viewer, API
   - Per-layer access permissions
   - Per-organization isolation

3. **Multi-Tenancy**
   - Organization management
   - Organization-scoped layers and features
   - Shared public layers across organizations
   - Organization switcher in UI

4. **API Security**
   - API key management for tile/API access
   - Rate limiting per API key
   - MapLibre transformRequest token injection
   - CORS configuration

5. **User Management**
   - Invite users to organization
   - Role assignment
   - User activity audit log
   - Account settings

## Files to Create/Modify
- `src/app/api/auth/[...nextauth]/route.ts` - NextAuth config
- `src/lib/server/auth.ts` - Auth helpers
- `src/lib/server/db/schema.ts` - Auth schema (users, orgs, roles)
- `src/lib/server/trpc/routers/auth.ts` - Auth tRPC router
- `src/lib/server/trpc/routers/org.ts` - Organization router
- `src/components/auth/LoginForm.tsx` - Login page
- `src/components/auth/OrgSwitcher.tsx` - Organization switcher
- `src/components/panels/UserPanel.tsx` - User settings
- `src/middleware.ts` - Route protection middleware
- `src/stores/auth-store.ts` - Auth state

## Acceptance Criteria
- [ ] Users can sign up and log in
- [ ] OAuth providers work (Google, GitHub)
- [ ] RBAC restricts layer editing to Editors/Admins
- [ ] Organizations isolate data correctly
- [ ] API keys gate tile and API access
- [ ] Rate limiting enforced per key
- [ ] Audit log tracks user actions
