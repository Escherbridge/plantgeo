# Track 27: Team Organization Pages — Implementation Plan

## Phase 1: Database Schema
- [x] Add `teams` table to schema (id, name, description, orgType, specialties text[], serviceArea geometry(Polygon,4326), verified bool, logoUrl, website, contactEmail, createdAt, ownerId)
- [x] Add `teamMembers` table (teamId, userId, role: owner/admin/member)
- [ ] Run `npm run db:generate && npm run db:migrate`
- [ ] Add PostGIS spatial index on `teams.serviceArea`

## Phase 2: tRPC Teams Router Extension
- [x] Extend `src/lib/server/trpc/routers/teams.ts` with:
  - `createTeam` mutation (authenticated, validates GeoJSON polygon)
  - `updateTeam` mutation (owner/admin only)
  - `getTeamsInBbox` query (bbox → team polygons with metadata)
  - `getTeamProfile` query (teamId → full profile)
  - `getTeamDashboard` query (authenticated → priority zones in service area, incoming requests)
- [x] Use PostGIS `ST_Intersects` to find priority zones within service area

## Phase 3: Service Area Draw Tool
- [x] Install `@mapbox/mapbox-gl-draw` or implement lightweight polygon editor with MapLibre events
- [x] Create `src/components/tools/ServiceAreaDrawTool.tsx` — polygon draw mode with vertex editing
- [ ] Support import from predefined boundaries: county/watershed (via existing PostGIS boundary data or HydroSHEDS from Track 22)
- [x] Convert drawn GeoJSON polygon to WKT for storage

## Phase 4: Map Layer
- [x] Create `src/components/map/layers/TeamLayer.tsx`
- [x] Fill polygons colored by orgType (NGO=green, government=blue, contractor=orange, utility=purple)
- [x] Hover popup: team name, top 3 specialties, verified badge
- [x] Click → dispatch teamId to panel store → open TeamProfilePanel
- [ ] Add to layer control toggle list

## Phase 5: Profile & Dashboard Panels
- [x] Create `src/components/panels/TeamProfilePanel.tsx` — logo, description, specialties chips, service area stats (area in km²), contact button, active projects list
- [x] Create `src/components/panels/TeamDashboard.tsx` (authenticated) — edit profile, view priority zones in service area table, incoming service request notifications
- [x] Integrate with Track 25: query `getPriorityZones` filtered to team's service area bbox

## Phase 6: Admin Verification
- [x] Add `verifyTeam` mutation (admin role check via existing auth)
- [x] Verified badge display in TeamProfilePanel and TeamLayer tooltip
