# Browser regression scope

The existing map and search suites use empty basemap/terrain fixtures and mocked
browser APIs. They verify UI behavior, not live source availability. The routing
flow remains explicitly skipped because its panel is not reachable.

`map-focus.spec.ts` exercises the canonical URL built by saved-conversation links,
delayed service-area arrival, and coordinate-search flight while closing the dock.
It reads the real canvas center through the user-visible location dialog, without
accessing MapLibre internals or sending an AI request. Five successive coordinate
readings ensure a transient animation frame cannot satisfy the assertion. The
visible metric scale must also settle below 1.5 km for the canonical zoom-13
target and 750 m for the zoom-14 coordinate search at the fixture latitude;
reaching the center while stopping at a regional zoom is a failure.
Each observation is dismissed with Escape, which clears both the analysis prompt
and the reverse-geocoding marker; closing only the analysis prompt leaves a marker
covering the next canvas-center click. Forward and reverse geocoding are fixtures.
Coverage is a procedure-specific fixture; other tRPC calls remain successful null
responses. Auth is anonymous, geocoding empty, and service workers are blocked.
This suite does not verify slider recovery, real Parquet ingestion, authenticated
saved-conversation SSR, or generated analysis. Those require separate coverage.

The configured runner starts the local Next dev server on port 3001. Browser
fixtures require no local database. Use the fixture basemap URL
`NEXT_PUBLIC_PMTILES_URL=https://tiles.aevani.com/fixture.pmtiles`; copying the
example environment's alternate basemap URL bypasses this fixture. Next's Google
font compilation can still require network access. Do not start a local database
to satisfy this suite, and do not confuse fixture success with production QA.
