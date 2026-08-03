import { NextResponse } from "next/server";
import { fetchBounded } from "@/lib/server/http/bounded-upstream";
import { GIBS_NDVI_PRODUCT } from "@/lib/vegetation";

export const runtime = "nodejs";

/** Observed GIBS NDVI tiles are ~50 KB; this only bounds a pathological response. */
const MAX_TILE_BYTES = 2 * 1024 * 1024;
const REQUEST_TIMEOUT_MS = 15_000;
/** A dated composite never changes once published. */
const IMMUTABLE_TILE_MAX_AGE_S = 7 * 24 * 60 * 60;
/** "default" tracks GIBS' newest composite, so it must expire. */
const LATEST_TILE_MAX_AGE_S = 6 * 60 * 60;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

interface TileParams {
  time: string;
  z: string;
  y: string;
  x: string;
}

function parseTileIndex(value: string, zoom: number): number | null {
  if (!/^\d+$/.test(value)) return null;
  const index = Number(value);
  return index < 2 ** zoom ? index : null;
}

/**
 * GET /api/tiles/vegetation/ndvi/{time}/{z}/{y}/{x}
 *
 * First-party proxy for NASA GIBS NDVI raster tiles. The browser never talks to
 * GIBS directly: routing environmental facts through a PlantGeo endpoint is what
 * keeps attribution, caching, and the upstream identity under our control, and
 * is enforced by scripts/check-client-provider-urls.mjs.
 * See `src/lib/server/AGENTS.md` §vegetation-tiles.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<TileParams> }
) {
  const { time, z, y, x } = await params;

  const isLatest = time === "default";
  if (!isLatest && !ISO_DATE.test(time)) {
    return NextResponse.json(
      { error: "time must be an ISO YYYY-MM-DD date or \"default\"" },
      { status: 422 }
    );
  }

  if (!/^\d+$/.test(z)) {
    return NextResponse.json({ error: "Invalid tile coordinate" }, { status: 422 });
  }
  const zoom = Number(z);
  if (zoom > GIBS_NDVI_PRODUCT.maxZoom) {
    return NextResponse.json(
      { error: `NDVI is published no deeper than zoom ${GIBS_NDVI_PRODUCT.maxZoom}` },
      { status: 404 }
    );
  }
  const row = parseTileIndex(y, zoom);
  const column = parseTileIndex(x, zoom);
  if (row === null || column === null) {
    return NextResponse.json({ error: "Invalid tile coordinate" }, { status: 422 });
  }

  const upstream =
    `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/` +
    `${GIBS_NDVI_PRODUCT.layerIdentifier}/default/${time}/` +
    `${GIBS_NDVI_PRODUCT.tileMatrixSet}/${zoom}/${row}/${column}.png`;

  let result;
  try {
    result = await fetchBounded(
      upstream,
      { method: "GET", headers: { Accept: "image/png" } },
      {
        maxBytes: MAX_TILE_BYTES,
        timeoutMs: REQUEST_TIMEOUT_MS,
        revalidateSeconds: isLatest ? LATEST_TILE_MAX_AGE_S : IMMUTABLE_TILE_MAX_AGE_S,
      }
    );
  } catch {
    return NextResponse.json({ error: "NDVI tile upstream is unavailable" }, { status: 502 });
  }

  // GIBS 404s dates outside a product's temporal extent rather than returning a
  // blank tile. Relaying that verbatim keeps a gap visibly empty on the map
  // instead of substituting imagery from another date.
  if (result.status === 404) {
    return NextResponse.json(
      { error: "GIBS published no NDVI composite for this date and tile" },
      { status: 404 }
    );
  }
  if (!result.ok || result.bodyError) {
    return NextResponse.json({ error: "NDVI tile upstream is unavailable" }, { status: 502 });
  }

  return new NextResponse(Buffer.from(result.bytes), {
    status: 200,
    headers: {
      "Content-Type": "image/png",
      "Cache-Control": `public, max-age=${
        isLatest ? LATEST_TILE_MAX_AGE_S : IMMUTABLE_TILE_MAX_AGE_S
      }, stale-while-revalidate=3600`,
      "X-Data-Source": GIBS_NDVI_PRODUCT.attribution,
      "X-Composite-Date": time,
    },
  });
}
