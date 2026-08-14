import type { Page } from "@playwright/test";
import { buildEmptyPmtilesArchive } from "./pmtiles";

/**
 * A 1x1 transparent PNG. Good enough for anything MapLibre decodes as an image (sprite
 * atlas, raster-dem hillshade tiles, raster satellite tiles) -- the pixel content is
 * irrelevant to whether decoding succeeds.
 */
const TRANSPARENT_PNG_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";
const TRANSPARENT_PNG = Buffer.from(TRANSPARENT_PNG_BASE64, "base64");

/** The Martin composite source ids this app requests -- see src/lib/map/sources.ts. */
const DYNAMIC_TILES_HOST = "http://localhost:3100";

/**
 * Routes every network dependency MapView pulls in on a bare page load to an inline fixture,
 * so `page.goto("/")` never depends on a real basemap CDN, Martin, S3, or the database.
 *
 * Must be installed before `page.goto`, so it is in place for the very first navigation.
 *
 * Registered on `page.context()`, not `page`: empirically (verified with a standalone
 * repro against this app's own dev server), `page.route()` does not reliably intercept a
 * same-machine loopback-to-loopback fetch (this app's own origin, localhost:3001, fetching
 * localhost:3100) -- the request reaches the real network and fails with `net::ERR_FAILED`
 * even though the exact same pattern registered via `page.context().route()` catches it every
 * time. Every route below uses the context to stay consistent and avoid re-discovering this.
 *
 * What each host is and why it needs a specific shape:
 * - `tiles.aevani.com` -- the PMTiles vector basemap archive (`src/lib/map/sources.ts`
 *   `DEFAULT_PMTILES_ARCHIVE_URL`). Fulfilled with a byte-exact minimal empty archive (see
 *   `pmtiles.ts`) so the pmtiles reader succeeds and every tile resolves to "no data" instead
 *   of a network error.
 * - `protomaps.github.io` -- the style's sprite (icon atlas, fetched unconditionally at style
 *   load) and glyph fonts (fetched lazily, only for rendered label text -- which never happens
 *   here because the PMTiles fixture has no tile data). Sprite JSON/PNG are answered with a
 *   trivially valid empty spec and a 1x1 PNG; the glyph route exists defensively.
 * - `localhost:3100` -- Martin's dynamic/OSM composite vector sources. The composite TileJSON
 *   request (no z/x/y in the URL) gets a minimal valid TileJSON with no `vector_layers`, which
 *   mirrors Martin's real behaviour for function-backed sources and skips MapLibre's
 *   source-layer validation entirely. Anything else (an actual `{z}/{x}/{y}` tile request) gets
 *   an empty (zero-length) body, which is a valid, empty MVT tile.
 * - `s3.amazonaws.com` -- the terrain-dem raster-dem source. The `hillshade` paint layer is
 *   present in every style unconditionally, so its tiles are requested regardless of whether
 *   3D terrain is toggled on. Answered with the same 1x1 PNG.
 * - `server.arcgisonline.com` -- the satellite raster basemap. Not used by the default ("dark")
 *   style, routed defensively in case a spec switches styles.
 * - `/api/trpc/**` -- headless widgets mounted unconditionally inside MapView
 *   (`TimeSliderCapabilitiesLoader`, `ServiceAreaLayer`, `IngestionCoverageBadge`) each fire a
 *   tRPC query on mount. Rather than depend on the real (remote, prod) database those procedures
 *   read from, every batched call in the request is answered with a generic successful `null`
 *   payload, superjson-shaped. Every consumer of these reads is written to tolerate a null/absent
 *   payload (e.g. `capabilities?.serverCurrentDate ?? UNINITIALIZED_DATE`), so this never breaks
 *   rendering -- it only removes the real-network dependency and the react-query retry/backoff
 *   that a hard network failure would otherwise add to every test.
 */
export async function mockHermeticMapNetwork(page: Page): Promise<void> {
  const context = page.context();
  const pmtilesArchive = buildEmptyPmtilesArchive();

  await context.route("https://tiles.aevani.com/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/octet-stream",
      body: Buffer.from(pmtilesArchive),
    })
  );

  await context.route("https://protomaps.github.io/basemaps-assets/sprites/**", (route) => {
    const url = route.request().url();
    if (url.endsWith(".png")) {
      return route.fulfill({ status: 200, contentType: "image/png", body: TRANSPARENT_PNG });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });

  await context.route("https://protomaps.github.io/basemaps-assets/fonts/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/x-protobuf",
      body: Buffer.alloc(0),
    })
  );

  await context.route(`${DYNAMIC_TILES_HOST}/**`, (route) => {
    const url = new URL(route.request().url());
    const corsHeaders = { "Access-Control-Allow-Origin": "*" };
    // The composite TileJSON request has no {z}/{x}/{y} path segments -- every actual tile
    // request does (see createMartinCompositeSource in src/lib/map/sources.ts).
    const isTileRequest = /\/\d+\/\d+\/\d+/.test(url.pathname);
    if (isTileRequest) {
      return route.fulfill({
        status: 200,
        contentType: "application/x-protobuf",
        headers: corsHeaders,
        body: Buffer.alloc(0),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: corsHeaders,
      body: JSON.stringify({
        tilejson: "2.2.0",
        tiles: [`${DYNAMIC_TILES_HOST}/__fixture/{z}/{x}/{y}.pbf`],
        minzoom: 0,
        maxzoom: 22,
      }),
    });
  });

  await context.route("https://s3.amazonaws.com/**", (route) =>
    route.fulfill({ status: 200, contentType: "image/png", body: TRANSPARENT_PNG })
  );

  await context.route("https://server.arcgisonline.com/**", (route) =>
    route.fulfill({ status: 200, contentType: "image/png", body: TRANSPARENT_PNG })
  );

  await context.route("**/api/trpc/**", (route) => {
    const url = new URL(route.request().url());
    const procedurePath = url.pathname.replace(/^\/api\/trpc\//, "");
    const procedureCount = Math.max(1, procedurePath.split(",").length);
    const body = JSON.stringify(
      Array.from({ length: procedureCount }, () => ({ result: { data: { json: null } } }))
    );
    return route.fulfill({ status: 200, contentType: "application/json", body });
  });
}
