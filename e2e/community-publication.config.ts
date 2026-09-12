import { defineConfig, devices } from "@playwright/test";
import { resolve } from "node:path";

process.env.COMMUNITY_E2E = "1";

export default defineConfig({
  testDir: ".",
  testMatch: "community-publication.spec.ts",
  fullyParallel: false,
  workers: 1,
  globalTeardown: "./community-publication-teardown.ts",
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 20_000 },
  reporter: [["list"]],
  outputDir: "../.tmp/community-browser-results",
  use: { ...devices["Desktop Chrome"], baseURL: "http://127.0.0.1:3307", serviceWorkers: "allow", trace: "retain-on-failure", screenshot: "only-on-failure" },
  webServer: {
    cwd: resolve(__dirname, ".."),
    command: "node e2e/community-publication-server.mjs",
    url: "http://127.0.0.1:3307/login",
    timeout: 240_000,
    reuseExistingServer: false,
    stdout: "pipe",
    stderr: "pipe",
  },
});
