---
type: evidence
slug: platform-experience-qa-case-inventory
observed_at: "2026-09-12T01:46:35Z"
status: not_run
source_main_commit: 26b69d386946308830bc202cd6e773f03a4d23a4
author_task: /root/surface_inventory_author
owner_track: platform_experience_qa_20260911
---

# PlantGeo experience acceptance case inventory

This is an authored inventory, not an execution report. Source inspection establishes intended behavior and reachability candidates only. **No case is passed.** The UTC observation is September 12; the owning track retains its September 11 local-date identifier. Main was inspected at the commit above; uncommitted track/planning files and incoming worktrees are source evidence, not an integrated candidate. Freeze a candidate and bind its commit, tree, dirty diff, review base, service revisions and fixture/release identities before running any case.

The governing requirements are [QA specification][qa], [dimension/evidence matrix][matrix], [layer lane standard][lane] and the feature sources cited in each group/row. A source-derived expected result can expose a defect or an unresolved requirement; it cannot approve itself. An independent verifier must review coverage and execution in a different task context.

## How to execute and record a case

Every row inherits the following dimensions unless explicitly narrowed. These are required execution cells, not claims that all combinations have already been tried. Create evidence records keyed by the stable row ID plus a meaningful variant suffix, for example `PGQA-L01.mobile-keyboard.historical`. Enumerate the materially different variants below; do not multiply unrelated dimensions into a Cartesian product.

| Dimension code | Required concrete expansion and evidence |
| --- | --- |
| `U` user interface | Desktop mouse and keyboard, mobile emulated touch plus keyboard; portrait/landscape when structure changes. Record browser/version, viewport, device/emulation and input. Run screen-reader names/announcements, visible focus, focus order/return, Escape, 200% zoom/reflow, contrast/non-colour cues, reduced motion and touch-target checks. Physical-device proof remains separate. |
| `R` role | Anonymous, synthetic viewer, contributor, expert and admin for public surfaces; permitted role plus anonymous/insufficient-role/other-owner denial for protected surfaces. Record role and fixture ID, not credentials. Organization roles and platform roles are separate axes. |
| `S` state | Initial/loading, populated, legitimate empty, missing/unavailable, stale, error, retry, cancel, refresh/deep-link and navigation recovery as applicable. Record why an inapplicable variant is excluded; no silent exclusions. |
| `C` cache | Named cold and warm local browser/application cache; request count/bytes, catalogue latency, TTFB and request-to-paint where relevant. State whether data cache is measured or unknown. Never claim a cold upstream from a browser reset. |
| `T` selected time | Latest readable, populated historical, governed-empty, missing/unwritten and outside-domain days; rapid day/place changes and delayed replies. Use each layer's own selected day. Static/reference products retain their release and do not acquire invented observation history. |
| `V` map/canvas | Original screenshots and canvas/pixel evidence with camera/bbox, zoom/rung, selected geometry, style, layer/opacity, day, release and capture timing. Compare coarse/middle/detail support, rung handoff, seams, overlaps, clipping and domain edges. Successful HTTP or DOM presence alone is insufficient. |
| `A` agent parity | UI and tool/final answer use identical role, selected place/area, per-layer day/window, filters, units and immutable release/run. Compare exact values, support, provenance, missingness, neighbours/refusals. An unavailable tool is an explicit result, not permission to improvise an answer. |

`U/R/S/C` applies to every UI row. `T/V/A` in a row adds the named dimension; `ref` means pinned static/reference release and honest unsupported-history behavior. API/MCP rows use `R/S/C/A` with bounded machine inputs and compare the corresponding UI where one exists. Accessibility is an execution requirement even when a row does not repeat the word.

For each run, add steps, preconditions, actual observations, UTC times, candidate/service/fixture identities, original capture paths and hashes, request/response traces, console/accessibility observations, performance measurements, defect/blocker links, author and independent-verifier task IDs. Evidence stays under this directory; retain credentials and personal data nowhere in receipts. Status vocabulary is `not_run`, `blocked`, `fail`, `pass`, `not_applicable`; the last requires an accepted scope reason. A blocked prerequisite is not a behavioral failure or pass.

All write journeys below mean **isolated local synthetic workflow mechanics** with a disposable QA database, verified local endpoints and external email/notifications disabled. They never authorize deployed-service writes, contact with people, seeding intervention records, or training publication. Create local recommendations through the contributor UI and explicit consent. `PGQA-C16` retains the separate real-human gate, which synthetic execution cannot satisfy.

## Owner and dependency keys

| Key | Feature/fix owner and dependency |
| --- | --- |
| `QA` | `platform_experience_qa_20260911`; coordinating task `/root` owns execution assignment and inventory reconciliation. Shared application fixes go to the integration owner, whose live task/base/tree must be bound in the ledger. Feature ownership not established by inspection remains an explicit intake question. |
| `DATA` | QA intake plus [Parquet production acceptance][production], current environmental reader/render owners. Local evidence cannot close production or burn-in gates. |
| `COMM` | [Community engagement completion][community-spec]; bind contributor/publication owner. August 5 source counts and original mount findings are historical, not current measurements. |
| `BOUND` | Incoming `intervention_boundary_authoring_20260911`, branch `codex/intervention-boundary-publication-20260911`, observed worktree `C:/Users/atooz/.codex/worktrees/2f1d/plantgeo` at worktree-list head `89e8494`. Shared MapView publication-sync/doc packets need integration and immutable intake. [Incoming specification][boundary-spec]. |
| `BOT` | [Botanical species profile lookup][bot-spec], branch `codex/botanical-species-profile-lookup`, observed worktree `C:/Users/atooz/.codex/worktrees/0caf/plantgeo` at worktree-list head `edc6afd`; require pinned release, integration receipt and independent review. |
| `OCC` | [Botanical occurrence experience][occ-spec] depends on [occurrence plane][occ-plane] and [PNW herbarium source admission][herbaria]. No current-main occurrence layer is established by this inventory. |
| `WX` | [Weather forecast experience][wx-spec] and [forecast plane][wx-plane], incoming branch `codex/weather-forecast-20260911`, observed worktree `C:/Users/atooz/.codex/worktrees/32be/plantgeo` at worktree-list head `09450d2`. Require admitted product, integration and exact run/time fixtures. |
| `PNW` | [PNW land/contact planned packet][pnw-spec] and [reference plane/source admission][pnw-plane]. Planning, public-route research and a reachable source page are not mounted UI, admitted data or authority to contact anyone. |

Worktree-list hashes identify observations only; they are not assertions that the worktrees were clean or that the cited heads contain every incoming edit. Reconcile full hashes, diffs and receipts at intake.

## Routes: all 23 current page files

For each route, navigate from a real link where one exists, open its URL directly, refresh, use back/forward and exercise the stated roles. Source paths mirror the route under [src/app][app]; dynamic route parameters require valid, malformed, absent/expired and foreign-owner fixtures.

