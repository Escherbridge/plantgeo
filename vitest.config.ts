import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

const repoRoot = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  root: repoRoot,
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    exclude: [
      "node_modules/**",
      ".next/**",
      "**/.venv/**",
      "**/.uv-cache/**",
      "**/coverage/**",
    ],
    coverage: {
      provider: "v8",
      thresholds: {
        lines: 60,
        functions: 60,
      },
    },
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
    // `vitest bench` reads this instead of `test.include`; scoped to __benchmarks__ so a bench
    // file can never be swept into a plain `npm test` run (whose `include` above only matches
    // *.test.*/*.spec.* anyway) and so `vitest run` never has to skip-filter bench files out.
    benchmark: {
      include: ["src/__benchmarks__/**/*.bench.ts"],
    },
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});
