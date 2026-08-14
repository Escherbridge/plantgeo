# Track 06: Real-Time Data Streaming

## Goal
Build real-time geospatial data streaming infrastructure using SSE for broadcast updates and WebSocket for bidirectional communication, enabling live fire perimeters, sensor feeds, and vehicle tracking.

## Features
1. **Server-Sent Events (SSE) Infrastructure**
   - Event stream endpoint per layer type
   - Fire perimeter updates
   - Weather alert broadcasts
   - Sensor reading streams
   - Reconnection with last-event-id

2. **WebSocket Infrastructure**
   - Vehicle/asset tracking channel
   - Collaborative map editing
   - User cursor sharing
   - Connection health monitoring

3. **Redis Pub/Sub Backend**
   - Channel per layer for targeted updates
   - Geographic filtering (only push updates in viewport)
   - Message rate limiting
   - Dead letter queue for failed deliveries

4. **Live Map Updates**
   - Hot-swap GeoJSON source data without re-adding layers
   - Smooth animation for moving markers (vehicle tracking)
   - Incremental polygon updates (fire perimeter growth)
   - Flash/highlight on new data

5. **Data Ingest API**
   - REST endpoint for sensor data submission
   - Batch ingest for historical data
   - Webhook receiver for external feeds (NASA FIRMS, weather)
   - Data validation and deduplication

6. **Connection Management**
   - Auto-reconnect with exponential backoff
   - Connection status indicator in UI
   - Offline queue for pending submissions
   - Graceful degradation when stream unavailable

## Files to Create/Modify
- `src/app/api/stream/[layerId]/route.ts` - SSE endpoint
- `src/app/api/ws/tracking/route.ts` - WebSocket endpoint
- `src/app/api/ingest/sensors/route.ts` - Sensor data ingest
- `src/app/api/ingest/fires/route.ts` - Fire data ingest
- `src/hooks/useSSE.ts` - SSE connection hook
- `src/hooks/useWebSocket.ts` - WebSocket connection hook
- `src/hooks/useLiveLayer.ts` - Live updating map layer
- `src/components/map/LiveIndicator.tsx` - Connection status
- `src/lib/server/services/realtime.ts` - Redis pub/sub service
- `src/lib/server/services/ingest.ts` - Data ingest service
- `src/stores/realtime-store.ts` - Connection state

## Acceptance Criteria
- [ ] SSE streams deliver GeoJSON updates to connected clients
- [ ] WebSocket tracking shows smooth marker animation
- [ ] Redis pub/sub routes events to correct channels
- [ ] Auto-reconnect recovers from disconnection within 5s
- [ ] Sensor data ingest validates and stores to TimescaleDB
- [ ] Fire perimeter updates animate growth on map
- [ ] Connection indicator shows live/reconnecting/offline
- [ ] 1000+ concurrent SSE connections without degradation