| ID | Route / source requirement | Journey and expected result | Dimensions / owner | Status |
| --- | --- | --- | --- | --- |
| PGQA-R01 | `/` — [map entry][map-page] | Open cold map; loading resolves into an operable map, manager and truthful layer state; map init failure offers working recovery. | U/R/S/C/V; QA | not_run |
| PGQA-R02 | `/feed` — [feed][feed] | Browse intervention feed, type filters and map links; allowed records and lifecycle meaning are accurate, empty/error states distinct. | U/R/S/C/V; COMM | not_run |
| PGQA-R03 | `/community` — [ledger][ledger] | Browse strategy requests by type/private/team scope; role prompts and request/vote counts reflect authorized scope. | U/R/S/C; COMM | not_run |
| PGQA-R04 | `/about` — [app][app], `about/page.tsx` | Follow principles/mission links and anchors; content is readable on mobile and claims do not promise unimplemented features. | U/R/S/C; QA | not_run |
| PGQA-R05 | `/docs` — [API tester][api-tester] | Read documented parameters/errors and operate all four tester endpoints locally; result/status readable and request matches selection. | U/R/S/C/A; QA | not_run |
| PGQA-R06 | `/embed` — [embed][embed] | Load frameless map with valid/default/invalid center, zoom, style and markers; bounded parsing and attribution, no extra shell. | U/R/S/C/V; QA | not_run |
| PGQA-R07 | `/login` — [auth][auth] | Successful/incorrect/unverified/expired-session login and callback return are explicit; pending state prevents accidental duplicates. | U/R/S/C; QA | not_run |
| PGQA-R08 | `/register` — [auth][auth] | Submit valid and invalid local account forms; password confirmation, duplicate identity and verification instructions are truthful. | U/R/S/C; QA | not_run |
| PGQA-R09 | `/forgot-password` — [auth][auth] | Submit local reset request for known/unknown identity with email sink disabled; acknowledge safely without account disclosure. | U/R/S/C; QA | not_run |
| PGQA-R10 | `/reset-password` — [auth][auth] | Valid, expired, malformed and reused token plus mismatched passwords; success permits login, failure preserves useful feedback. | U/R/S/C; QA | not_run |
| PGQA-R11 | `/verify-email` — [app][app], `(auth)/verify-email/page.tsx` | Valid/expired/reused/missing verification token produces an accurate terminal state and next action. | U/R/S/C; QA | not_run |
| PGQA-R12 | `/onboarding` — [onboarding][onboarding] | Signed-in user creates or joins organization; anonymous redirect and completed-onboarding re-entry preserve intended destination. | U/R/S/C; QA | not_run |
| PGQA-R13 | `/invite/[token]` — [app][app], `invite/[token]/page.tsx` | Preview and accept eligible local invitation; expired/revoked/wrong identity does not reveal membership or join silently. | U/R/S/C; QA | not_run |
| PGQA-R14 | `/join/[code]` — [app][app], `join/[code]/page.tsx` | Preview and join via valid code; malformed/exhausted/revoked/rotated code is rejected with useful recovery. | U/R/S/C; QA | not_run |
| PGQA-R15 | `/dashboard` — [dashboard][dashboard] | Workspace navigation, sign-in gate and intentionally standalone chrome work; all section links resolve. | U/R/S/C; QA | not_run |
| PGQA-R16 | `/dashboard/org` — [organization][org] | Active organization overview, type/details/counts and no-membership state match selected organization. | U/R/S/C; QA | not_run |
| PGQA-R17 | `/dashboard/org/members` — [organization][org] | List members; authorized role/remove/leave actions and owner protections work without cross-team access. | U/R/S/C; QA | not_run |
| PGQA-R18 | `/dashboard/org/invitations` — [organization][org] | Local invitation and join-link management presents status, one-time secrets and revoke/rotate outcomes correctly. | U/R/S/C; QA | not_run |
| PGQA-R19 | `/dashboard/org/settings` — [organization][org] | Edit allowed organization fields and tags, save/reload, deny insufficient role and cross-team changes. | U/R/S/C; QA | not_run |
| PGQA-R20 | `/dashboard/conversations` — [conversations][conversations] | List only own conversations, newest/empty/error states and working detail links. | U/R/S/C/A; QA | not_run |
| PGQA-R21 | `/dashboard/conversations/[id]` — [conversations][conversations] | Read own conversation with citations/feedback; missing and foreign IDs do not expose messages. | U/R/S/C/A; QA | not_run |
| PGQA-R22 | `/moderation` — [moderation route][moderation] | Expert/admin enter canonical contribution queue; anonymous/other roles are redirected and API authority independently holds. | U/R/S/C; COMM | not_run |
| PGQA-R23 | `/admin/jobs` — [jobs][jobs] | Admin sees local lane ledger/history/exhausted gaps; lower roles denied; no production lane commands. | U/R/S/C; DATA | not_run |

## Global shell, identity and accessibility

Sources: [ApplicationShell][shell], [TopBar][topbar], [auth forms][auth], [root layout][app].

| ID | Journey and expected result | Dimensions / owner | Status |
| --- | --- | --- | --- |
| PGQA-G01 | Navigate Map, Feed, Community, About, Dashboard, organization and role-visible Moderation from desktop navigation; active state follows exact route/children. | U/R/S/C; QA | not_run |
| PGQA-G02 | Open/close compact navigation by touch and keyboard; Escape, focus restoration and route selection work without covering unreachable content. | U/R/S/C; QA | not_run |
| PGQA-G03 | Compare global chrome on ordinary routes with frameless auth/onboarding/invite/join/embed and exact `/dashboard`; descendants get their intended shell. | U/R/S/C; QA | not_run |
| PGQA-G04 | Skip-to-content, document landmarks/headings, titles, link names, focus rings and zoom/reflow remain useful on every route family. | U/R/S/C; QA | not_run |
| PGQA-G05 | Anonymous → login → role-bearing session → sign-out; identity menu, protected links and cached private data update together. | U/R/S/C; QA | not_run |
| PGQA-G06 | Session expires during form/query; user receives appropriate reauthentication and no false success or stale privileged action. | U/R/S/C; QA | not_run |
| PGQA-G07 | Role/organization changes while a page is open; cached data and available controls follow new authority, server denies stale actions. | U/R/S/C; QA | not_run |
| PGQA-G08 | Return/callback URLs preserve legitimate in-app destinations and reject hostile/external redirect input; invitation context survives login. | U/R/S/C; QA | not_run |
| PGQA-G09 | Error notices, toasts, dialogs, validation summaries and loading indicators are announced, dismissible and do not rely on colour. | U/R/S/C; QA | not_run |
| PGQA-G10 | Refresh/back navigation, unknown route and network interruption leave a recoverable interface without duplicate writes or leaking prior identity state. | U/R/S/C; QA | not_run |

## Map manager and canvas controls

Sources: [MapView][map-view], [manager][manager], [view controls][view], [keyboard shortcuts][shortcuts], [map registry][registry].

| ID | Journey and expected result | Dimensions / owner | Status |
| --- | --- | --- | --- |
| PGQA-M01 | First visit starts with current default layer/manager state and a dismissible discovery hint; dismiss/open persistence survives reload and storage denial is handled. | U/R/S/C/V; QA | not_run |
| PGQA-M02 | Open/close manager and navigate six layer categories plus Teams/Offline; one scroller exposes every row/report on desktop and mobile. | U/R/S/C/V; QA | not_run |
| PGQA-M03 | Expand/collapse each category/report; report mount/unmount starts/stops relevant reads while layer visibility remains independently correct. | U/R/S/C/V; QA | not_run |
| PGQA-M04 | Toggle group eyes through none/some/all; accessible mixed state/count excludes withheld entries and changes only that category. | U/R/S/C/V; QA | not_run |
| PGQA-M05 | Adjust individual layer visibility and opacity including 0/100%, close manager and inspect rail chips/legend; one control determines drawn state. | U/R/S/C/T/V; QA | not_run |
| PGQA-M06 | Switch Dark/Light/Satellite repeatedly with overlays selected; sources, feature selection, opacity, labels and attribution recover without duplicates. | U/R/S/C/T/V; QA | not_run |
| PGQA-M07 | Toggle terrain and exaggeration 0–3; relief changes coherently without changing selected overlays; resource/loading failures disclosed. | U/R/S/C/V; QA | not_run |
| PGQA-M08 | Toggle globe and 3D tilt, pan/zoom/reset and cross world edge; correct camera/support/labels, no unexpected layer changes. | U/R/S/C/V; QA | not_run |
| PGQA-M09 | Exercise Ctrl/Cmd+K and registered r/t/g/1/2/3 shortcuts with dock closed/open; text fields suppress map shortcuts and focus reaches search. | U/R/S/C/V; QA | not_run |
| PGQA-M10 | Open manager during camera movement, rotate viewport and reduce motion; desktop padding/mobile overlay preserve location and useful controls. | U/R/S/C/V; QA | not_run |
| PGQA-M11 | Hover/select overlapping map features at each rung; tooltip identity, units and geometry match painted feature; dismissal and text equivalents work. | U/R/S/C/T/V/A; DATA | not_run |
| PGQA-M12 | Exercise map initialization failure, context loss/style reload, delayed tiles and retry; loading chips settle accurately and UI stays operable. | U/R/S/C/V; QA | not_run |
| PGQA-M13 | Follow map focus URL from feed and explicit coordinates; valid geometry/camera retained, malformed bounds ignored/rejected safely, reduced motion honored. | U/R/S/C/V; QA | not_run |

