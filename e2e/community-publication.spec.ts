import { expect, test, type Page, type TestInfo } from "@playwright/test";
import identities from "./community-publication-identities.json";
import { mockHermeticMapNetwork } from "./fixtures/network";

const COMMUNITY_EMAILS = identities.emails;
const COMMUNITY_PASSWORD = identities.password;

test.skip(!process.env.COMMUNITY_E2E, "Requires the isolated community-publication.config.ts server and PostGIS database.");

async function signIn(page: Page, role: keyof typeof COMMUNITY_EMAILS) {
  const csrfResponse = await page.request.get("/api/auth/csrf");
  expect(csrfResponse.ok()).toBe(true);
  const { csrfToken } = await csrfResponse.json();
  const result = await page.request.post("/api/auth/callback/credentials", { form: { csrfToken, email: COMMUNITY_EMAILS[role], password: COMMUNITY_PASSWORD, callbackUrl: "/", json: "true" } });
  expect(result.ok()).toBe(true);
  const session = await (await page.request.get("/api/auth/session")).json();
  expect(session.user?.platformRole).toBe(role);
}

async function backgroundFixtures(page: Page) {
  await mockHermeticMapNetwork(page);
  await page.context().route("**/api/trpc/**", async (route) => {
    const procedures = new URL(route.request().url()).pathname.replace(/^\/api\/trpc\//, "").split(",");
    if (procedures.some((procedure) => /^(interventions|contributions|teams)\./.test(procedure))) return route.continue();
    await route.fulfill({ json: procedures.map(() => ({ result: { data: { json: null } } })) });
  });
}

async function openCommunity(page: Page) {
  await expect(page.locator("canvas.maplibregl-canvas")).toBeVisible();
  const close = page.getByRole("button", { name: "Close map manager" });
  if (!await close.isVisible()) {
    await expect(page.getByRole("button", { name: "Map manager", exact: true })).toBeVisible();
    await page.keyboard.press("Control+k");
    await expect(close).toBeVisible();
  }
  const details = page.getByRole("button", { name: "Strategy Requests", exact: true });
  if (await details.getAttribute("aria-expanded") !== "true") await details.click();
}

async function screenshot(page: Page, testInfo: TestInfo, name: string) {
  const path = testInfo.outputPath(`${name}.png`);
  await page.screenshot({ path });
  await testInfo.attach(name, { path, contentType: "image/png" });
}

async function mySubmissions(page: Page) {
  const response = await page.request.get(`/api/trpc/interventions.listMySubmissions?input=${encodeURIComponent(JSON.stringify({ json: {} }))}`);
  expect(response.ok()).toBe(true);
  return (await response.json()).result.data.json as Array<{ id: string; status: string; properties: { name: string; geometry: { type: string } } }>;
}

test("contributor draws Polygon, expert publishes, and the same warm viewport renders the real PostGIS feature", async ({ page, context }, testInfo) => {
  test.setTimeout(180_000);
  await backgroundFixtures(page);
  await signIn(page, "contributor");
  await page.goto("/?focusLng=-105.03&focusLat=40.02&focusZoom=13");
  await expect(page.locator("canvas.maplibregl-canvas")).toBeVisible();
  await page.evaluate(async () => { await navigator.serviceWorker.ready; });
  await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true);
  await openCommunity(page);
  const toggle = page.getByRole("switch", { name: "Show Interventions on map", exact: true });
  if (await toggle.getAttribute("aria-checked") !== "true") await toggle.click();
  await expect.poll(() => page.evaluate(async () => {
    const cache = await caches.open("plantgeo-v2");
    const requests = (await cache.keys()).filter((request) => request.url.includes("/intervention_tiles/"));
    return { count: requests.length, lengths: await Promise.all(requests.map(async (request) => (await (await cache.match(request))!.arrayBuffer()).byteLength)) };
  })).toMatchObject({ count: expect.any(Number), lengths: expect.arrayContaining([0]) });

  await page.getByRole("button", { name: "+ Recommend", exact: true }).click();
  await page.getByRole("button", { name: "Draw polygon", exact: true }).click();
  const drawing = page.getByRole("application", { name: "Intervention boundary map" });
  const bounds = await drawing.boundingBox();
  expect(bounds).not.toBeNull();
  const cx = bounds!.width * 0.66;
  const cy = bounds!.height * 0.38;
  for (const [x, y] of [[cx - 70, cy - 60], [cx + 70, cy - 60], [cx + 70, cy + 60], [cx - 70, cy + 60]]) await drawing.click({ position: { x, y } });
  await expect(page.getByText(/4 vertices selected/)).toBeVisible();
  await page.getByRole("button", { name: "Finish boundary", exact: true }).click();
  const name = `Synthetic browser boundary ${Date.now()}`;
  await page.getByLabel("Site Name", { exact: false }).fill(name);
  await page.getByLabel("Description", { exact: false }).fill("Synthetic local acceptance boundary for visible-map verification.");
  await page.getByRole("checkbox", { name: /I understand this is a recommendation/ }).check();
  await page.getByRole("button", { name: "Submit Recommendation", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Recommend an Intervention" })).toHaveCount(0);
  const submitted = (await mySubmissions(page)).find((row) => row.properties.name === name)!;
  expect(submitted.status).toBe("pending_review");
  expect(submitted.properties.geometry.type).toBe("Polygon");
  await expect(page.getByText(name, { exact: true }).locator("..")).toContainText("In review");
  await page.getByRole("button", { name: "Strategy Requests", exact: true }).click();
  await page.mouse.move(bounds!.x + cx, bounds!.y + cy);
  await expect(page.getByText(name, { exact: true })).toHaveCount(0);
  await screenshot(page, testInfo, "warm-empty-viewport");

  const reviewer = await context.newPage();
  await signIn(reviewer, "expert");
  await reviewer.goto("/moderation");
  const card = reviewer.locator(`[title="Feature ${submitted.id}"]`);
  await expect(card).toContainText(name);
  await card.getByRole("button", { name: "Approve & Publish", exact: true }).click();
  await expect(reviewer.getByRole("status")).toContainText("Published to the public map");
  await page.bringToFront();
  await expect.poll(() => page.evaluate(async () => {
    const cache = await caches.open("plantgeo-v2");
    const requests = (await cache.keys()).filter((request) => request.url.includes("/intervention_tiles/") && request.url.includes("publication="));
    const lengths = await Promise.all(requests.map(async (request) => (await (await cache.match(request))!.arrayBuffer()).byteLength));
    return lengths.some((length) => length > 0);
  })).toBe(true);
  await expect(async () => {
    await page.mouse.move(bounds!.x + cx - 1, bounds!.y + cy);
    await page.mouse.move(bounds!.x + cx, bounds!.y + cy);
    await expect(page.getByText(name, { exact: true })).toBeVisible();
  }).toPass({ timeout: 20_000 });
  await screenshot(page, testInfo, "published-same-viewport");
  const view = page.getByRole("button", { name: "Map view", exact: true });
  if (await view.getAttribute("aria-expanded") !== "true") await view.click();
  await page.getByRole("button", { name: "Light basemap", exact: true }).click();
  await expect(page.getByRole("button", { name: "Light basemap", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(async () => {
    await page.mouse.move(bounds!.x + cx - 1, bounds!.y + cy);
    await page.mouse.move(bounds!.x + cx, bounds!.y + cy);
    await expect(page.getByText(name, { exact: true })).toBeVisible();
  }).toPass({ timeout: 20_000 });
  await screenshot(page, testInfo, "published-after-style-swap");
  await signIn(page, "contributor");
  expect((await mySubmissions(page)).find((row) => row.id === submitted.id)?.status).toBe("published");
  await page.reload();
  await openCommunity(page);
  await expect(page.getByText(name, { exact: true }).locator("..")).toContainText("Published");
  await reviewer.close();
});

test.describe("touch boundary authoring", () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  test("touch rectangle can be saved, edited, and canceled while retaining the saved draft", async ({ page }) => {
    await backgroundFixtures(page);
    await signIn(page, "contributor");
    await page.goto("/?focusLng=-105.03&focusLat=40.02&focusZoom=13");
    await openCommunity(page);
    await page.getByRole("button", { name: "+ Recommend", exact: true }).tap();
    await page.getByRole("button", { name: "Draw rectangle", exact: true }).tap();
    const drawing = page.getByRole("application", { name: "Intervention boundary map" });
    await drawing.tap({ position: { x: 80, y: 100 } });
    await drawing.tap({ position: { x: 260, y: 260 } });
    await expect(page.getByText(/2 corners selected/)).toBeVisible();
    await page.getByRole("button", { name: "Finish boundary", exact: true }).tap();
    await expect(page.getByText("Polygon boundary: 4 vertices", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Edit polygon", exact: true }).tap();
    await page.getByRole("button", { name: "Clear / restart", exact: true }).tap();
    await page.getByRole("button", { name: "Cancel drawing", exact: true }).tap();
    await expect(page.getByText("Polygon boundary: 4 vertices", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Cancel", exact: true }).tap();
    await expect(page.getByRole("dialog", { name: "Recommend an Intervention" })).toHaveCount(0);
  });
});

test("keyboard rectangle authoring supports undo, clear, finish, and cancel without submitting", async ({ page }, testInfo) => {
  await backgroundFixtures(page);
  await signIn(page, "contributor");
  await page.goto("/?focusLng=-105.03&focusLat=40.02&focusZoom=13");
  await openCommunity(page);
  await page.getByRole("button", { name: "+ Recommend", exact: true }).click();
  await page.getByRole("button", { name: "Draw rectangle", exact: true }).click();
  const drawing = page.getByRole("application", { name: "Intervention boundary map" });
  await drawing.focus();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Shift+ArrowRight");
  await page.keyboard.press("Shift+ArrowDown");
  await page.keyboard.press("Enter");
  await expect(page.getByText(/2 corners selected/)).toBeVisible();
  await page.keyboard.press("Backspace");
  await expect(page.getByText(/1 corners selected/)).toBeVisible();
  await page.getByRole("button", { name: "Clear / restart", exact: true }).click();
  await expect(page.getByText(/0 corners selected/)).toBeVisible();
  await page.getByRole("button", { name: "Focus map", exact: true }).click();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Shift+ArrowRight");
  await page.keyboard.press("Shift+ArrowDown");
  await page.keyboard.press("Enter");
  await page.keyboard.press("Control+Enter");
  await expect(page.getByText("Polygon boundary: 4 vertices", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Edit polygon", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Recommend an Intervention" })).toBeVisible();
  await screenshot(page, testInfo, "keyboard-rectangle-draft");
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Recommend an Intervention" })).toHaveCount(0);
});
