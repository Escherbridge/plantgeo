---
type: track-plan
track: pnw_land_data_delivery_20260920
status: in_progress
---

# Plan

Current corrective review, quality gates and deployment milestones are recorded in
[the PR #10 review evidence](evidence/pr10-review-deployment-20260921.md).

- [x] Implement per-product publication availability and independent crop display.
- [x] Repair stable boundary/contact identity and preserve source provenance.
- [x] Bind PNW sources; retain explicit unavailability outside PNW.
- [x] Apply all implementation fixes, then run integrated checks and independent review.
- [x] Ingest validated products into the existing production warehouse; verify readback.
- [x] Open [PR #10](https://github.com/Escherbridge/plantgeo/pull/10) with ingestion evidence and explicit follow-up tracks.
- [x] Complete the current independent PR review, resolve publication-ladder/contact/render-loop findings, and record the final integrated verification results.
- [x] Merge PR #10 and record its exact merge revision.
- [ ] Verify Railway deployment of that revision for the frontend, data service and job executor, including service health.
- [ ] Verify live BLM/crop API availability and each admitted crop edition against the deployed code.
- [ ] Verify browser controls, selected editions, unavailable-family notices and map rendering against the deployed code.
- [ ] Under a separate activation decision, activate the four new scheduled definitions in the production active-lane allowlist after deployed-code and readback acceptance, preserving existing active lanes.
- [ ] Collect the first successful scheduled turn for all four definitions before closing this track.

The current owner request authorizes PR review, relevant track updates, merging and deployment
monitoring. [PR #9](https://github.com/Escherbridge/plantgeo/pull/9) was already merged at
`2026-09-13T16:46:48Z`; [PR #10](https://github.com/Escherbridge/plantgeo/pull/10) merged at
`2026-09-21T04:53:52Z` as `fcadc3536cb46eae58c37c0e1442733249f2c43e`, following reviewed commit
`0a9ecabebf9b53a100ad1ef8437a4fc121a479e6`.
The corrective batch resolved the physical publication-ladder, hidden-contact-state and
render-loop findings. Independent source review, frontend verification and the isolated full
Python sweep with Docker-compatible receipt verification passed; exact scope and evidence
are recorded above. Railway deployment is in progress; service-health and API acceptance remain
pending, and no schedule activation is claimed by this update.

Browser acceptance is independently pending: `cua.getBrowser` reported no available browser
in this session. Post-deployment API and readiness checks will be collected separately and
cannot substitute for live browser evidence. The current request covers review, track updates,
merging and deployment monitoring; it does not activate lanes, change allowlists or trigger
new ingestion. Schedule activation and first scheduled turns remain follow-up gates.

Implementation and initial production ingestion are verified in
[the September 21 delivery evidence](evidence/production-delivery-20260921.md).
That evidence records the initial publication and its implementation review. The current
corrective review and checks are recorded separately in the PR #10 review evidence above.
The track remains open for deployment, live acceptance and schedule acceptance;
initial production data publication does not establish that the frontend or executor definitions
are deployed.
