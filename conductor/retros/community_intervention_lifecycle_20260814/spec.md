---
type: specification
---

# Conductor Track Specification: Community & Intervention Lifecycle Engine

## Track ID: `community_intervention_lifecycle_20260814`

### Overview
This track consolidates legacy community proposals (`community_engagement_completion_20260805`) and North American evidence ingestion (`north_america_intervention_data_20260723`) into a single, high-impact **Community & Intervention Lifecycle Engine**.

It enables land managers, agroforesters, and local communities to propose Regenerative Agriculture, Biochar, Wildfire Buffer, and Drought Mitigation projects directly on top of MapLibre ML Strategy heatmaps, submit them for expert moderation (`/moderation`), track project state changes (`Proposed` → `Under Expert Review` → `Approved / Active` → `Telemetry Monitored`), and monitor post-intervention environmental recovery (soil moisture retention, streamflow recovery, fire risk reduction).

### Objectives
1. **Direct Map Proposal Linkage**: Allow users to click on any ML Strategy Recommendation cell on MapLibre and convert it into a community intervention proposal (`strategyRequests`).
2. **Expert Moderation Scorecard UI (`/moderation`)**: Build a dedicated moderation dashboard for agronomists and forestry experts to review submitted proposals, evaluate ML causal effect bounds ($\hat{\tau}$), and cast approval votes.
3. **Intervention Feed & Progress Tracker (`/feed`)**: Upgrade the intervention feed to display active community projects with real-time satellite/sensor monitoring badges.
4. **Post-Intervention Telemetry Monitoring**: Link PostGIS spatial geometries of active interventions to live USGS water gauges, ERA5 soil moisture, and NASA FIRMS fire telemetry to quantify post-intervention recovery.

### Key Deliverables
- **tRPC Router Upgrade (`src/lib/server/trpc/routers/interventions.ts`)**: API endpoints for proposing interventions from map coordinates, expert voting, and state transitions.
- **Expert Moderation Panel (`src/app/moderation/page.tsx`)**: Moderation workflow page with evidence verification scorecards.
- **Enhanced Intervention Feed (`src/app/feed/InterventionFeed.tsx`)**: Live feed of active community interventions with telemetry badges.
- **Integration Tests**: Vitest suite verifying intervention lifecycle state transitions and moderation vote tallying.
