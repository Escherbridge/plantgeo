# Track 06: Real-Time Data Streaming - Implementation Plan

## Phase 1: Redis Pub/Sub
- [x] Set up Redis pub/sub service with channel management
- [x] Create publish/subscribe helpers
- [x] Add geographic filtering for viewport-aware updates
- [x] Test pub/sub message flow

## Phase 2: SSE Endpoints
- [x] Create SSE endpoint for layer updates
- [x] Implement last-event-id for reconnection
- [x] Add per-layer event channels
- [x] Create useSSE hook with auto-reconnect

## Phase 3: WebSocket Tracking
- [x] Create WebSocket endpoint for vehicle tracking
- [x] Implement smooth marker interpolation
- [x] Add connection heartbeat/keepalive
- [x] Create useWebSocket hook

## Phase 4: Live Map Layers
- [x] Build useLiveLayer hook for hot-swap GeoJSON updates
- [x] Implement incremental polygon updates
- [x] Add flash/highlight animation for new features
- [x] Integrate with MapLibre source.setData()

## Phase 5: Data Ingest
- [x] Create sensor data ingest API with validation
- [x] Create fire perimeter ingest API
- [x] Add webhook receiver for NASA FIRMS
- [x] Store to PostGIS + publish to Redis

## Phase 6: Connection Management
- [x] Build LiveIndicator component
- [x] Implement offline queue for submissions
- [x] Add exponential backoff reconnection
- [x] Create realtime-store for connection state
