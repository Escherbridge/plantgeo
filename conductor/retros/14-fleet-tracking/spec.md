# Track 14: Fleet & Asset Tracking

## Goal
Build real-time fleet and asset tracking with vehicle positions, route history, geofencing, and alert management using TimescaleDB for time-series storage.

## Features
1. **Live Vehicle Tracking**
   - Real-time position updates via WebSocket
   - Smooth marker interpolation between updates
   - Vehicle heading/direction indicator
   - Speed and status display
   - Vehicle type icons (truck, car, drone, person)

2. **Route History**
   - Historical path replay with time slider
   - Speed-colored route line
   - Stop detection and dwell time
   - Route statistics (distance, duration, stops)

3. **Geofencing**
   - Draw geofence zones on map
   - Entry/exit alerts
   - Dwell time alerts
   - Speed alerts within zones
   - Zone-based reporting

4. **Fleet Dashboard**
   - Fleet overview panel
   - Vehicle list with status
   - Filter by status/type/zone
   - Group management

5. **Alert Management**
   - Alert rules (geofence, speed, battery, offline)
   - Alert notifications (in-app, email)
   - Alert history and acknowledgment
   - Escalation rules

## Files to Create/Modify
- `src/components/tracking/FleetPanel.tsx` - Fleet overview
- `src/components/tracking/VehicleMarker.tsx` - Animated marker
- `src/components/tracking/RouteHistory.tsx` - Replay controls
- `src/components/tracking/GeofenceEditor.tsx` - Geofence drawing
- `src/components/tracking/AlertManager.tsx` - Alert management
- `src/lib/server/services/tracking.ts` - TimescaleDB tracking
- `src/lib/server/services/geofence.ts` - Geofence processing
- `src/lib/server/trpc/routers/tracking.ts` - Tracking router
- `src/stores/tracking-store.ts` - Fleet state

## Acceptance Criteria
- [ ] Vehicle markers update in real-time with smooth animation
- [ ] Route history replays with time slider
- [ ] Geofence triggers entry/exit alerts
- [ ] Fleet panel shows all vehicles with status
- [ ] TimescaleDB stores position history efficiently
- [ ] Alert rules trigger correctly
- [ ] 500+ vehicles tracked simultaneously
