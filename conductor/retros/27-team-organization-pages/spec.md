# Track 27: Team Organization Pages

## Goal
Allow organizations, agencies, land managers, utility companies, and restoration teams to create a public team profile on PlantGeo with a defined service area polygon, specialty tags, and map visibility — enabling discovery by landowners seeking help and by PlantCommerce supplier matching.

## Features

1. **Team Profile Creation**
   - Team name, description, website, contact email, logo upload
   - Organization type: NGO / Government Agency / Private Contractor / Utility / Research Institution / Individual Practitioner
   - Specialty tags: wildfire mitigation / water management / soil restoration / reforestation / silvopasture / keyline design / biochar / general land management
   - Verified status (manually reviewed by PlantGeo admins — badge shown on profile)

2. **Service Area Polygon**
   - Draw service area on map (polygon tool using MapLibre draw / Turf.js)
   - Or: select from predefined watershed / county / state boundaries
   - Service area stored as PostGIS geometry
   - Visible on main map as semi-transparent overlay (toggleable)

3. **Team Map Layer**
   - Teams layer: shows service area polygons color-coded by organization type
   - Hover → tooltip with team name, specialty tags, rating
   - Click → open Team Profile panel
   - Filter by: specialty tag, organization type, verified status

4. **Team Profile Panel**
   - Public profile view: logo, description, specialties, service area, contact button
   - Active projects section: linked strategy requests from Track 25 that this team is working on
   - "Request services" button → opens contact form or links to PlantCommerce team page

5. **Team Dashboard (Authenticated)**
   - Private dashboard for team members: manage profile, update service area, view incoming service requests
   - See community priority zones within their service area (Track 25 integration)
   - Track strategies implemented (link to contribution system from existing Track 7)

## Files to Create/Modify
- `src/lib/server/db/schema.ts` — Add `teams` table (id, name, description, orgType, specialties[], serviceArea geom, verified, userId), `teamMembers` table
- `src/lib/server/trpc/routers/teams.ts` — Extend existing with `createTeam`, `updateTeam`, `getTeamsInBbox`, `getTeamProfile` (already partial)
- `src/components/map/layers/TeamLayer.tsx` — Service area polygon layer with org-type color coding
- `src/components/panels/TeamProfilePanel.tsx` — Public profile view
- `src/components/panels/TeamDashboard.tsx` — Authenticated team management view
- `src/components/map/ServiceAreaDrawTool.tsx` — Polygon draw tool for service area definition

## Acceptance Criteria
- [ ] Team profile creation form saves with service area polygon
- [ ] Service area polygons render on main map and are filterable by specialty
- [ ] Team profile panel opens on polygon click with full team details
- [ ] Community priority zones within service area visible in team dashboard
- [ ] "Request services" contact flow functional
- [ ] Verified badge displayed for verified teams
- [ ] Team map layer toggleable from layer control

## Dependencies
- Track 25 (Community Requests) — Priority zones shown in team dashboard
- Track 26 (Strategy Cards) — Strategy context for active projects
- Existing auth system (Track 12) — team ownership and member management
- Existing contributions system (Track 7) — linking implemented strategies

## Tech Stack Note
Service area drawing uses MapLibre GL Draw plugin or a lightweight custom polygon editor with Turf.js for geometry operations. PostGIS `ST_Intersects` queries identify priority zones within team service areas.
