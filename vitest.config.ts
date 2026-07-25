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
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});
