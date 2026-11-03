---
type: architecture
---
# Operator bootstrap implementation boundary

Status: independently inspected proposal, not implemented.

Add `agri-service data region-bootstrap --config deployment.toml --output deployment/` to `interface/cli/data.py`, with strict validation/rendering in `foundation/regional_bootstrap/`. Reuse the canonical region models and the lane loader's existing `PLANTGEO_LANES_DIRECTORY`. Compilation performs no acquisition, scheduler activation or availability publication. Keep the existing SHA-pinned `availability-bootstrap` publication operation separate.

The input declares schema version, deployment id, US bbox/timezone/country/admin membership, requested layers and sources, per-layer temporal mode/history interval/coverage fraction/revision policy, bounded acquisition and repair budgets, credential environment-variable names, and object/database isolation. Provider-specific defaults must be documented; example budget values must not silently become recommendations. Never place credential values in the input or generated bundle.

Generate deterministic `region.json`, versioned sorted stream support definitions, lane/provider TOMLs, matching service/web environment templates, isolation manifest, file checksums and `readiness.json`. Exclude timestamps and machine-specific paths. Report every requested layer, temporal semantics, credential names, support size, estimated work units and admission blockers. Offline presence checks cannot prove credential validity; an explicitly bounded provider preflight can, without starting acquisition.

The existing lane model supports history targets and forward/gap-fill caps. Independent correction and repair budgets require actual runner changes: emitting unknown or unused TOML fields is not implementation. Current-only weather cannot accept historical obligations. Legacy climate/soil/vegetation paths must be generalized at their actual execution seams, preserving PNW identities. FIRMS and watershed requests must use their passed region. See `source-support-audit.md` for exact paths.

Fail requested unsupported combinations explicitly with stable reasons such as `source_support_not_region_generic`, `current_only_history_requested`, `missing_required_credential`, and `footprint_requires_partitioning`. Do not silently omit requested layers. State presets must describe whether they mean a bounding envelope or a real state mask. Bounds wider than 30 degrees longitude or 20 degrees latitude, and antimeridian crossings, need supported partitioning or explicit refusal.

Tie storage and executor isolation to `identity-design.md`: geographic namespace, stream support proof and web/service agreement have distinct identities. Database-backed lane leases/jobs need isolation in addition to object prefixes. The activation seams remain `execution/lane_specs.py` and `job_executor_service.py`. Do not emit an executable custom production activation bundle until identity enforcement and source admission exist.

Completion still requires a fresh non-PNW multi-layer run and a second region proving isolation. A working compiler or water-only run is an intermediate milestone.

## Existing local startup coverage

The root `docker-compose.yml` starts PostGIS, Redis and Martin, but neither the web application nor the Python API/executor. `infra/local-warehouse/compose.yaml` starts only an isolated PostGIS warehouse on port 5442. The service-local compose starts its database, Redis, Martin and a development API, but no executor or web application. None is presently an end-to-end regional bootstrap. A generated deployment bundle must specify which processes it starts, how the web build receives the public manifest, and how API and executor share the admitted region and isolated state; merely pointing an operator at an existing compose file does not satisfy the setup requirement.