## Layer registry census: 27 entries

Every entry below requires toggle → visible painted result (or explicit withholding) → legend → selection/details → selected-time/agent reconciliation. Source of identity is [LAYER_REGISTRY][registry]; source of declared time nature/reader is [Parquet capabilities][capabilities], [time types][time-types] and [climate vocabulary][climate]. These are source declarations, not proof that a release exists. `not_run` includes testing honest unavailable behavior; it does not assume data readiness. A fixture requiring absent infrastructure becomes `blocked` at execution intake.

All layer rows inherit `U/R/S/C/T/V/A` except null-axis/reference products as stated. All own DATA + QA, with COMM additionally owning community rows. All require support limits, coarse/middle/detail zoom, camera/day changes, style swap and cold/warm captures. Per-signal rows remain distinct even when they share a renderer.

| ID | Toggle → capability / declared time | Expected specific result | Owner | Status |
| --- | --- | --- | --- | --- |
| PGQA-L01 | `fire` → `fire-detections`; event/daily Parquet | Detections show correct day, confidence/source and event meaning; no empty-as-no-fire assertion. | DATA | not_run |
| PGQA-L02 | `fire-perimeters` → same; event over static lookup | Correct perimeter boundary and capture/release semantics; no false instantaneous observation/history. | DATA | not_run |
| PGQA-L03 | `water` → `water-gauges`; daily series, selectable floor 2022-08-05 | Gauges, flow units/age and selected date agree; pre-floor unsupported state differs from zero flow. | DATA | not_run |
| PGQA-L04 | `drought` → `drought-areas`; daily selector over release series | Published weekly classification valid for requested day; release date not shifted by local timezone. | DATA | not_run |
| PGQA-L05 | `weather` → `weather-observations`; daily series | Current sampled product is not represented as a continuous forecast; clear unavailable day and stale frame. | DATA/WX | not_run |
| PGQA-L06 | `sensors` → same; snapshot over daily data | Fixed reference and supported station attributes disclosed; no invented scrubbable observation series. | DATA | not_run |
| PGQA-L07 | `watersheds` → same; snapshot/static lookup | Boundary support and HUC zoom rung are truthful; reference release shown without daily axis. | DATA | not_run |
| PGQA-L08 | `vegetation` → same; daily series | Measured NDVI cells and composite alternatives keep their product/time/source meanings distinct. | DATA | not_run |
| PGQA-L09 | `soil` → null; permanently withheld raster | Disabled row explains unpublished SoilGrids raster and point-query alternative; stored active ID cannot draw it. | DATA | not_run |
| PGQA-L10 | `soil-survey` → same; snapshot/static, declared PostgreSQL reader gate | SSURGO supported geometry and reference meaning; catalogue withholds unsupported Parquet claim rather than inventing readiness. | DATA | not_run |
| PGQA-L11 | `soil-moisture` → `soil-field-moisture`; daily, three-depth intersection | Correct moisture depth/support/unit and common readable days; no missing-depth substitution. | DATA | not_run |
| PGQA-L12 | `soil-temperature` → `soil-field-temperature`; daily, four-depth intersection | Correct temperature depth including 100–255 cm and common readable days; independent of moisture selection. | DATA | not_run |
| PGQA-L13 | `soil-vpd` → `soil-field-vpd`; daily | Atmospheric dryness in kPa, correct pseudo-depth and no claim of soil moisture measurement. | DATA | not_run |
| PGQA-L14 | `climate-air-temperature` → `climate-field-air-temperature`; daily, mean/max/min intersection | Separate statistic choice with correct units, fill/allowed contours and per-signal date. | DATA | not_run |
| PGQA-L15 | `climate-dew-point` → `climate-field-dew-point`; daily | Dew-point value, unit, supported form and independent availability match selected cells. | DATA | not_run |
| PGQA-L16 | `climate-precipitation` → `climate-field-precipitation`; daily | Daily amount/interval and observed zero distinct from missing; no unsupported contours. | DATA | not_run |
| PGQA-L17 | `climate-relative-humidity` → `climate-field-relative-humidity`; daily | Percent range, unit and availability agree in map, detail and answer. | DATA | not_run |
| PGQA-L18 | `climate-shortwave-radiation` → `climate-field-shortwave-radiation`; daily | Correct source unit/quantity and daily support; no sunlight-hours substitution. | DATA | not_run |
| PGQA-L19 | `climate-wind-speed` → `climate-field-wind-speed`; daily | Scalar wind-speed meaning and unit maintained; no invented direction or forecast. | DATA | not_run |
| PGQA-L20 | `climate-soil-wetness-surface` → `climate-field-soil-wetness-surface`; daily pilot | Surface wetness pilot coverage explicit; blank ground not dry ground, no unsupported contours. | DATA | not_run |
| PGQA-L21 | `climate-soil-wetness-root-zone` → `climate-field-soil-wetness-root-zone`; daily pilot | Root-zone meaning, source footprint and independent availability preserved. | DATA | not_run |
| PGQA-L22 | `climate-soil-wetness-profile` → `climate-field-soil-wetness-profile`; daily pilot | Profile meaning distinct from surface/root zone and unsupported cells remain explicit. | DATA | not_run |
| PGQA-L23 | `demand-heatmap` → null; current aggregate | K-anonymity-floored activity grid, no single private request location and no fabricated observation slider. | COMM | not_run |
| PGQA-L24 | `interventions` → same; community publication/date contract | Only authorized published interventions visible with correct geometry/state; pending/rejected remain private. | COMM | not_run |
| PGQA-L25 | `strategy-recommendations` → snapshot surface | Fixed recommendation reference/support and evidentiary limits; absent model output is not generated advice. | COMM/DATA | not_run |
| PGQA-L26 | `evacuation-zones` → same; snapshot/static lookup | Source-backed reference boundary and status/date meaning; no implied current evacuation instruction from stale reference. | DATA | not_run |
| PGQA-L27 | `burn-severity` → same; event/release series | Cumulative history at/before selected day, correct severity boundaries and publication date distinct from ignition. | DATA | not_run |

## Time, availability and data-truth journeys

Sources: [LayerTimeSlider/LayerTimeStatus][manager], [time store][time-store], [drawn metrics][metrics], [capability authority][capabilities], [lane standard][lane].

| ID | Journey and expected result | Dimensions / owner | Status |
| --- | --- | --- | --- |
| PGQA-T01 | Independently scrub two active layers to different days; each slider, request, drawn frame and detail retains its own day; summary reports mixed dates honestly. | U/R/S/C/T/V/A; DATA | not_run |
| PGQA-T02 | Latest → historical → latest, stepping dates by keyboard/touch and date input; requested, loading and actually drawn days cannot be conflated. | U/R/S/C/T/V/A; DATA | not_run |
| PGQA-T03 | Compare populated, governed-empty, unwritten, outside-coverage and withheld-catalogue fixtures; labels, neighbour options and blank canvas distinguish all. | U/R/S/C/T/V/A; DATA | not_run |
| PGQA-T04 | Rapidly change day, pan/location and signal with reordered delayed responses; only current request may paint or update selected details. | U/R/S/C/T/V/A; DATA | not_run |
| PGQA-T05 | Exercise snapshot/null-axis/withheld products alongside series; no fabricated common axis, latest date or history. | U/R/S/C/T/V/A; DATA | not_run |
| PGQA-T06 | Capability failure/stale/checksum-invalid/missing-rung/common-history fixture; catalogue fails closed and exposes usable explanation rather than false zero availability. | U/R/S/C/T/V/A; DATA | not_run |
| PGQA-T07 | Soil depth and temperature-statistic lane intersections disagree; selectable days reflect all required lanes, never a union that conceals gaps. | U/R/S/C/T/V/A; DATA | not_run |
| PGQA-T08 | Test release/date boundaries, UTC versus America/Denver, month/year/leap-day edges and source ceiling; labels and exact reader remain consistent. | U/R/S/C/T/V/A; DATA | not_run |
| PGQA-T09 | Request temporal/spatial neighbours from missing exact day/place; distance/offset and support are explicit and selecting one changes context deliberately. | U/R/S/C/T/V/A; DATA | not_run |
| PGQA-T10 | Repeat same camera/day cold and warm, then publish only a local fixture revision; freshness invalidation updates catalogue/data/legend together without claiming upstream coldness. | U/R/S/C/T/V/A; DATA | not_run |
| PGQA-T11 | Compare per-layer saved-on-device track before hydration, with no saved days and with disjoint saved runs; dotted sync bands, coverage bands and selected-day tick share an axis but never imply that published means downloaded. | U/R/S/C/T/V; DATA/QA | not_run |
| PGQA-T12 | Pin a day with date input/range, use Arrow/Home/End/Page and Shift+Page stepping, clear/invalid input and return-to-latest control; no double step, silent clamp mismatch or accidental unpin when latest catalogue advances. | U/R/S/C/T/V; DATA | not_run |

