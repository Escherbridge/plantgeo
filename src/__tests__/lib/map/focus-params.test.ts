import { describe, expect, it } from "vitest";
import { MAP_MAX_ZOOM, MAP_MIN_ZOOM } from "@/lib/map/camera-limits";
import {
  DEFAULT_MAP_FOCUS_ZOOM,
  buildMapFocusHref,
  readMapFocus,
} from "@/lib/map/focus-params";

/** The URL is user input; MapLibre throws on a non-finite centre. */
function focusFrom(query: string) {
  return readMapFocus(new URLSearchParams(query));
}

describe("buildMapFocusHref", () => {
  it("round-trips a point through the query contract", () => {
    const href = buildMapFocusHref(-116.2023, 43.6150);
    expect(href).not.toBeNull();

    const focus = focusFrom(href!.replace(/^\/\?/, ""));
    expect(focus).toEqual({
      longitude: -116.2023,
      latitude: 43.615,
      zoom: DEFAULT_MAP_FOCUS_ZOOM,
    });
  });

  it("returns null for a row whose centroid the database could not compute", () => {
    expect(buildMapFocusHref(null, 43.615)).toBeNull();
    expect(buildMapFocusHref(-116.2023, null)).toBeNull();
    expect(buildMapFocusHref(undefined, undefined)).toBeNull();
  });

  it("returns null rather than a link to nowhere for out-of-range coordinates", () => {
    expect(buildMapFocusHref(181, 0)).toBeNull();
    expect(buildMapFocusHref(0, 91)).toBeNull();
    expect(buildMapFocusHref(Number.NaN, 0)).toBeNull();
    expect(buildMapFocusHref(0, Number.POSITIVE_INFINITY)).toBeNull();
  });

  it.each([
    ["a non-finite zoom falls back to the default", Number.NaN, DEFAULT_MAP_FOCUS_ZOOM],
    ["a zoom past the ceiling is clamped to it", 99, MAP_MAX_ZOOM],
    ["an in-range zoom is written as given", 15, 15],
  ])("%s", (_description, zoom, written) => {
    const href = buildMapFocusHref(-116.2023, 43.615, zoom);
    expect(href).toContain(`focusZoom=${written}`);
  });
});

describe("readMapFocus", () => {
  it("returns null when the params carry no focus at all", () => {
    expect(focusFrom("")).toBeNull();
    expect(focusFrom("focusLng=-116.2")).toBeNull();
    expect(focusFrom("focusLat=43.6")).toBeNull();
  });

  it("rejects coordinates outside the geographic range", () => {
    expect(focusFrom("focusLng=200&focusLat=43.6")).toBeNull();
    expect(focusFrom("focusLng=-116.2&focusLat=-91")).toBeNull();
  });

  it("rejects non-numeric coordinates instead of passing NaN to the camera", () => {
    expect(focusFrom("focusLng=boise&focusLat=43.6")).toBeNull();
    expect(focusFrom("focusLng=-116.2&focusLat=")).toBeNull();
  });

  it.each([
    ["an unparseable zoom keeps the point at the default zoom", "nope", DEFAULT_MAP_FOCUS_ZOOM],
    ["a blank zoom keeps the point at the default zoom", "", DEFAULT_MAP_FOCUS_ZOOM],
    ["an in-range zoom is honoured", "16.5", 16.5],
    ["the floor is honoured", "0", 0],
    ["a z20 link written under the old z22 contract opens at the ceiling", "20", MAP_MAX_ZOOM],
    ["a far-past-the-ceiling zoom opens at the ceiling", "40", MAP_MAX_ZOOM],
    ["a negative zoom opens at the floor", "-3", MAP_MIN_ZOOM],
  ])("%s", (_description, zoomParam, zoom) => {
    expect(focusFrom(`focusLng=-116.2&focusLat=43.6&focusZoom=${zoomParam}`)).toEqual({
      longitude: -116.2,
      latitude: 43.6,
      zoom,
    });
  });
});
