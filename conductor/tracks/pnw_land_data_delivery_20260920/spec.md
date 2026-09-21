---
type: track-spec
track: pnw_land_data_delivery_20260920
status: in_progress
---

# PNW land data delivery

Integrate real BLM reference data and source-derived USDA crop cover, remove unavailable switches, and publish verified artifacts.

Owner authorization: 2026-09-20 request to implement in an isolated worktree, maximize sourced data and reasonable estimates, remove unavailable frontend switches, create a PR and ingest production data as needed. This supersedes the earlier implementation-only limitation for BLM and crop sources. Existing unresolved parcel/utility source restrictions remain explicit follow-up work.

Data contracts: governed Parquet publication; observed source classifications distinguished from forecasts; source year, release day and capture time remain separate; no fabricated parcels, service territories, ownership or responsible contacts. Outside-source geography remains unavailable. UI switches depend on actual readable publication, not registration alone.

Verification: source evidence, meaningful domain tests, full type/lint/boundary checks, affected Python tests and an independent review after the complete implementation batch. Production writes stay within the new product prefixes and use the ordinary publication locks and completion protocol. PR creation does not authorize merging main.