## Dock details and scientific interpretation

Sources: [panel implementations][panels] (`FireDetails`, `WaterDetails`, `VegetationDetails`, `SoilDetails`, `ClimateDetails`, `TeamDetails`), [panel conventions][panel-doc].

| ID | Journey and expected result | Dimensions / owner | Status |
| --- | --- | --- | --- |
| PGQA-D01 | Open Fire Dashboard at selected fire/place; fire/weather metrics, age and sources agree with location/time; unsupported conditions do not become certainty. | U/R/S/C/T/V/A; DATA | not_run |
| PGQA-D02 | Open Water Streamflow tab, select gauge/time and inspect metrics/chart; discharge unit, selected gauge, historical window and missing/zero readings agree. | U/R/S/C/T/V/A; DATA | not_run |
| PGQA-D03 | Open Water Drought tab; classification, categories, percentages/trend and publication age agree with mapped release. | U/R/S/C/T/V/A; DATA | not_run |
| PGQA-D04 | Open Water Watersheds at wide/narrow bbox; map can render while bounded basin list explains its coverage ceiling, not a false outage. | U/R/S/C/ref/V/A; DATA | not_run |
| PGQA-D05 | Vegetation measured-cells versus satellite composite selection; mutually exclusive paint, selected day versus composite month, palette qualification and missing period correct. | U/R/S/C/T/V/A; DATA | not_run |
| PGQA-D06 | Vegetation legend band endpoints and tooltip values align; index is not biomass/abundance or botanical specimen evidence. | U/R/S/C/T/V/A; DATA | not_run |
| PGQA-D07 | Soil point query and each property selection (pH, organic carbon, nitrogen, bulk density, CEC, organic carbon density); highlighted numeric property changes, depth/unit/source disclosed and raster unavailability stays separate. | U/R/S/C/ref/V/A; DATA | not_run |
| PGQA-D08 | Soil survey polygon/property details at support edge; geometry, map-unit identity and survey/model distinction correct with clear partial/unavailable state. | U/R/S/C/ref/V/A; DATA | not_run |
| PGQA-D09 | Soil moisture/temperature depth and VPD controls; one measure change cannot silently switch other active fields or units. | U/R/S/C/T/V/A; DATA | not_run |
| PGQA-D10 | Climate render-form/statistic controls across nine rows; allowed forms only, old point-form state normalized honestly, combined layers remain interpretable. | U/R/S/C/T/V/A; DATA | not_run |
| PGQA-D11 | Open Team Dashboard for no team/one/multiple teams; metrics, organization type/details and selected service area follow active membership. | U/R/S/C/V; QA | not_run |
| PGQA-D12 | Browse every chart/metric table with keyboard, screen reader and 200% zoom; textual values, units, axes, uncertainty and missing intervals remain available. | U/R/S/C/T/A; QA/DATA | not_run |
| PGQA-D13 | Keep details open while changing day/place/layer then collapse/reopen; stale cached report cannot be presented as the newly selected context. | U/R/S/C/T/V/A; DATA | not_run |

## Search, location and routing

Sources: [search dock][search], [reverse geocode][reverse], [routing components][routing-ui], [routing API][routing-api]. Routing component definitions were found, but no current parent imports mounting a complete routing panel were found. The visible reverse-geocode action still needs runtime verdict; do not treat an orphaned destination write as a completed journey.

| ID | Journey and expected result | Dimensions / owner | Status |
| --- | --- | --- | --- |
| PGQA-S01 | Search place names including short/blank, accented, long and no-match text; bounded autocomplete, loading/error, keyboard active option and geographic bias are truthful. | U/R/S/C/V; QA | not_run |
| PGQA-S02 | Choose house/street/city/state/country result; map flies to correct lon/lat and suitable zoom, recents persist/deduplicate/clear without stale query results. | U/R/S/C/V; QA | not_run |
| PGQA-S03 | Enter valid signed/zero/boundary `lat, lon` and invalid/non-finite/out-of-range pairs; coordinate jump is explicit and invalid input cannot corrupt camera. | U/R/S/C/V; QA | not_run |
| PGQA-S04 | Change query rapidly, clear/Escape then close manager; old autocomplete cannot reopen or override newer selection. | U/R/S/C/V; QA | not_run |
| PGQA-S05 | Right-click, long-press and keyboard location action; reverse-geocode address/coordinates match chosen point, popup tracks pan and dismisses with focus recovery. | U/R/S/C/V; QA | not_run |
| PGQA-S06 | Copy coordinates with allowed/denied clipboard; copied precision/order correct and failure not silently presented as copied; ingestion coverage badge accurately describes its scope. | U/R/S/C/V; QA | not_run |
| PGQA-S07 | Click visible “Directions to here”; user must reach a route-planning result or an explicit unavailable explanation, not a silent store-only change. | U/R/S/C/V; QA | not_run |
| PGQA-S08 | Full origin/destination/waypoint edit, reverse/reorder/remove, car/bike/pedestrian/truck modes, alternatives, summary and turn-by-turn route paint. Expected coherent end-to-end route with units and recovery. **Blocked: routing panel/route renderer mounting not established; integration owner must decide wiring/scope.** | U/R/S/C/V; QA | blocked |
| PGQA-S09 | `/api/v1/route` and tRPC route: valid/invalid coordinates, unreachable route, units, alternatives, upstream failure and limits produce documented bounded results. | R/S/C/A; QA | not_run |
| PGQA-S10 | tRPC isochrone: select point/costing/contours, compare returned support/time-distance units and inaccessible/invalid cases; no claimed UI control without mount. | R/S/C/A; QA | not_run |
| PGQA-S11 | tRPC matrix: sources/targets/modes preserve ordering and unreachable cells; limits/error states distinguish no route from service unavailable. | R/S/C/A; QA | not_run |

## Community, interventions and moderation

Sources: [community ledger][ledger], [community panels/forms][panels], [intervention router][intervention-api], [contribution router][contribution-api], [moderation route][moderation], [community requirements][community-spec]. Every mutation row uses synthetic local actors only.

