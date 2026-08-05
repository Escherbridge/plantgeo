import { describe, expect, it } from "vitest";
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

  it("falls back to the default zoom when handed an unusable one", () => {
    const href = buildMapFocusHref(-116.2023, 43.615, 99);
    expect(href).toContain(`focusZoom=${DEFAULT_MAP_FOCUS_ZOOM}`);
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

  it("keeps a valid point when only the zoom is unusable", () => {
    expect(focusFrom("focusLng=-116.2&focusLat=43.6&focusZoom=nope")).toEqual({
      longitude: -116.2,
      latitude: 43.6,
      zoom: DEFAULT_MAP_FOCUS_ZOOM,
    });
    expect(focusFrom("focusLng=-116.2&focusLat=43.6&focusZoom=40")).toEqual({
      longitude: -116.2,
      latitude: 43.6,
      zoom: DEFAULT_MAP_FOCUS_ZOOM,
    });
  });

  it("honours an in-range zoom", () => {
    expect(focusFrom("focusLng=-116.2&focusLat=43.6&focusZoom=16.5")?.zoom).toBe(
      16.5
    );
    expect(focusFrom("focusLng=0&focusLat=0&focusZoom=0")?.zoom).toBe(0);
  });
});
