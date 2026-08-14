import { test, expect } from "@playwright/test";
import { mockHermeticMapNetwork } from "./fixtures/network";

/**
 * ROUTING PANEL IS NOT REACHABLE FROM THE APP -- documented dead code, not a test gap.
 *
 * `src/components/map/AGENTS.md` (the "One manager, no floating surfaces" section) says so
 * directly: "Four never-mounted panels (`RoutingPanel`, `IsochronePanel`, `EcosystemTracker`,
 * `TeamProfilePanel`), which nothing had rendered in any tree." Confirmed independently while
 * writing this spec:
 *   - `src/components/routing/{WaypointInput,ModeSelector,RouteSummary,DirectionsList}.tsx` and
 *     `src/stores/routing-store.ts` exist, have unit tests
 *     (`src/__tests__/stores/routing-store.test.ts`), and the backing API route
 *     (`src/app/api/v1/route/route.ts`, requires an API key via `authorizeApiRequest`) works --
 *     but no `RoutingPanel` component exists, and none of the layer-panel dock sources, the App
 *     Router page files, or `panel-store.ts`'s `DockSectionId`/`PanelId` union reference
 *     "routing" at all. There is no click path from `/` that reaches a routing UI.
 *
 * This task's file boundary is playwright.config.ts, e2e (this directory), .husky, package.json
 * and .gitignore -- wiring a `RoutingPanel` into the dock is a change under `src`, out of bounds
 * here, and a product decision this task should not make unilaterally.
 *
 * The flow below is written against the real, already-implemented pieces (`WaypointInput`'s
 * `aria-label={placeholder}` combobox, `ModeSelector`'s `title`-labelled mode buttons,
 * `RouteSummary`'s duration/distance text, all driven by `useRoutingStore` from
 * src/stores/routing-store.ts) so it is ready to un-fixme the moment a `RoutingPanel` mounts
 * these components somewhere reachable -- only the entry-point interaction (opening that panel)
 * would need to change.
 */
test.describe("routing flow", () => {
  test.beforeEach(async ({ page }) => {
    await mockHermeticMapNetwork(page);
    // src/app/api/v1/route/route.ts -- POST, requires an API key, proxies Valhalla. Intercepted
    // at the browser network layer so this stays hermetic regardless of that auth requirement.
    await page.context().route("**/api/v1/route", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          trip: {
            shape: "_p~iF~ps|U_ulLnnqC_mqNvxq`@",
            legs: [{ maneuvers: [] }],
            summary: { length: 12.3, time: 900, has_highway: false, has_toll: false },
          },
        }),
      })
    );
  });

  test.fixme(
    "opens the routing panel, sets an origin/destination, and renders the fixture route",
    async ({ page }) => {
      await page.goto("/");

      // There is no reachable control that opens a routing panel -- see the module comment.
      // Once `RoutingPanel` (or equivalent) is mounted, replace this with the real open action,
      // e.g. `await page.getByRole("button", { name: /Directions/ }).click();`, and the
      // assertions below should already match the existing components' markup.
      await page.getByRole("button", { name: /Directions|Routing|Get directions/ }).click();

      await page.getByRole("combobox", { name: "Origin" }).fill("Portland, Oregon");
      await page
        .getByRole("option", { name: "Portland, Oregon, United States" })
        .click();

      await page.getByRole("combobox", { name: "Destination" }).fill("Seattle, Washington");
      await page
        .getByRole("option", { name: "Seattle, Washington, United States" })
        .click();

      // RouteSummary.tsx formats route.summary.time/length -- 900s/12.3km fixture -> "15 min" / "12.3 km".
      await expect(page.getByText("15 min")).toBeVisible();
      await expect(page.getByText("12.3 km")).toBeVisible();
    }
  );
});