| ID | Journey and expected result | Dimensions / owner | Status |
| --- | --- | --- | --- |
| PGQA-C01 | Submit strategy request with type/description/location and private/team scope; validation, consent/scope and success agree with persisted local record. | U/R/S/C/V; COMM | not_run |
| PGQA-C02 | Browse own/private/team requests and apply strategy filters; unauthorized teams/other owners remain inaccessible, filter counts and empty state truthful. | U/R/S/C; COMM | not_run |
| PGQA-C03 | Vote on eligible request and repeat/reload; count and eligibility follow API rules, denied role and failed mutation restore truthful state. | U/R/S/C; COMM | not_run |
| PGQA-C04 | Submit intervention recommendation through current form; valid type/text/geometry and explicit publication consent create pending review only. | U/R/S/C/V; COMM | not_run |
| PGQA-C05 | Missing consent, invalid/oversized geometry, invalid text and insufficient role block submission; pending/retry/double-click does not misrepresent outcome. | U/R/S/C/V; COMM | not_run |
| PGQA-C06 | Contributor list shows pending/published/rejected own submissions with accurate review note and team scope; other contributor records remain private. | U/R/S/C; COMM | not_run |
| PGQA-C07 | Expert opens canonical ContributionQueue and inspects geometry/provenance/consent; queue loading/empty/error and sorting identity remain usable. | U/R/S/C/V; COMM | not_run |
| PGQA-C08 | Expert publishes eligible local submission; lifecycle/result, contributor view and published feature agree after refresh; denied caller cannot bypass review. | U/R/S/C/V/A; COMM | not_run |
| PGQA-C09 | Expert rejects with review note; contributor sees outcome, feature remains absent publicly, retry/concurrent review gives truthful result. | U/R/S/C/V; COMM | not_run |
| PGQA-C10 | Local contributor → expert → contributor → anonymous map round trip; published geometry/attributes match original consented submission with no pending/rejected leakage. | U/R/S/C/V/A; COMM | not_run |
| PGQA-C11 | Two reviewers act concurrently or stale queue retries action; final state is coherent, no duplicate publication or misleading success. | U/R/S/C; COMM | not_run |
| PGQA-C12 | Feed type filtering and “view on map” preserve feature identity, geometry and lifecycle language; feed “proposed” is not confused with public contribution approval. | U/R/S/C/V; COMM | not_run |
| PGQA-C13 | Demand aggregation across bbox/zoom edges with <3 and >=3 contributors; no individual disclosure, k-floor and membership are bbox-independent. | U/R/S/C/V/A; COMM | not_run |
| PGQA-C14 | Copy/share request or intervention context; clipboard denial and cancellation handled, only authorized information copied and never automatically sent. | U/R/S/C; COMM | not_run |
| PGQA-C15 | Legacy `ModerationPanel` voting/lifecycle UI versus canonical ContributionQueue: identify intended reachability, quorum/lifecycle semantics and owner before exercising. **Blocked: no mounted parent established for legacy panel; do not substitute it for canonical publication.** | U/R/S/C; COMM | blocked |
| PGQA-C16 | Governing real-human contribution acceptance: a real contributor submits their own recommendation and expert outcome reaches submitter/map under that track's authority. **Blocked: synthetic local rows cannot satisfy this gate; real-human participant/authorization and separate evidence required.** | U/R/S/C/V; COMM | blocked |

## Organization and user workspace journeys

Sources: [organization routes][org], [onboarding components][onboarding], [TeamDetails/TeamPanel][panels], [service-area tool][draw-service], [teams router][teams-api].

| ID | Journey and expected result | Dimensions / owner | Status |
| --- | --- | --- | --- |
| PGQA-O01 | Create each offered organization type with valid/invalid fields, then enter workspace; resulting membership/role and one-time code disclosure are correct. | U/R/S/C; QA | not_run |
| PGQA-O02 | Join existing organization by code or invitation; anonymous callback, already-member, expiry/revocation and wrong identity all give coherent outcomes. | U/R/S/C; QA | not_run |
| PGQA-O03 | Switch among organizations and back to no active team; header, map service area, details, members, requests and invitations update as one scope. | U/R/S/C/V; QA | not_run |
| PGQA-O04 | Save organization name/type/description/tags and other surfaced settings; invalid or unauthorized updates preserve prior state and explain failure. | U/R/S/C; QA | not_run |
| PGQA-O05 | Edit service-area geometry, finish/cancel/clear and refresh; correct outline/mask remains below data, invalid geometry denied and prior saved area preserved on cancel. | U/R/S/C/V; QA | not_run |
| PGQA-O06 | Owner/admin update member role/remove/leave, including self/last-owner and concurrent change; privileges and member list stay consistent. | U/R/S/C; QA | not_run |
| PGQA-O07 | Create/revoke a local email invitation with outbound transport disabled; invite preview/status and existing-member/invalid address handling truthful. | U/R/S/C; QA | not_run |
| PGQA-O08 | Create/copy/rotate/revoke join link; one-time reveal and use/expiry constraints correct, old link fails without exposing new secret. | U/R/S/C; QA | not_run |
| PGQA-O09 | View Team Dashboard summaries and refresh while membership changes; counts correspond to chosen team and do not imply undisclosed data access. | U/R/S/C; QA | not_run |
| PGQA-O10 | Profile/change-password `UserPanel`: validate identity/password flow and own-team display once a user-visible mount exists. **Blocked: source component found without parent mount; assign scope/wiring owner.** | U/R/S/C; QA | blocked |

## Offline, sharing, upload and realtime

Sources: [OfflinePanel and LayerUpload][panels], [embed components][embed-dir], [SyncIndicator][sync], [stream route][stream], [websocket route][ws], [layer API][layers-api]. Source-only components are not treated as delivered UI.

| ID | Journey and expected result | Dimensions / owner | Status |
| --- | --- | --- | --- |
| PGQA-X01 | Download current viewport tiles at selected zoom bounds; estimated size/progress/completion and quota/network failure are accurate, cancellation if offered works. | U/R/S/C/V; QA | not_run |
| PGQA-X02 | Reopen cached area offline then pan beyond it; cached coverage and unavailable ground distinct, reconnect restores freshness without pretending all layers work offline. | U/R/S/C/T/V; QA | not_run |
| PGQA-X03 | Inspect storage and clear local cache via control; counts and retained app state truthful, denied storage/quota handled; never clear upstream/shared caches. | U/R/S/C; QA | not_run |
| PGQA-X04 | Queue permitted local offline change, reconnect and resolve keep-local/discard-local conflict; no loss/duplicate or unauthorized replay, status announcements accurate. | U/R/S/C; QA | not_run |
| PGQA-X05 | SSE layer subscription receives local fixture update, disconnect/reconnect and stale/duplicate message; layer freshness and sync chip reconcile, unmounted listener stops. | U/R/S/C/T/V; QA/DATA | not_run |
| PGQA-X06 | WebSocket realtime/tracking advertised API path: handshake/unsupported transport/auth/disconnect behavior explicit; no claim of a mounted tracking UI without evidence. | R/S/C/A; QA | not_run |
| PGQA-X07 | Upload valid/invalid GeoJSON and supported other offered formats, visibility/title validation, size/feature caps, create/select layer. **Blocked: `LayerUpload` has no discovered mount; inspect supported formats on candidate before execution.** | U/R/S/C/V; QA | blocked |
| PGQA-X08 | Layer API create/update/delete/reorder own layer, public/private/team listing and foreign-owner denial; validate request and rendering order contract separately from absent drag UI. | R/S/C/A; QA | not_run |
| PGQA-X09 | Generate/copy embed HTML with center/zoom/style/markers, preview and clipboard-denied recovery. **Blocked: `EmbedCodeGenerator` has no discovered mount; `/embed` itself is covered by R06.** | U/R/S/C/V; QA | blocked |
| PGQA-X10 | Read/export/share surfaced structured feature data through docs/API; bounds, pagination, permitted fields and units survive round trip, secrets/private records excluded. No standalone image/PDF/export button is asserted. | U/R/S/C/A; QA | not_run |

## Regional intelligence and every current agent/MCP tool

Sources: [AgentInteraction][agent-interaction], [RegionalIntelligencePanel][panels], [conversation routes][conversations], [agent tools][agent-tools], [MCP server][mcp], [agent surface catalogue][agent-surfaces]. The catalogue explicitly names 24 surfaces: the 11 feature capabilities represented by L01/L02/L03/L05/L06/L07/L08/L10/L24/L26/L27 plus drought and the 12 climate/soil streams. The 27 UI toggles additionally include withheld soil, demand aggregate and strategy snapshot; those must not be assumed to be registered agent observation surfaces.

For each of the ten tool rows, run the exact tool and the user's final answer, including invalid coordinates/dates/names, radius/window/limit bounds, legitimate empty, unwritten/withheld/unbuilt plane and delayed responses. Do not replace exact selected context with viewport centre or nearby evidence. Compare permitted roles and relevant UI counterparts.

