# planes (L4)

The serving surface: bounded readers over published artifacts and forecast partitions, and the
blueprints mounted at `/api/v1/ml`. Named `planes` rather than `serving` to match the lattice
agri-data-service already enforces, so one lattice vocabulary covers both services.

Import rules: may import `foundation`, `method`, `warehouse` and `pipeline`; may NOT import
`interface`.

A missing partition is a typed refusal naming the reason, never a fallback to another store and
never a fabricated zero. Every response carries `artifact_sha256`, `issued_on` and
`claim_tier: evaluation_only`. Only the placeholder route exists until phase 2 (plan.md, 2C).
