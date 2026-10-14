/**
 * THE map ceiling, named once so every camera on this platform stops at the same place.
 *
 * MapLibre's own default reaches z22 with no source behind it at all. Nothing this platform
 * serves needs to go that deep:
 *
 *   - the basemap's vector PMTiles stop publishing at z15 (sources.ts, createPmtilesSource) --
 *     but a VECTOR source overzooms cleanly past its own maxzoom (MapLibre keeps requesting the
 *     deepest tile and scales the already-parsed geometry), so z15 is a bandwidth ceiling, not a
 *     visual one.
 *   - the satellite raster stops at z19 (sources.ts, Esri World Imagery's own native ceiling) --
 *     and a RASTER source overzooms too, but by stretching the same block of PIXELS, which is
 *     the point where "zoomed in" stops meaning "more detail" and starts meaning "a blurrier
 *     crop of the same photo". This is the deepest real pixel data the platform has, so it sets
 *     the ceiling.
 *   - every Parquet-fed data rung tops out at BASE_ZOOM_TIER = 13 (zoom-tiers.ts) and the GIBS
 *     NDVI composite at z9 (vegetation.ts) -- both GeoJSON/vector-shaped, so both overzoom
 *     cleanly like the basemap, never vanish, and never needed a ceiling of their own.
 *
 * Capping the CAMERA at the satellite ceiling means a reader can never pinch past the deepest
 * real imagery into a meaningless stretched enlargement of the last tile MapLibre could fetch --
 * which is the mobile "granularity breaks" symptom the data rungs on their own do not cause (they
 * overzoom; only the un-capped camera let a reader zoom PAST what any source, including the
 * deepest one, actually has). If a future raster release is ever published past z19, raising this
 * one constant is the whole fix. A style LAYER's own `maxzoom` must stay above it: MapLibre hides a
 * layer at zoom >= its maxzoom, which blanked the satellite imagery at exactly z19 until 2026-10-13.
 *
 * Every MapLibre Map (`MapView`, `EmbedMap`, and the small drawing maps `InterventionSubmitModal`
 * and `AiInterventionWorkspace` embed) takes this as its own `maxZoom` constructor option, and
 * MapLibre's `Transform` then clamps every later `flyTo`/`easeTo`/`jumpTo`/`setZoom` against it
 * automatically -- so this constant is the map-level ceiling. `clampCameraZoom` below is the
 * programmatic-move ceiling: a second, call-site-level clamp for a zoom value that came from
 * outside this app's own control (a search result, a URL query param, an embed `postMessage`)
 * before it is ever handed to the map, so a caller reads a sane requested zoom rather than relying
 * only on the map silently re-clamping it later. See `src/__tests__/map/camera-zoom-cap-contract.test.ts`,
 * which scans every Map construction and camera move in `src/**` for this constant or
 * `clampCameraZoom`, and requires a `camera-cap-exempt:` comment on anything that deliberately
 * skips it.
 */
export const MAP_MAX_ZOOM = 19;

/** MapLibre's own floor, named so a constructor never hand-writes the bare literal. */
export const MAP_MIN_ZOOM = 0;

/**
 * Clamps a zoom value that did not originate in this app's own code -- a geocoder result, a
 * `focusZoom` URL param, an embed `postMessage` -- to the ceiling every map on this platform
 * obeys. Non-finite input (a malformed postMessage payload, a `NaN` from a bad query string)
 * resolves to the ceiling itself rather than throwing, because a camera move is best-effort
 * UX, never a place to crash the map over one bad field.
 */
export function clampCameraZoom(zoom: number): number {
  if (!Number.isFinite(zoom)) return MAP_MAX_ZOOM;
  return Math.min(Math.max(zoom, MAP_MIN_ZOOM), MAP_MAX_ZOOM);
}