| ID | Journey / capability and expected result | Dimensions / owner | Status |
| --- | --- | --- | --- |
| PGQA-A01 | Select location and inspect explicit analysis prompt; approximate default versus exact precision is disclosed, cancel sends nothing and repeat choice sends only selected precision. | U/R/S/C/V/A; QA | not_run |
| PGQA-A02 | Start regional analysis, stream/load/fail/cancel/retry; selected place/day context, evidence sufficiency, sources and missingness remain visible without fabricated conclusions. | U/R/S/C/T/V/A; QA/DATA | not_run |
| PGQA-A03 | Ask follow-up, follow cited evidence and select suggested strategy chips; conversation context and informational limits retained, no outreach/field action occurs. | U/R/S/C/T/A; QA | not_run |
| PGQA-A04 | Save/reopen conversation and set/change feedback; ownership enforced, messages/feedback persist accurately and failure does not masquerade as saved. | U/R/S/C/A; QA | not_run |
| PGQA-A05 | `signals_near_point`: bounded nearby signal series carries support, applied radius/window, source and units; summary is not exact-point observation. | R/S/C/T/A; DATA | not_run |
| PGQA-A06 | `drought_history_at_point`: selected historical classification/release/window and partial coverage agree with drought UI; no outside-US/empty false absence. | R/S/C/T/A; DATA | not_run |
| PGQA-A07 | `fire_history_near_point`: separate detections/burn-severity evidence, timestamps and spatial support; missing lane does not become no historical fire. | R/S/C/T/A; DATA | not_run |
| PGQA-A08 | `forecast_summary_for_cell`: existing forecast product/series/run and refusal are accurately identified; it must not be passed off as the incoming weather forecast. | R/S/C/T/A; DATA/WX | not_run |
| PGQA-A09 | `signal_value_on_day`: exact requested day/cell/signal and units; typed unavailability instead of silent latest substitution. | R/S/C/T/A; DATA | not_run |
| PGQA-A10 | `signal_neighbors_in_time`: before/after bounds, actual day offset and requested day explicit; neighbours never answer exact-day request silently. | R/S/C/T/A; DATA | not_run |
| PGQA-A11 | `nearest_signal_cells`: nearest represented supports and real distances, quality/limit disclosure; no centre-coordinate substitution. | R/S/C/T/A; DATA | not_run |
| PGQA-A12 | `observation_coverage_on_day`: enumerate all 24 catalogue surfaces, exact day, whole-history authority, governed states and intervention-specific handling; reject unsupported surface names. | R/S/C/T/A; DATA/COMM | not_run |
| PGQA-A13 | `observation_temporal_neighbors`: all applicable surfaces have truthful earlier/later releases and offsets, empty/out-of-window state distinct from absence. | R/S/C/T/A; DATA | not_run |
| PGQA-A14 | `feature_value_near_point`: source-typed properties, feature/geometry support and served day; intervention publication/access exception remains protected. | R/S/C/T/V/A; DATA/COMM | not_run |
| PGQA-A15 | MCP initialize/ping/tools-list/tools-call discovers exactly ten current tool schemas, protocol negotiation and malformed/unknown method handling remain usable after an error. | R/S/C/A; QA | not_run |
| PGQA-A16 | MCP typed refusal is returned as refusal content; malformed call differs from transport failure, diagnostics cannot corrupt stdio and unavailable tools are named explicitly. | R/S/C/A; QA | not_run |
| PGQA-A17 | Compare UI/agent on all 24 catalogue surfaces and explicitly unsupported soil/demand/strategy products; release/unit/support/role/time claims match and incomplete evidence is not a recommendation. | U/R/S/C/T/V/A; DATA | not_run |

## Public API, imagery and operational surfaces

Sources: [API route tree][api], [API documentation/tester][api-tester], [jobs dashboard][jobs], [tRPC routers][routers]. These are surfaced contracts even when no separate map widget exists. Service-only health/ingest endpoints are not claimed as user screens.

| ID | Journey and expected result | Dimensions / owner | Status |
| --- | --- | --- | --- |
| PGQA-P01 | Docs tester layers/features read: API key absent/valid/revoked, bbox/layer/limit/offset and authorization return documented records/errors without key echo. | U/R/S/C/A; QA | not_run |
| PGQA-P02 | Public geocode/reverse and tester geocode: Unicode, empty/invalid/large input, upstream failure and pagination/limits preserve schema and location order. | U/R/S/C/A; QA | not_run |
| PGQA-P03 | Location-context API matches chosen coordinates/day/window and reports each environmental source's availability/provenance rather than mixing days. | R/S/C/T/A; DATA | not_run |
| PGQA-P04 | Action-network API and public/team feature access honor aggregation/visibility, bbox edges and role; hidden records cannot be inferred from counts. | R/S/C/A; COMM | not_run |
| PGQA-P05 | Teams public/API contract returns only allowed team fields and membership scope; invalid key/identity/path handling explicit. | R/S/C/A; QA | not_run |
| PGQA-P06 | API-key lifecycle endpoints with local synthetic owner/admin: create/list/revoke/scope, one-time reveal and subsequent denial; no mounted key-management UI assumed. | R/S/C/A; QA | not_run |
| PGQA-P07 | Imagery/sequence/image/Mapillary tile contracts return source attribution, bounds, missing/configuration/rate-limit states; distinguish API capability from an unmounted street-view UI. | R/S/C/V/A; QA | not_run |
| PGQA-P08 | Admin jobs read/history/exhausted gaps/refresh and local-only toggle/trigger: state, failure and authorization accurate; scheduler mutation requires confirmed isolated local endpoint first. | U/R/S/C; DATA | not_run |
| PGQA-P09 | Readiness/health and environmental tile failures encountered by UI show bounded recovery, no secret diagnostics or misleading available layer; service ingestion routes are outside browser acceptance. | R/S/C/V; QA/DATA | not_run |

## Incoming and planned experiences: not integrated into this baseline

All rows are **blocked** pending their named dependency, an immutable integrated candidate and suitable admitted/synthetic fixtures. Inspecting a worktree, planning document or author receipt does not clear the block. Keep existing main weather, community and agent cases active in parallel. Forecast/botanical/PNW rows do not add to the current 27-toggle/24-agent-surface census.

