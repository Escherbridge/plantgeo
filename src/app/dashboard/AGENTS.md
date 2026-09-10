# Dashboard module notes

## Deferred analytics widgets — 2026-09-10

The dashboard now exposes working map, organization and saved-conversation destinations only.
`DashboardGrid` remains as deferred implementation but is not imported or mounted. Its six chart
slots are explicit `UnavailableEvidence` placeholders across Fire Monitoring, Fleet Overview and
Environmental presets. `MetricsBar` subscribes to alerts:global but expects a metric snapshot shape
not supplied by the current alert events; its counters remain null. `SpatialStats` queries legacy
PostgreSQL layer/system statistics rather than the current Parquet environmental plane. Removing
the grid from this route also stops those subscription/query side effects and ignores stale saved
preset layouts. None of these backend routes or component modules is deleted in this change.

The right-hand Map Preview was an icon with links, not a rendered map; it is replaced by a direct
Explore the map destination. Do not reinstate analytics or a preset until each offered widget has
a published source and handles loading, outage, empty and populated states without false zeros.
The Parquet pivot track's deferred UI section records this roadmap decision.

The application body does not scroll. Dashboard uses `viewport-below-top-bar` with a fixed header
and a `min-h-0 flex-1 overflow-y-auto` main, matching organization pages. Moderation likewise owns
a viewport-bounded scroll surface so both review queues remain reachable on short/mobile screens.
