# Track 11: Offline-First & PWA

## Goal
Make PlantGeo work offline as a Progressive Web App with tile caching, offline data entry, and background sync.

## Features
1. **Service Worker Tile Cache**
   - Cache-first strategy for basemap tiles
   - Network-first for dynamic overlay tiles
   - Configurable cache size limit
   - Tile pre-download for specified areas

2. **Offline Data Entry**
   - Draw features offline (stored in IndexedDB)
   - Log sensor readings offline
   - Queue API requests for later sync
   - Conflict resolution on reconnect

3. **Background Sync**
   - Sync queued operations when online
   - Progress indicator for pending sync
   - Retry failed operations
   - Notification on sync complete

4. **PWA Manifest**
   - Install prompt
   - App icon and splash screen
   - Standalone display mode
   - Theme color matching

5. **Offline Maps**
   - Download map area for offline use
   - Select zoom range to cache
   - Storage usage display
   - Clear cached areas

## Files to Create/Modify
- `public/sw.js` - Service worker
- `src/app/manifest.ts` - PWA manifest
- `src/lib/offline/tile-cache.ts` - Tile caching strategy
- `src/lib/offline/sync-queue.ts` - Offline operation queue
- `src/lib/offline/indexed-db.ts` - IndexedDB helpers
- `src/components/panels/OfflinePanel.tsx` - Offline map download
- `src/components/ui/SyncIndicator.tsx` - Sync status
- `src/hooks/useOfflineSync.ts` - Background sync hook

## Acceptance Criteria
- [ ] Map renders with cached tiles when offline
- [ ] Features can be drawn/saved while offline
- [ ] Data syncs automatically when back online
- [ ] PWA installs on mobile and desktop
- [ ] Area download pre-caches tiles for offline use
- [ ] Storage usage is tracked and displayed
- [ ] Conflict resolution handles concurrent edits