| ID | Requirement / journey and expected result after integration | Dimensions / owner / blocking dependency | Status |
| --- | --- | --- | --- |
| PGQA-B01 | [Profile identity/API][bot-spec]: select canonical authority/version/concept plus pinned release; accepted/source names and ambiguity/unmatched taxon explicit, no name-only join. | U/R/S/C/ref/A; BOT; integrate lookup and admitted profile release | blocked |
| PGQA-B02 | Profile growth/establishment traits show approved values, original/normalized units, evidence class and per-value source/licence/assertion/decision links. | U/R/S/C/ref/A; BOT; publication/reconciliation evidence | blocked |
| PGQA-B03 | Missing/conflicting/withdrawn traits and unavailable release remain explicit; no relational-authoring fallback, guessed default or silent new-release overwrite. | U/R/S/C/ref/A; BOT; release fixtures | blocked |
| PGQA-B04 | Species-information tool separates growth, fuel/tissue, agricultural roles and companion evidence; fuel method/component/live-dead/basis/season context retained. | R/S/C/ref/A; BOT; integrated tool schema | blocked |
| PGQA-B05 | Unsupported fire/planting/agronomic outcome claim refused; occurrence, establishment compatibility and reviewed objective effect stay separate, no species ranking implied. | U/R/S/C/ref/A; BOT; downstream recommendation handoff | blocked |
| PGQA-B06 | Bounded profile pagination/continuation, source-version change, conditional publication/rollback and cache identity preserve pinned result and exact per-value provenance. | R/S/C/ref/A; BOT; immutable intake/release fixtures | blocked |
| PGQA-B07 | [Occurrence detail layer][occ-spec]: inspect admitted specimen support/taxon/event interval/collection/catalog/uncertainty/rights; no centroid placement or withheld-locality inference. | U/R/S/C/T/V/A; OCC; admission + occurrence release + renderer | blocked |
| PGQA-B08 | Documented-taxon richness at regional/middle zoom counts exact concepts, collection/QC exclusions and release; no summed child richness or inferred botanical absence. | U/R/S/C/T/V/A; OCC; support-evaluation artifact + aggregate renderer | blocked |
| PGQA-B09 | Collection-effort layer shows record/event-estimate/collection/date/uncertainty measures with declared aggregation, never abundance or vegetation density. | U/R/S/C/T/V/A; OCC; admitted effort contract | blocked |
| PGQA-B10 | Taxon/collection/quality/event filters retain snapshot/QC identity and interval dates; aggregate/detail rungs exclusive, caps/completeness explicit. | U/R/S/C/T/V/A; OCC; shared filter/zoom contract | blocked |
| PGQA-B11 | Occurrence agent returns documented evidence and uncertainty-aware spatial/event-time neighbours with offsets; refuses occupancy, suitability, surveyed absence and abundance. | R/S/C/T/A; OCC; integrated reader/tools | blocked |
| PGQA-W01 | [Weather mode separation][wx-spec]: existing toggle is labelled sampled/model estimate; distinct Now/History/Forecast products and controls never recast polls as forecasts. | U/R/S/C/T/V/A; WX; admitted product + UI integration | blocked |
| PGQA-W02 | Scalar field selects one variable; temperature/apparent temperature/precipitation/cloud/humidity use supported field or disclosed interpolation, domain masks and no cracks/rung overlap. | U/R/S/C/T/V/A; WX; scalar product + renderer | blocked |
| PGQA-W03 | Wind u/v arrows/barbs/optional particles match speed/direction convention and cancellation; stop at no-data edge, pause/reduced-motion static equivalent. | U/R/S/C/T/V/A; WX; admitted vectors + renderer | blocked |
| PGQA-W04 | Pick/search location opens first-valid/current, hourly 24–48h and daily 7–10d only as supported; high/low, amount/probability, gust/direction/humidity/timezone/update age agree. | U/R/S/C/T/V/A; WX; location card + source horizons | blocked |
| PGQA-W05 | Hourly/daily stepping keeps run, issue, valid time and accumulation interval distinct; newer run cannot partially replace pinned series. | U/R/S/C/T/V/A; WX; run/valid-time controls | blocked |
| PGQA-W06 | April 28, 2025 unavailable-day regression: reconcile catalogue/exact reader, clear old sampled frame and prevent delayed repaint; alternate product only under its own mode. | U/R/S/C/T/V/A; WX/DATA; exact unavailable fixture + integration | blocked |
| PGQA-W07 | Unit changes and zero/missing/outside/stale/not-generated/provider-unavailable states agree across tooltip/legend/card/agent; no unsupported probabilistic certainty. | U/R/S/C/T/V/A; WX; unit + availability contract | blocked |
| PGQA-W08 | Text forecast table, keyboard time/place, mobile sheet, motion pause and measured byte/texture/particle/frame budgets; style/unmount releases GPU resources. | U/R/S/C/T/V/A; WX; frozen performance budgets + candidate | blocked |
| PGQA-I01 | [Boundary Polygon/rectangle/explicit Point][boundary-spec]: draw visible vertices/first point/preview/fill and finish into consent form; never silently replace polygon with point. | U/R/S/C/V; BOUND; geometry editor + shared integration | blocked |
| PGQA-I02 | Undo, clear/restart, edit/cancel preserve saved geometry/form; existing accepted MultiPolygon stays intact unless explicitly replaced. | U/R/S/C/V; BOUND; incoming editor | blocked |
| PGQA-I03 | Invalid repeated/self-intersecting/degenerate/pole/antimeridian/excess vertex/byte geometry rejected consistently with server; spherical area and precision honest. | U/R/S/C/V; BOUND; shared validation and fixtures | blocked |
| PGQA-I04 | Keyboard/touch drawing suppresses unrelated gestures/analysis; cancel/finish/unmount restores prior handlers, style swap restores draft then cleans temporary paint/listeners. | U/R/S/C/V; BOUND; integrated MapView/editor | blocked |
| PGQA-I05 | Local drawn-boundary submit → consent → pending → expert publish/reject → contributor outcome/map invalidation round trip; geometry preserved and only publication disclosed. | U/R/S/C/V/A; BOUND/COMM; canonical publication-sync packet | blocked |
| PGQA-N01 | [PNW Parcels and land use][pnw-spec]: select namespaced parcel, county/location/acreage/method/nonpersonal category/record link and optional use facets; no title or zoning-right inference. | U/R/S/C/ref/V/A; PNW; admitted parcel/reference layer | blocked |
| PGQA-N02 | Electric utility territories: provider/type/source/overlaps plus documented service/interconnection/engineering routes; no address-service/capacity guarantee or gas/water/transmission expansion. | U/R/S/C/ref/V/A; PNW; WA/OR/ID source reconciliation | blocked |
| PGQA-N03 | BLM surface-manager tract plus separately evidenced field-office/program relationship; distinguish surface responsibility from office boundary and other federal land. | U/R/S/C/ref/V/A; PNW; admitted BLM relationships | blocked |
| PGQA-N04 | State-managed lands: actual owner/manager/steward/interests and regional/program route; no automatic assignment of every tract to a single agency. | U/R/S/C/ref/V/A; PNW; admitted agency/source keys | blocked |
| PGQA-N05 | Point/on-boundary/multi-feature bounded-area selection pins persistent details and browsable contact list; preserve project area, multiple candidates, pagination and coverage, not centroid-only answer. | U/R/S/C/ref/V/A; PNW; selection/results contract | blocked |
| PGQA-N06 | Public route cards show organization/office/role, documented service, relationship evidence/match method and verification; nearby office/page reachability alone cannot establish fit. | U/R/S/C/ref/A; PNW; reviewed contact/relationship evidence | blocked |
| PGQA-N07 | Editable Draft inquiry includes permitted selected references, user's idea, public recipient/role, question and sources; copy sends nothing, no invented facts/private owners or implied forwarding/permission. | U/R/S/C/ref/A; PNW; draft capability + permitted-field contract | blocked |
| PGQA-N08 | Independent geometry/directory/use versions and unsupported historical day; labelled current reference/current contacts never impersonate historical conditions or replace selected boundary version. | U/R/S/C/ref/V/A; PNW; release/availability policy | blocked |
| PGQA-N09 | Outside pilot/missing jurisdiction/admitted-empty/partial/stale/unverified/optional-missing states remain distinct; excluded private fields do not count as defects or repair tasks. | U/R/S/C/ref/V/A; PNW; coverage + privacy contract | blocked |
| PGQA-N10 | Keyboard/touch accessible selection, persistent mobile sheet, source-link return, bounded geometry/contact bytes and stale-response cancellation; measure frozen budgets and explain capped incompleteness. | U/R/S/C/ref/V/A; PNW; candidate + performance budgets | blocked |
| PGQA-N11 | Agent answers intersections, responsible offices/advisers and inquiry drafts using same point/area/topic/permitted fields/releases; routes and responsibilities evidenced, no silent nearest substitute. | R/S/C/ref/A; PNW; tools + admitted relationship plane | blocked |

## Coverage accounting and execution gates

The row counts below are inventory counts, never successful-test counts. Variant records will increase execution count without changing stable requirement IDs. Coverage reduction or a `not_applicable` verdict requires independent review.

