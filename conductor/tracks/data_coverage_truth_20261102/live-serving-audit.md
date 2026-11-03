---
type: verification-evidence
---
# Live serving contract audit — 2026-11-02 America/Denver

Read-only public API probes against https://plantgeo.aevani.com. Captures occurred at approximately 21:11–21:15 MST (November 3 UTC). The visual browser pass is pending: browser inventory was empty and opening an IAB tab returned Browser is not available. This file does not claim rendered-UI verification.

## Results
- Readiness returned HTTP 200, ready, configuration/database/Redis true on custom and Railway domains.
- Slider capabilities returned HTTP 200 with per-layer observed dates, gaps and governed absences.
- Weather November 2 selected-day query returned ready, servedDay November 2, non-truncated, one sampled point in the bounded Portland-area viewport.
- Weather January 1 2018 selected-day query returned ready with an actual historical row, source Open-Meteo Historical Forecast (best_match, hourly), ingested August 26 2026.
- Vegetation requested November 2 returned ready with servedDay October 26 and distinct per-cell observation dates. The UI must disclose that time difference and not borrow it for the separate satellite source.
- Climate October 29 in the Portland-area viewport returned published with four cells.
- The same climate date in a New York viewport returned unavailable/not_published, confirming the source-traced conflation of no samples in a viewport with no publication.
- Shortwave capability latest observed August 31; explicit gap September 1 through October 25. Advertised unavailable reason was null, so interpretation must inspect dates/gaps instead of treating a present capability as fresh data.

## Correction to earlier weather assessment
The earlier assessment identified a collision between current acquisition and historical coverage obligations. The live query now proves retained historical weather is actually served back to 2018. Therefore the history floor must not simply be shortened to 2026. Keep existing data accessible and separate current-writer acquisition capability from historical release lineage/repair ownership in follow-up. The earlier code-only claim of an incorrect historical floor was too broad.

## Source-code findings queued in lane A
1. Weather/measured vegetation notices drop reader truncation, governed absence/not-generated state, and raw query errors.
2. Satellite vegetation's drawn-date report incorrectly derives from measured Parquet data.
3. Ready climate responses without viewport rows are labeled not_published.

## Reproducibility
Ignored raw evidence:
- .omc/research/live-serving-probe-2026-11-02.json
- .omc/research/live-layer-probe-2026-11-02.json

Inputs: bounded bbox -123,45,-122,46 (Portland area), comparison -75,40,-74,41, zoom9; dates named above. Queries read stored serving data and did not trigger ingestion. Initial array-format bbox probes were rejected with HTTP400; corrected documented string inputs generated the results above.

## Follow-up rendered Playwright pass

The initial browser limitation above was resolved after the owner requested Playwright. No dedicated Playwright MCP was exposed; the installed Playwright driver ran through an approved shell network path after the Node tool's browser network was denied. Headless Chromium rendered the live site at 1440 by 1000. This is a bounded desktop production pass, not a full responsive or authenticated-product certification.

- Initial satellite basemap and layer manager rendered with no uncaught page errors. Unavailable parcel, utility-territory and state-land datasets were explicitly labeled unavailable.
- Enabled Wind & Weather, Solar radiation and measured Vegetation individually, and opened their reports. API replies were HTTP 200. Weather returned 98 aggregate cells without truncation; solar reported 343 measured cells; the displays explained their aggregate support.
- The mixed-date notice accurately identified weather November 3, solar August 31 and vegetation October 26, a 64-day span. Solar explicitly disclosed its August 31 served edge, October 25 source ceiling and 55-day publication lag.
- Selected January 1 2018 through the weather date textbox: the report retained that date and showed 98 historical aggregate cells. Selected October 22 2026: the report said no weather was published and nothing was drawn. The mixed-date composite then excluded the undrawn weather layer. The availability control separately disclosed incomplete coverage on that date.
- Found a real distance-unit defect: the weather report displayed approximately 38,885.8 km within the PNW viewport. The helper returns meters. The local fix converts once to kilometers; a known-distance regression verifies 111.2 km for one latitude degree, nearest selection, and zero distance after marking. Independent targeted source review accepted the fix. The production screenshot still represents the pre-release defect.
- Switched measured vegetation to Satellite (MODIS). Production continued to show the measured October 26 date in the drawn-day badge and measured-grid gaps in the date control, while describing the satellite layer as gap-free. The local source-specific changes separate the requested composite period from an unknown acquisition date; follow-up copy explicitly identifies the remaining date-control catalogue as measured history and removes the unsupported gap-free claim.

Additional ignored evidence: `live-ui-initial.png`, `live-ui-layers.json`, `live-ui-data.png`, `live-ui-data.json`, `live-ui-responses.json`, `live-ui-history.json`, `live-ui-history-responses.json`, and reproducible `live-ui-playwright.cjs` under `.omc/research/`.

Satellite evidence is in `live-ui-satellite.json` and `live-ui-satellite.png`, reproduced by `live-ui-satellite.cjs`.

Post-deployment verification of the new notices and distance correction remains required. Tooltip detail, mobile layouts, offline reload behavior and authenticated agent execution were not exercised in this bounded pass.
