---
type: evidence-receipt
track: platform_experience_qa_20260911
date: 2026-09-12
status: partial
---

# Root integrated verification

This receipt covers the root checkout after integrating the approved weather
repair, the bounded Herbaria metadata refresh, climate/soil scalar labels and
the exact-UUID botanical authoring lookup. It records local verification only;
no Railway, production, object-store or data-writer action was attempted.

## Checks

| Check | Result | Detail |
| --- | --- | --- |
| `npm run check:data-boundary` | PASS | Client URL rules, restricted imports and observation-fabrication checks passed. |
| `npm run type-check` | NOT RUN | The checkout's `node_modules` directory has no `tsc` binary. |
| `npm run lint` / `npm run test:changed -- --base origin/main` | NOT RUN | Both depend on the missing JavaScript toolchain. An offline `npm ci --ignore-scripts` attempt was blocked by an `EPERM` read of the host npm cache; no network install was attempted. |
| `services/agri-data-service/scripts/check.py --changed --base origin/main` (with workspace `UV_CACHE_DIR`) | PARTIAL | Ruff format, Ruff lint and mypy passed. Full pytest reached `5482 passed, 147 skipped, 1 xfailed, 510 errors`; the selector was full service mode because shared agent/app modules changed, so this is not a green full-suite receipt. |
| Focused botanical/agent suite | PASS | `66 passed, 2 skipped` across the new botanical plane, HTTP route, agent graph and provider-wiring tests. |
| Root working-tree custody | PASS | No user-code changes are unstaged. Only pre-existing `.omc/ultrapilot-state.json`, `.omc/notepads/` and `.omc/plans/` remain outside the committed code/evidence set. |

## Interpretation

The focused botanical contract is verified locally, and the individual
weather/scalar-label owner receipts retain their own type, lint, boundary,
focused-test and synthetic-browser results. The combined Python suite is
environment-limited by the 510 errors and must not be represented as complete
release acceptance. The frontend combined type/test gates remain open until the
project dependencies are available in a writable environment.

The platform QA track therefore remains `active` with populated-data,
narrow-mobile/touch, role/accessibility, selected-day, cache, canvas and
agent/MCP acceptance still outstanding. This receipt does not authorize a
remote push, deployment or database read.
