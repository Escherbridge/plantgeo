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
Explore the map destination. Do not reinstate `DashboardGrid` or its Fire/Fleet/Environmental
presets until each offered widget has a published source and handles loading, outage, empty and
populated states without false zeros -- those three presets still point at unpublished
aggregates (fire severity rollups, fleet status, sensor time series) and remain exactly as
deferred as they were. The Parquet pivot track's deferred UI section records this roadmap
decision.

**2026-10-26: a narrower analytics destination reinstated, not the grid.** `analyticsRouter`'s
`layerStats`/`systemStats`/`featureCounts`/`recentActivity` all read the one public, relational
layer (`interventions`) rather than an unpublished warehouse aggregate, and `SpatialStats`
already handled loading/empty states -- the thing withholding it was that nothing mounted it,
not an unmet precondition. `/dashboard/analytics` (`src/app/dashboard/analytics/page.tsx`) mounts
`SpatialStats` alone, linked from this page's destination grid; it is deliberately a new,
minimal route rather than un-deferring `DashboardGrid`, whose other widget slots still have no
backing data. `SpatialStats` now also surfaces `recentActivity` (a second bar chart, "New
Features (24h)") and `featureCounts` (the CSV export on "Features per Layer" -- `layerStats` and
`featureCounts` run the identical join; `layerStats` only adds the name `BarChart` needs, so the
chart draws from one and the raw export reads from the other rather than drawing the same bars
twice). `getRegionalRiskSummary`, `getTrendData` and `getPrioritySubregions` stayed deleted: no
backing table exists for any of them (see `src/components/map/AGENTS.md`'s 2026-10-26 correction),
and their three purpose-built chart components (`PriorityTable`, `RiskSummaryWidget`,
`TrendChart`) plus two more that had drifted to zero importers for the same reason
(`ForecastBandChart`, `PieChart`) were deleted with them rather than kept as unreachable code.

The application body does not scroll. Dashboard uses `viewport-below-top-bar` with a fixed header
and a `min-h-0 flex-1 overflow-y-auto` main, matching organization pages. Moderation likewise owns
a viewport-bounded scroll surface so both review queues remain reachable on short/mobile screens.
