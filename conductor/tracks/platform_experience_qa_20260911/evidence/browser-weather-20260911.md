---
type: evidence
slug: platform-experience-qa-browser-weather
observed_at: "2026-09-12T04:33:25Z"
status: partial
source_candidate_commit: 599f3e4ff66bc62049360aef430723563c0bf1fb
source_candidate_tree: 5b2251f6bb76061696ef40b70bb303e04b96d339
owner_track: platform_experience_qa_20260911
---

# Browser evidence: historical weather presentation

This is a root-QA observation of the immutable integrated candidate. It is
local browser evidence only; it authorizes neither deployment nor production
data mutation.

## Environment

The PlantGeo map was opened at `http://localhost:3001/` in the Codex in-app
browser on the available fixed 1280x720 desktop viewport. The running page was
the PlantGeo 3D map with the Map Manager open. No production credentials,
Railway resource, production database or object store was accessed.

## Observed behavior

1. The Climate group exposes **Wind & Weather** as a climate layer. Enabling it
   exposes the layer opacity control and the explicit `RETRYING`/unavailable
   state; the map did not render the former spaced square placeholders while
   the data service was unavailable.
2. Opening **Climate & Weather History** renders a dedicated **Historical
   weather** report card. The card identifies the selected day (`2026-09-12`),
   source/product (`Open-Meteo historical estimates`) and units (`SI units`).
3. With the governed data service unavailable, the report says: “Historical
   weather is temporarily unavailable from the data service. No fallback frame
   is shown.” The map also shows the matching temporary-unavailability banner.
   This verifies an explicit terminal state and the stale-frame guard rather
   than substituting a neighboring day or painting an invented frame.
4. The report is placed under the Climate & Weather History section instead of
   presenting raw square glyphs as the only user-facing explanation.

## Verdict and limits

The candidate passes the available desktop unavailable-state/browser check for
the requested traditional report treatment and no-stale-frame behavior. The
receipt remains **partial** because the local governed Parquet reader and
slider-capability/data service were unavailable: live raw-point and
aggregate-cell temperature labels, wind-label collision behavior, precipitation
hover, and real selected-day transitions could not be visually exercised. The
browser surface has a fixed desktop viewport, so narrow-mobile reflow and touch
acceptance remain pending. The owner test suite and integration checks are the
separate automated evidence recorded in
`integrated-weather-botanical-candidate-2026-09-11.md`.
