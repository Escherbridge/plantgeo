import { expect, test, type Page } from "@playwright/test";
import { buildMapFocusHref } from "../src/lib/map/focus-params";
import { mockHermeticMapNetwork } from "./fixtures/network";

test.use({ serviceWorkers: "block", contextOptions: { reducedMotion: "no-preference" } });

const coverage = { configured: true, bbox: { west: -125, south: 42, east: -110, north: 50 } };

async function readCenterThroughLocationDialog(page: Page): Promise<string | null> {
  const canvas = page.locator("canvas.maplibregl-canvas");
  await canvas.click({ button: "right" });
  const dialog = page.getByRole("dialog", { name: "Location actions" });
  const coordinates = await dialog.locator("p.font-mono").textContent();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Map marker", exact: true })).toHaveCount(0);
  return coordinates;
}

async function expectSettledCenter(page: Page, expected: string, maximumScaleMetres: number) {
  let consecutiveMatches = 0;
  await expect.poll(async () => {
    const scaleText = await page.locator(".maplibregl-ctrl-scale").textContent();
    const scale = scaleText?.trim().match(/^([\d.]+)\s*(km|m)$/);
    const scaleMetres = scale ? Number(scale[1]) * (scale[2] === "km" ? 1000 : 1) : Infinity;
    consecutiveMatches = await readCenterThroughLocationDialog(page) === expected && scaleMetres <= maximumScaleMetres
      ? consecutiveMatches + 1 : 0;
    return consecutiveMatches;
  }, { timeout: 15_000, intervals: [300] }).toBeGreaterThanOrEqual(5);
}

async function fixtureNetwork(page: Page, coverageReady: Promise<void>) {
  await mockHermeticMapNetwork(page);
  await page.context().route("**/api/auth/session", (route) => route.fulfill({ json: {} }));
  await page.context().route("**/api/geocode?**", (route) => route.fulfill({ json: { results: [] } }));
  await page.context().route("**/api/geocode/reverse?**", (route) => route.fulfill({ json: { results: [] } }));
  await page.context().route("**/api/trpc/**", async (route) => {
    const procedures = new URL(route.request().url()).pathname.replace(/^\/api\/trpc\//, "").split(",");
    if (procedures.includes("layers.getIngestionCoverage")) await coverageReady;
    await route.fulfill({ json: procedures.map((procedure) => ({
      result: { data: { json: procedure === "layers.getIngestionCoverage" ? coverage : null } },
    })) });
  });
}

async function waitForMap(page: Page) {
  await expect(page.getByRole("button", { name: /Map manager/ })).toBeVisible();
  await expect(page.locator("canvas.maplibregl-canvas")).toBeVisible();
  await expect(page.locator(".absolute.inset-0.z-10.flex.items-center.justify-center")).toHaveCount(0);
}

test("canonical saved-location target survives late service-area coverage", async ({ page }) => {
  let releaseCoverage!: () => void;
  const pendingCoverage = new Promise<void>((resolve) => { releaseCoverage = resolve; });
  await fixtureNetwork(page, pendingCoverage);
  try {
    await page.goto(buildMapFocusHref(-116.78, 45.94)!);
    await waitForMap(page);
    await expectSettledCenter(page, "45.94, -116.78", 1500);
  } finally {
    releaseCoverage();
  }
  await page.keyboard.press("Control+k");
  await expect(page.getByText(/^Covering /)).toBeVisible();
  await page.getByRole("button", { name: "Close map manager" }).click();
  await expectSettledCenter(page, "45.94, -116.78", 1500);
});

test("coordinate search reaches its target when the dock closes during flight", async ({ page }) => {
  await fixtureNetwork(page, Promise.resolve());
  await page.goto(buildMapFocusHref(-122.68, 45.52)!);
  await waitForMap(page);
  await page.keyboard.press("Control+k");
  await expect(page.getByText(/^Covering /)).toBeVisible();
  await page.getByRole("combobox", { name: "Search places" }).fill("45.94, -116.78");
  await page.getByRole("button", { name: "Go to 45.9400, -116.7800" }).click();
  await page.getByRole("button", { name: "Close map manager" }).click({ force: true });
  await expectSettledCenter(page, "45.94, -116.78", 750);
});
