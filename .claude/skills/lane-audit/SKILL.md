---
name: lane-audit
description: >
  Audit every PlantGeo data lane end to end -- declared source limits, census coverage and
  freshness, the last ledger turn, provider usage and error rates, open incidents -- and cross-check
  each layer against what the web slider serves, then log a dated report and propose or apply fixes.
  Use when asked to "audit lanes", "check all lanes", "lane health", "why is a layer behind",
  "why is a layer withheld", "are the lanes healthy", or after a deploy or provider incident.
---

# Lane audit

The standing instructions are `services/agri-data-service/docs/lane-audit/BASE_PROMPT.md`. **Read it
in full before step 1**; this skill is the walk, the base prompt is the rulebook (safety rules,
classification table, fix playbook, report format).

## The walk

1. **Run the verb in production** (read-only, safe to repeat):
   ```sh
   mkdir -p .omc/research/lane-audits/raw
   railway ssh --service plantgeo-job-executor -- sh -c 'cd /app/agri-service; agri-service ops lane-audit --format json' \
     > .omc/research/lane-audits/raw/$(date -u +%F)-agri.json
   ```
   Read `section_errors` and `activation.active_lane_count` first; a lane reading `unknown` had no
   evidence, not good evidence. Add `--days 14` when a weekly lane matters.
2. **Fetch the web payload**:
   ```sh
   curl -s https://plantgeo.aevani.com/api/trpc/environmental.getSliderCapabilities \
     > .omc/research/lane-audits/raw/$(date -u +%F)-web.json
   ```
   Payload at `.result.data.json`: `layers[]` and `withheldParquetCapabilities[]`.
3. **Cross-check per layer** (base prompt, step 3): web `latestObservedDate` equals census
   `latest_recorded_day` unless that day is in `thinRanges`; every withheld `reason` maps to a real
   agri cause.
4. **Classify** each flag as expected / transient / defect / owner decision, using the base prompt's
   expected-lag table (climate today-5, ERA5-Land soil ~today-6..-9, NDVI ~7, sensors today-1 but
   today-2 before ~03:20Z, crop-cover and MTBS annual, static snapshots).
5. **Log** to `.omc/research/lane-audits/<YYYY-MM-DD>.md` in the base prompt's format, citing fields.
6. **Fix** per the playbook. Read-only checks and code fixes in the working tree are fine. Anything that
   mutates production (`--apply`, lane enable/supersede, Railway variables, keys, deploys, pushes) needs
   an explicit owner go in this conversation -- otherwise list the exact command under "Proposed".

## Quick reference

- Flag rules and thresholds: `services/agri-data-service/src/agri_data_service/execution/lane_audit.py`
  (named constants at the top); design notes: `execution/AGENTS.md`, "Lane audit".
- Gap repair: `agri-service ops jobs-plan-gap-repair [--lane ID]` (dry run) -- see skill `agri-pipelines`.
- What a finished lane must have (history floor, forward refresh, gap detection, governed absence,
  serving reader, freshness labels): skill `layer-lane-standard`.
- Withheld-reason wording the user sees: `src/components/map/layer-panel/layer-time-state.ts`.
- Never print a key; check presence by length only.
