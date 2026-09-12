# Local forecast contract

This isolated slice reads only the fixture-backed forecast Parquet plane. The
HTTP boundary and agent tool call the same validated reader and preserve the
selected coordinates, immutable run and UTC window. No current-condition,
historical, prior-day or PostgreSQL fallback is permitted.

`WEATHER_FORECAST_LOCAL_URL` is server-only and must name a loopback HTTP service.
The boundary is disabled when `NODE_ENV=production`; unconfigured service,
oversize payload, invalid schema or mismatched context returns an explicit
unavailable result. A fixture remains labelled synthetic throughout the UI.

The capability descriptor is a handoff, not shared catalogue registration.
Registration awaits ownership transfer of
`src/lib/server/services/parquet-slider-capabilities.ts`,
`src/lib/map/layer-registry.ts`, `src/lib/map/layer-render-contract.ts`,
`src/components/map/LayerManager.tsx`, and
`src/lib/server/trpc/routers/wildfire.ts`. The existing selected-day and observation
contracts remain with the tracks identified in both forecast metadata files.
The exported agent function is executable locally; registering it in the shared
agent/MCP dispatcher also requires its owner's handoff. No shared dispatcher is
modified by this slice.

All fixture times display in UTC. UTC daily summaries are explicitly derived,
and partial days never present complete totals. The page allows at most 48 hours
and displays only returned, validated hours; no horizon is synthesized. Changing
place/run/window clears the previous result, aborts the request and ignores
late results. Field samples do not imply continuous spatial coverage.