| Inventory family | Case range | Requirement accounting |
| --- | --- | --- |
| Routes | R01–R23 | 23 current `page.tsx` files, including all auth and dynamic invite/join/conversation routes. |
| Shell | G01–G10 | Navigation, auth transitions, role/cache boundaries, responsive/accessibility and recovery. |
| Map | M01–M13 | Manager, groups, rows, view controls, shortcuts, canvas, camera and feature interaction. |
| Layers | L01–L27 | 18 explicit registry entries plus nine generated climate entries = 27; soil is a visible withholding case, not omitted. |
| Time | T01–T12 | Per-layer selected-time and catalogue/reader/paint/detail/answer consistency, saved-day track and pin/step controls. |
| Details | D01–D13 | Six category reports plus Team; Offline covered by X01–X04; eight total detail bodies. |
| Search/routing | S01–S11 | Six search/location journeys, visible directions trigger, source-only routing mount and three routing API capabilities. |
| Community | C01–C16 | Submission, consent, role/ownership, review, outcome, feed, aggregate privacy; synthetic and human gates separate. |
| Organization | O01–O10 | Onboarding, identity/team scope, settings, boundaries, members and local invitations. |
| Offline/sharing/realtime | X01–X10 | Offline/storage/conflicts, SSE/WebSocket, upload/layer operations, embed and data sharing. |
| Agent/MCP | A01–A17 | Ten named current tools, 24 named observation surfaces, regional UI, conversation/feedback and protocol/refusal semantics. |
| API/operations | P01–P09 | Developer-facing API/tester, imagery and admin jobs; routing and tools counted in their own families. |
| Incoming/planned | B01–B11, W01–W08, I01–I05, N01–N11 | Profile six; occurrence five; forecast eight; boundary five; PNW eleven. Not added to current-main surface census. |

**Authored total: 206 stable cases — 165 `not_run`, 41 `blocked`, 0 `pass`, 0 `fail`, 0 `not_applicable`.** The 41 blocked rows consist of 35 incoming/planned journeys, five source-only UI reachability gates and one real-human gate. All 206 IDs are unique. Reference targets were checked for local existence as an authoring check; behavior and coverage still require the independent verifier. No runtime browser, API, database or service checks were performed to create this inventory. No claimed full test suite, scientific/data acceptance, production readiness or GREEN follows from it.

## Unresolved inventory questions

1. Bind the integration and feature owners' actual task IDs, titles, full review base/candidate/tree/diff and service/release receipts. Worktree-list observations are insufficient; mutable main may have moved since this census.
2. Resolve current reachability of routing panel/renderer, `LayerUpload`, `UserPanel`, `EmbedCodeGenerator` and legacy `ModerationPanel`. Source-only definitions are blocked coverage, not proof of removal or accepted scope exclusion; the visible directions action still requires runtime testing.
3. Reconcile soil-survey's explicitly declared PostgreSQL reader/capability withholding with the current governed-plane contract and incoming reader work. Never bypass the gate or warm a production reader to populate QA.
4. Freeze permitted role/organization fixtures, disposable database identity, local endpoints and disabled external transports before any write journey. The real-human submission gate remains separately owned and cannot be waived by synthetic outcomes.
5. Freeze admitted populated/governed-empty/missing/outside-domain fixtures for each of 24 agent surfaces and each UI product, plus source ceilings, reference releases and per-rung support. Confirm local service provisioning before promising runtime data coverage.
6. Confirm which APIs and docs advertise export, routing, tracking and imagery without mounted UI. Retired analytics, alerts, drag reorder, blend/lock and building-footprint controls are not reintroduced by this inventory; any surfaced promise still needs a case or accepted exclusion.
7. Freeze interaction/request-to-paint/frame/byte budgets and screen-reader/physical-device scope; emulated mobile must not be called physical-device coverage. Assign performance and scientific reviewers independently.
8. Incoming botanical/forecast/boundary branches need full candidate/diff/receipt intake; profile is a nonspatial reference product, occurrence is separate, and existing forecast-summary tool is not evidence of traditional weather UI.
9. PNW planned source/rights/office relationships, county/state coverage, geometry-directory version compatibility, permitted field set and contact freshness remain prerequisites. Research establishes no outreach authorization or ready layer.
10. Reconcile complete API/MCP `tools/list` and route manifests on the frozen candidate, including incoming tools; this inventory explicitly lists current ten tools and 23 pages but does not assume future additions retain those totals.

[qa]: ../spec.md
[matrix]: ../matrix.md
[lane]: ../../../../docs/layer-lane-standard.md
[production]: ../../parquet_production_acceptance_20260901/plan.md
[community-spec]: ../../community_engagement_completion_20260805/spec.md
[bot-spec]: ../../botanical_species_profile_lookup_20260911/spec.md
[occ-spec]: ../../botanical_occurrence_experience_20260911/spec.md
[occ-plane]: ../../botanical_occurrence_parquet_lane_20260911/spec.md
[herbaria]: ../../pnw_herbaria_source_admission_20260911/spec.md
[wx-spec]: ../../weather_forecast_experience_20260911/spec.md
[wx-plane]: ../../weather_forecast_parquet_lane_20260911/spec.md
[pnw-spec]: ../../pnw_land_contact_experience_20260911/spec.md
[pnw-plane]: ../../pnw_land_context_reference_plane_20260911/spec.md
[boundary-spec]: ./intervention-boundary-spec.md
[app]: ../../../../src/app/
[api]: ../../../../src/app/api/
[map-page]: ../../../../src/app/page.tsx
[feed]: ../../../../src/app/feed/InterventionFeed.tsx
[ledger]: ../../../../src/app/community/CommunityLedger.tsx
[api-tester]: ../../../../src/app/docs/ApiTester.tsx
[embed]: ../../../../src/components/embed/EmbedMap.tsx
[embed-dir]: ../../../../src/components/embed/
[auth]: ../../../../src/components/auth/
[onboarding]: ../../../../src/components/onboarding/
[dashboard]: ../../../../src/app/dashboard/page.tsx
[org]: ../../../../src/app/dashboard/org/
[conversations]: ../../../../src/app/dashboard/conversations/
[moderation]: ../../../../src/app/moderation/page.tsx
[jobs]: ../../../../src/components/panels/JobRunnerDashboard.tsx
[shell]: ../../../../src/components/layout/ApplicationShell.tsx
[topbar]: ../../../../src/components/layout/TopBar.tsx
[map-view]: ../../../../src/components/map/MapView.tsx
[manager]: ../../../../src/components/map/layer-panel/
[view]: ../../../../src/components/map/layer-panel/ViewDockSection.tsx
[shortcuts]: ../../../../src/components/map/MapKeyboardShortcuts.tsx
[registry]: ../../../../src/lib/map/layer-registry.ts
[capabilities]: ../../../../src/lib/server/services/parquet-slider-capabilities.ts
[time-types]: ../../../../src/types/time-slider.ts
[time-store]: ../../../../src/stores/time-slider-store.ts
[metrics]: ../../../../src/stores/useMetricAtDate.ts
[climate]: ../../../../src/lib/environmental/climate-field.ts
[panels]: ../../../../src/components/panels/
[panel-doc]: ../../../../src/components/panels/AGENTS.md
[search]: ../../../../src/components/map/layer-panel/SearchDockSection.tsx
[reverse]: ../../../../src/components/search/ReverseGeocode.tsx
[routing-ui]: ../../../../src/components/routing/
[routing-api]: ../../../../src/lib/server/trpc/routers/routing.ts
[intervention-api]: ../../../../src/lib/server/trpc/routers/interventions.ts
[contribution-api]: ../../../../src/lib/server/trpc/routers/contributions.ts
[teams-api]: ../../../../src/lib/server/trpc/routers/teams.ts
[draw-service]: ../../../../src/components/tools/ServiceAreaDrawTool.tsx
[layers-api]: ../../../../src/lib/server/trpc/routers/layers.ts
[sync]: ../../../../src/components/ui/SyncIndicator.tsx
[stream]: ../../../../src/app/api/stream/[layerId]/route.ts
[ws]: ../../../../src/app/api/ws/route.ts
[agent-interaction]: ../../../../src/components/map/AgentInteraction.tsx
[agent-tools]: ../../../../services/agri-data-service/src/agri_data_service/agent/tools.py
[agent-surfaces]: ../../../../services/agri-data-service/src/agri_data_service/agent/surfaces.py
[mcp]: ../../../../services/agri-data-service/src/agri_data_service/agent/mcp_server.py
[routers]: ../../../../src/lib/server/trpc/routers/
