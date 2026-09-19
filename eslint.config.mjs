import { defineConfig, globalIgnores } from "eslint/config";
import nextTs from "eslint-config-next/typescript";
import nextVitals from "eslint-config-next/core-web-vitals";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      "@typescript-eslint/ban-ts-comment": "error",
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
          ignoreRestSiblings: true,
        },
      ],
      "prefer-const": "warn",
      "react-hooks/immutability": "warn",
      "react-hooks/refs": "warn",
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/set-state-in-render": "warn",
    },
  },
  {
    files: ["src/lib/server/**/*.{ts,tsx}"],
    ignores: ["src/lib/server/http/bounded-upstream.ts"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "CallExpression[callee.name='fetch']",
          message:
            "Bare fetch() is banned in src/lib/server/**. Use fetchBoundedJson, fetchBoundedText, or fetchBounded from '@/lib/server/http/bounded-upstream' — they enforce a timeout and a response-size cap.",
        },
      ],
    },
  },
  {
    // A raw react-query result must not escape the viewport-read module. Its `data` outlives the
    // enablement that fetched it (`placeholderData: keepPreviousData`), so a consumer holding one
    // cannot tell an answer for the current request from a retained frame -- the defect style
    // review W8 B3 / W9 S1 / W10 B1 found three waves running, each time at a consumer that had
    // re-derived half of a predicate it could not see. Every hook returns a `LiveViewportRead`
    // instead; `liveViewportRead` is the only admission point.
    files: ["src/hooks/useViewportProxiedLayers.ts"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "ReturnStatement > CallExpression[callee.property.name='useQuery']",
          message:
            "Do not return a react-query result from useViewportProxiedLayers.ts. Wrap it: `return liveViewportRead<typeof query.data>(isAnswerLive, query);` — the retained frame must not reach a consumer while the observer is disabled.",
        },
      ],
    },
  },
  globalIgnores([
    ".next/**",
    "**/.mypy_cache/**",
    "**/.agri-local-runs/**",
    "**/.pytest_cache/**",
    "**/.pytest-workspace-temp/**",
    "**/.ruff_cache/**",
    "**/.tmp/**",
    "**/.uv-cache/**",
    "**/.venv/**",
    "**/__pycache__/**",
    "**/*.egg-info/**",
    "**/coverage/**",
    "**/htmlcov/**",
    "**/site-packages/**",
    "node_modules/**",
    "out/**",
    "public/generated/**",
    "tmp/**",
    "next-env.d.ts",
  ]),
]);
