# Track 29: Environmental Alert System

## Goal
Proactively notify users and teams when environmental conditions cross critical thresholds near their area of interest — fire proximity, extreme drought, critical streamflow, new Priority Zones, and severe erosion risk. Alerts surface in-app and optionally via email/push.

## Features

1. **Alert Types**
   - **Fire Proximity**: active fire perimeter within 50km of user's watched location or team service area
   - **Streamflow Critical**: USGS gauge drops below 10th percentile (drought stress) or spikes above 90th (flood risk)
   - **Drought Escalation**: USDM drought class increases (e.g., D1 → D2) within user's area
   - **New Priority Zone**: community priority zone created within team's service area (Track 25)
   - **High Erosion Risk**: erosion risk layer flags "Very High" for a location in user's watchlist
   - **Carbon Opportunity**: SoilGrids update shows significant SOC drop in watched area (Track 24)

2. **User Alert Subscriptions**
   - Users save "watched locations" (named lat/lon pins) and optionally subscribe to alert types per location
   - Teams auto-subscribe to all alert types within their service area
   - Alert preferences stored per user: email frequency (immediate / daily digest / weekly), in-app only

3. **In-App Alert Feed**
   - Bell icon in nav with unread count badge
   - Alert feed panel: sorted by severity + recency, each alert with: type icon, title, location name, timestamp, "View on map" action
   - Mark as read individually or "mark all read"
   - Clicking alert: zoom map to affected area + open relevant panel (fire, water, soil, etc.)

4. **Alert Generation (Server-Side)**
   - BullMQ jobs evaluate thresholds on each data refresh cycle:
     - Fire alerts: triggered by existing fire detection job (Track 21)
     - Water alerts: triggered by 15-min USGS refresh job (Track 22)
     - Drought alerts: triggered by weekly USDM refresh job (Track 22)
     - Priority zone alerts: triggered by nightly zone recompute job (Track 25)
   - Alert deduplication: don't re-fire same alert type for same location within 24 hours

5. **Email Notifications**
   - Daily/weekly digest email: list of alerts for the period with map thumbnail and "View on PlantGeo" link
   - Immediate email for severity ≥ "High" alerts (fire proximity, critical streamflow)
   - Provider: Resend or SendGrid (configurable via `EMAIL_PROVIDER` env var)

## Files to Create/Modify
- `src/lib/server/db/schema.ts` — Add `watchedLocations` table, `alertSubscriptions` table, `alerts` table (id, userId/teamId, type, severity, title, body, lat, lon, readAt, createdAt)
- `src/lib/server/services/alert-engine.ts` — Threshold evaluation logic per alert type
- `src/lib/server/services/email-service.ts` — Email notification client (Resend/SendGrid)
- `src/lib/server/jobs/alert-dispatcher.ts` — BullMQ job: evaluate thresholds → create alert records → queue emails
- `src/lib/server/trpc/routers/alerts.ts` — `getAlerts`, `markRead`, `updateSubscription`, `addWatchedLocation`
- `src/components/panels/AlertPanel.tsx` — Alert feed UI with severity icons and map actions
- `src/components/ui/AlertBell.tsx` — Nav bell icon with unread count

## Acceptance Criteria
- [ ] Fire proximity alert created when active fire perimeter enters 50km of watched location
- [ ] Streamflow alert fires when gauge drops below 10th percentile
- [ ] Drought escalation alert fires within 1 hour of USDM data refresh
- [ ] Priority zone alert fires within 30 minutes of zone creation
- [ ] Alert feed shows unread count in nav bell
- [ ] Clicking alert zooms map to affected location and opens relevant panel
- [ ] Daily digest email sent to subscribed users with ≥1 new alert
- [ ] No duplicate alerts for same event within 24 hours

## Dependencies
- Track 21 (Wildfire) — fire perimeter data feed
- Track 22 (Water Scarcity) — streamflow + drought data
- Track 24 (Soil Health) — erosion risk + SOC data
- Track 25 (Community Requests) — priority zone creation events
- Track 27 (Teams) — team service area for auto-subscription
- BullMQ — job scheduling for threshold evaluation
- Resend or SendGrid — email delivery

## Tech Stack Note
Alerts are stored in PostgreSQL (not ephemeral Redis pub/sub) so users can see historical alerts and mark-as-read state persists. Real-time in-app delivery uses existing SSE infrastructure from Track 14 (fire alerts SSE). New alert types are added to the same SSE event stream.
