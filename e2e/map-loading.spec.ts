import { test, expect } from "@playwright/test";
import { mockHermeticMapNetwork } from "./fixtures/network";

/**
 * The one loading-state selector this suite leans on structurally rather than by role/label:
 * MapView.tsx renders exactly one element with this exact class combination, the overlay that
 * wraps the `<Skeleton>` shown while `isLoading` is true. It is removed the instant the
 * MapLibre `"load"` event fires (see MapView.tsx `m.on("load", () => setIsLoading(false))`),
 * which is otherwise unobservable from outside the component -- the map instance itself is
 * never exposed on `window`. No `data-testid` was added for this (out of this task's file
 * boundary); this is the accessible-selector-free fallback, documented rather than silent.
 */
const LOADING_OVERLAY_SELECTOR = ".absolute.inset-0.z-10.flex.items-center.justify-center";

test.describe("map loading", () => {
  test.beforeEach(async ({ page }) => {
    await mockHermeticMapNetwork(page);
  });

  test("loads the app, mounts the MapLibre canvas, and serves the basemap archive from a fixture", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    // The one real network round trip this test depends on: MapLibre resolving the
    // `protomaps` vector source through the pmtiles:// protocol, which fetches this URL
    // for the archive header + root directory. Waiting on it (rather than a fixed sleep)
    // ties the wait to the actual resource the map needs, hermetically served by
    // mockHermeticMapNetwork from e2e/fixtures/pmtiles.ts.
    const archiveResponsePromise = page.waitForResponse((response) =>
      response.url().startsWith("https://tiles.aevani.com/")
    );

    await page.goto("/");

    const archiveResponse = await archiveResponsePromise;
    // Chromium slices a Range-requested fulfilled response into a 206 Partial Content itself
    // (the pmtiles reader always sends `Range: bytes=0-16383`) -- `ok()` covers both outcomes.
    expect(archiveResponse.ok()).toBe(true);

    // The map container mounts before the canvas; MapLibre appends the canvas into it
    // synchronously when the Map is constructed (see MapView.tsx `initMap`).
    const canvas = page.locator("canvas.maplibregl-canvas");
    await expect(canvas).toBeVisible();

    // Headless Chromium renders WebGL via SwiftShader (software rasterizer), which is
    // slower than a real GPU -- give the style/sources time to resolve and the loading
    // skeleton to clear before asserting on the settled state.
    await page.waitForSelector(LOADING_OVERLAY_SELECTOR, { state: "detached", timeout: 30_000 });

    await expect(canvas).toBeVisible();
    const canvasBox = await canvas.boundingBox();
    expect(canvasBox).not.toBeNull();
    expect(canvasBox!.width).toBeGreaterThan(0);
    expect(canvasBox!.height).toBeGreaterThan(0);

    // MapLibre's own navigation control is added synchronously in initMap and is a second,
    // independent signal (beyond the canvas) that the map actually initialized rather than
    // the page merely rendering an empty container.
    await expect(page.locator(".maplibregl-ctrl-zoom-in")).toBeVisible();

    expect(pageErrors, `Uncaught page errors: ${pageErrors.join("\n")}`).toEqual([]);
    expect(
      consoleErrors,
      `Console errors: ${consoleErrors.join("\n")}`
    ).toEqual([]);
  });
});
