# Track 06: Real-Time Data Streaming - Implementation Plan

## Phase 1: Redis Pub/Sub
- [ ] Set up Redis pub/sub service with channel management
- [ ] Create publish/subscribe helpers
- [ ] Add geographic filtering for viewport-aware updates
- [ ] Test pub/sub message flow

## Phase 2: SSE Endpoints
- [ ] Create SSE endpoint for layer updates
- [ ] Implement last-event-id for reconnection
- [ ] Add per-layer event channels
- [ ] Create useSSE hook with auto-reconnect

## Phase 3: WebSocket Tracking
- [ ] Create WebSocket endpoint for vehicle tracking
- [ ] Implement smooth marker interpolation
- [ ] Add connection heartbeat/keepalive
- [ ] Create useWebSocket hook

## Phase 4: Live Map Layers
- [ ] Build useLiveLayer hook for hot-swap GeoJSON updates
- [ ] Implement incremental polygon updates
- [ ] Add flash/highlight animation for new features
- [ ] Integrate with MapLibre source.setData()

## Phase 5: Data Ingest
- [ ] Create sensor data ingest API with validation
- [ ] Create fire perimeter ingest API
- [ ] Add webhook receiver for NASA FIRMS
- [ ] Store to PostGIS + publish to Redis

## Phase 6: Connection Management
- [ ] Build LiveIndicator component
- [ ] Implement offline queue for submissions
- [ ] Add exponential backoff reconnection
- [ ] Create realtime-store for connection state
