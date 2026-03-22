# Track 29: Environmental Alert System — Implementation Plan

## Phase 1: Database Schema
- [x] Add `watchedLocations` table (id, userId, name, lat, lon, createdAt)
- [x] Add `alertSubscriptions` table (userId/teamId, alertType, frequency: immediate/daily/weekly)
- [x] Add `alerts` table (id, recipientId, recipientType: user/team, type, severity: low/medium/high/critical, title, body, lat, lon, metadata jsonb, readAt, createdAt)
- [x] Add unique index on `alerts(recipientId, type, createdAt::date)` for deduplication
- [ ] Run `npm run db:generate && npm run db:migrate`

## Phase 2: Alert Engine Service
- [x] Create `src/lib/server/services/alert-engine.ts`
- [x] Implement `checkFireProximity(watchedLocations[])` — ST_DWithin against active fire perimeters (50km)
- [x] Implement `checkStreamflow(watchedLocations[])` — query `waterGauges` where percentile < 10 or > 90
- [x] Implement `checkDroughtEscalation(watchedLocations[])` — compare latest vs previous USDM class per location
- [x] Implement `checkPriorityZones(teamServiceAreas[])` — new zones created since last check
- [x] Deduplication check: skip if same type alert exists for same recipient in last 24h

## Phase 3: Alert Dispatcher Job
- [x] Create `src/lib/server/jobs/alert-dispatcher.ts` — BullMQ job triggered by data refresh jobs
- [x] Run alert engine checks, insert alert records, queue email jobs for immediate subscribers
- [x] Register job triggers in: fire refresh (Track 21), water refresh (Track 22), zone refresh (Track 25)

## Phase 4: Email Service
- [x] Create `src/lib/server/services/email.ts`
- [x] Support Resend (`RESEND_API_KEY`) and SendGrid (`SENDGRID_API_KEY`) via `EMAIL_PROVIDER` env var
- [x] Implement `sendAlertEmail(to, alert)` — single alert email for severity ≥ warning
- [x] Implement `sendDigestEmail(to, alerts[])` — grouped summary email
- [x] Create `src/lib/server/jobs/email-digest.ts` — BullMQ daily job (8am UTC) for digest subscribers

## Phase 5: tRPC Alerts Router
- [x] Create `src/lib/server/trpc/routers/alerts.ts`
- [x] `getAlerts` query (authenticated → user's alerts, sorted severity+recency, pagination)
- [x] `markRead` mutation (alertId)
- [x] `markAllRead` mutation
- [x] `addWatchedLocation` mutation (name, lat, lon, radiusKm)
- [x] `updateSubscription` mutation (alertType, emailEnabled, inAppEnabled)
- [x] `getUnreadCount` query (for bell badge — lightweight count query)
- [x] `getWatchedLocations` query
- [x] Register `alertsRouter` in `src/lib/server/trpc/router.ts`

## Phase 6: Alert UI
- [x] Create `src/components/ui/AlertBell.tsx` — bell icon with unread count badge, polling `getUnreadCount` every 30s
- [x] Create `src/components/panels/AlertPanel.tsx` — alert feed list with severity icons, mark-read controls, tabs (All/Unread/Critical/Locations)
- [x] Watched locations management tab with add-location form
