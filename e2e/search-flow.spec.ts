import { test, expect } from "@playwright/test";
import { mockHermeticMapNetwork } from "./fixtures/network";
import type { NormalizedGeocodingResult } from "../src/lib/geocoding";

/**
 * Two results shaped exactly like `NormalizedGeocodingResult` (src/lib/geocoding.ts), which the
 * client validates strictly (`readGeocodingResults`) before rendering anything -- an
 * under-shaped fixture would fail silently as "No results found" rather than as a test failure,
 * so every field the validator checks is present.
 */
const FIXTURE_RESULTS: NormalizedGeocodingResult[] = [
  {
    id: "fixture-portland-or",
    type: "city",
    name: "Portland",
    displayName: "Portland, Oregon, United States",
    coordinates: [-122.6784, 45.5152],
    properties: { country: "United States" },
  },
  {
    id: "fixture-portland-me",
    type: "city",
    name: "Portland",
    displayName: "Portland, Maine, United States",
    coordinates: [-70.2553, 43.6591],
    properties: { country: "United States" },
  },
];

test.describe("search flow", () => {
  test.beforeEach(async ({ page }) => {
    await mockHermeticMapNetwork(page);
    // src/hooks/useGeocode.ts -> GET /api/geocode?q=...&limit=...&lat=...&lon=...
    // src/app/api/geocode/route.ts responds { results: NormalizedGeocodingResult[] }.
    // Routed on the context (not the page) for the same reason as e2e/fixtures/network.ts.
    await page.context().route("**/api/geocode?**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ results: FIXTURE_RESULTS }),
      })
    );
  });

  test("typing a query renders fixture results, and selecting one records it as a recent search", async ({
    page,
  }) => {
    await page.goto("/");

    // MapView is dynamically imported client-only (`ssr: false`), so the "Map manager" button
    // appearing is the earliest reliable signal that MapKeyboardShortcuts' keydown listener
    // (registered in a useEffect alongside it) has actually attached. Under a full parallel
    // suite run, sending the shortcut before that finishes drops the keypress silently.
    await expect(page.getByRole("button", { name: /Map manager/ })).toBeVisible();

    // The search field lives inside the map manager dock, which starts collapsed
    // (panel-store `layerPanelOpen: false`). Opening it via the Ctrl/Cmd+K shortcut
    // (src/components/map/MapKeyboardShortcuts.tsx) rather than clicking the rail's "Map
    // manager" button sidesteps a real conflict: that button sits bottom-left, the same
    // corner Next.js's dev-mode indicator (`<nextjs-portal>`) occupies, which intermittently
    // steals the click. The shortcut also focuses the field directly, matching how a keyboard
    // user actually reaches search.
    await page.keyboard.press("Control+k");

    const searchInput = page.getByRole("combobox", { name: "Search places" });
    await expect(searchInput).toBeVisible({ timeout: 10_000 });

    await searchInput.fill("Portland");

    const listbox = page.getByRole("listbox", { name: "Search places" });
    const options = listbox.getByRole("option");
    await expect(options).toHaveCount(2);
    await expect(options.nth(0)).toContainText("Portland, Oregon, United States");
    await expect(options.nth(1)).toContainText("Portland, Maine, United States");

    await options.nth(0).click();

    // Selecting a result calls flyToResult -> addRecentSearch + resetSearch (see
    // SearchDockSection.tsx): the field clears and the same result reappears under "Recent",
    // which is the observable, persisted state change a selection is supposed to cause.
    await expect(searchInput).toHaveValue("");
    await expect(page.getByText("Recent", { exact: true })).toBeVisible();

    const recentListbox = page.getByRole("listbox", { name: "Search places" });
    const recentOptions = recentListbox.getByRole("option");
    await expect(recentOptions).toHaveCount(1);
    await expect(recentOptions.first()).toContainText("Portland, Oregon, United States");
  });
});
