import { defineConfig, devices } from "@playwright/test";

/**
 * `npm run dev` binds port 3001 (see package.json `"dev": "next dev --turbopack -p 3001"`),
 * not Next's default 3000 -- the webServer target below matches that, not the framework
 * default, so `npx playwright test` boots the same server `npm run dev` would.
 */
const PORT = 3001;
const BASE_URL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: [["html", { outputFolder: "playwright-report", open: "never" }]],
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run dev",
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    // Turbopack's first compile of the whole App Router tree is slow, well past
    // Playwright's 60s default -- generous on purpose so a cold start doesn't flake.
    timeout: 180_000,
  },
});
