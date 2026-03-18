# Track 12: Auth & Multi-Tenancy - Implementation Plan

## Phase 1: NextAuth Setup
- [ ] Install and configure NextAuth.js
- [ ] Set up credentials provider
- [ ] Add OAuth providers (Google, GitHub)
- [ ] Create auth database tables

## Phase 2: RBAC
- [ ] Define roles and permissions schema
- [ ] Create middleware for route protection
- [ ] Add per-layer access control checks
- [ ] Build permission checking utilities

## Phase 3: Multi-Tenancy
- [ ] Create organizations table and relationships
- [ ] Scope all queries by organization
- [ ] Build OrgSwitcher component
- [ ] Add organization invitation flow

## Phase 4: API Security
- [ ] Create API key management
- [ ] Add rate limiting middleware
- [ ] Configure CORS
- [ ] Add transformRequest token injection for MapLibre

## Phase 5: User Management
- [ ] Build user settings panel
- [ ] Create invite/role assignment UI
- [ ] Add audit logging

## Phase 6: Testing
- [ ] Test auth flows end-to-end
- [ ] Test RBAC enforcement
- [ ] Test multi-tenant data isolation
