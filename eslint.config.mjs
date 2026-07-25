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
      "prefer-const": "warn",
      "react-hooks/immutability": "warn",
      "react-hooks/refs": "warn",
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/set-state-in-render": "warn",
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
