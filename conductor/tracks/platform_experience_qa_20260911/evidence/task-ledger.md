---
type: evidence
recorded_on: 2026-09-12
observed_at: 2026-09-12T07:20:03Z
---

# Task and candidate ledger

This ledger binds the current QA and authoring tasks before their handoffs are
consumed. `Active working tree` is not an immutable candidate. A task can move
to `integrated` only after its committed tree is reconciled into the canonical
candidate; it can close only after the required independent verdict.

## Bound tasks

| Role | Codex task | Checkout and branch | Bound source or candidate | Current disposition |
| --- | --- | --- | --- | --- |
| QA and orchestration | `01a0904b-756b-7961-b991-cab666123be2` — PlantGeo QA and track orchestration | `C:/Users/atooz/Programming/plantgeo`, `main` | QA planning commit `820753d73543514f534c9c1386e1f84012152775`, tree `b3205373e373641059c3edf10ce01c7f5f61d994` | Active. Owns this ledger, requirement matrix, defect routing, shared Conductor registry/runbook and proof-gated task archival. Runtime integration is complete; populated-data, mobile, agent-parity and broader track verdicts remain open. |
| Botanical profile source-admission continuation | `01a09445-e497-7e30-9cc8-23982fbc5ee4` — Resume botanical profile source admission (`client-new-thread:2c8eff2e-ec3b-4ed3-a841-7e87747e9e1c`) | `C:/Users/atooz/.codex/worktrees/7f2f/plantgeo`, detached clean checkout | Independently reviewed commit `2777778`, tree `91723969b291df1ed17c88709550b7c81dbd9cd0`; integrated into root `4072fb0` | Archived after independent review and local integration. The contract audit keeps P0/P1 source-admission and schema gates open; no database, pgt, Railway, production, object-store, writer, scheduler or deployment access occurred. |
| Parquet reader and gapless acceptance continuation | `01a09446-41c3-7683-b7cb-a78f9c370916` — Resume Parquet reader and gapless acceptance (`client-new-thread:4e903414-5148-4d7a-8cea-62e8eb724933`) | `C:/Users/atooz/.codex/worktrees/828b/plantgeo`, detached clean checkout | Independently reviewed commit `de64862`, tree `e7dca1d02e3e18578731ce986d84bed9a6eede5e`; integrated into root `0669adb` | Archived after independent review and local integration. The receipt preserves requested/served-day, coverage ceiling/caption, cold/warm trace and gapless recovery blockers; no runtime data load or writer/scheduler/pointer mutation occurred. |
| Weather and visual acceptance continuation | `01a09446-5355-7251-86b1-48a0ea891eed` — Resume weather and visual acceptance (`client-new-thread:c67bcc49-a6c6-4ba5-8748-22437a984b9e`) | `C:/Users/atooz/.codex/worktrees/1b03/plantgeo`, detached clean checkout | Independently reviewed commit `994760e`, tree `36feec73eeddf6af086845d8fe57092f50851d89`; integrated into root `37eb50a` | Archived after independent review and local integration. The fixture regression proves the ready selected-day report removes readings and the map-marking action on a typed `upstream_unavailable` response; populated-data, mobile/touch, hover and forecast gates remain open. |
| Repository conformity evidence continuation | `01a09446-6856-7780-8a33-132eb0ee2e3e` — Resume repository conformity evidence (`client-new-thread:02e707ef-d506-4897-8ff8-e327708a463d`) | `C:/Users/atooz/.codex/worktrees/c54e/plantgeo`, detached clean checkout | Reviewed replacement commit `5ac5642`, tree `f1d49bc4496afbef11327d96c78874bf4f6a2f1f`; integrated as root commit `176ddca` because its superseded parent was absent from main | Archived after replacement independent review and local integration. The candidate inventory remains read-only and proof-gated; no deletion/refactor, runtime or data-plane change occurred. |
| Environmental retirement and offline-export continuation | `01a09446-85d7-7943-a4aa-cc4f12beb760` — Resume environmental retirement and offline export (`client-new-thread:aaa02c39-564d-4867-acd0-790e4fab2605`) | `C:/Users/atooz/.codex/worktrees/ab41/plantgeo`, detached clean checkout | Independently reviewed commit `bcdcd6c`, tree `8a342a93fcfda025d0ef05441ec7db8ac554628e`; integrated into root `1952bb7` | Archived after independent review and local integration. Environmental retirement, offline export, cutoff and source-identity gates remain active; no Railway, production object-store, PostgreSQL, pgt, writer, scheduler, pointer or deployment action occurred. |
| Herbaria admission gate | `client-new-thread:5798d8ec-db17-4f5e-a5ad-5f4b402042c6` — PlantGeo Herbaria admission gate | `C:/Users/atooz/.codex/worktrees/7387/plantgeo`, branch `codex/pnw-herbaria-governance-audit-20260912` | Independently reviewed commit `80d0632`, tree `5eb0393b69a1a959ee0ced80b8e3c33d50d90a43`; integrated into root `1637f97` | Archived after independent review and local integration. WTU/UBC rights, coordinate-withholding, release identity and quarantine remain blocked; no archive/specimen/media acquisition or data-plane access. |
| Botanical profile source contract | `client-new-thread:f7fc17f4-d86a-4ca5-b2f0-294f5052e400` — PlantGeo botanical profile source contract | `C:/Users/atooz/.codex/worktrees/2205/plantgeo`, detached clean checkout | Independently reviewed commit `031dd2a`, tree `326f177016ed3434046b147d9d5267fba9692ce5`; integrated into root `747f094` | Archived after independent review and local integration. The nine source, identity, schema, publication and consumer blockers remain release-blocking; no DB, pgt, Railway, ingestion or runtime change. |
| Forecast contract freeze | `client-new-thread:6f70e73b-4eae-44e1-8b52-ef99fbcb5c2f` — PlantGeo forecast contract freeze | `C:/Users/atooz/.codex/worktrees/30ce/plantgeo`, branch `codex/weather-forecast-planning-20260912` | Independently reviewed commit `518597f`, tree `14ed3b1a80fe2233447f20a1b83d5eacf3e31935`; integrated into root `23409e2` | Archived after independent planning review and local integration. Forecast tracks remain planned; provider admission, F0/F1/F3/F4, implementation, populated-data and mobile gates remain open. Documentation-only; no data operation. |
| PostgreSQL shrink proof audit | `client-new-thread:38b706ea-0752-41a7-810c-758aeb812265` — PlantGeo PostgreSQL shrink proof audit | `C:/Users/atooz/.codex/worktrees/7837/plantgeo`, detached clean checkout | Independently reviewed commit `7967e6c`, tree `9df48945ca2276bfcf38b5fa089eb09356ff7f12`; integrated into root `f0b6b79` | Archived after independent review and local integration. Ownership/removal proof remains open under successor tracks; historical bridge/drain work is explicitly not restarted and no database or production action occurred. |
| Service-backed QA gate matrix | `01a0945e-953b-7961-8d64-4d8443eb1f0f` — Platform experience QA acceptance audit (`client-new-thread:dd4c97cc-6217-4f47-a687-da30ace6b8e0`) | `C:/Users/atooz/.codex/worktrees/ff33/plantgeo`, detached clean checkout | Independently reviewed commit `ad7ae72`, tree `b3973d15965061496b6a6fac0fe297ba0ab80bd6`; integrated into root `15347ea` | Sidebar task archived after independent review and local integration of the audit-only matrix. It records 34 groups (30 blocked, 4 not-run); platform QA remains active and no runtime/data/production action occurred. |
| Parquet reader and gapless acceptance continuation | `01a0941d-41b7-7483-b87b-61c94fab4120` — Resume Parquet reader and gapless acceptance | `C:/Users/atooz/.codex/worktrees/b893/plantgeo`, detached clean checkout | Reviewed commit `6213b303da54e91204c3da1ff4f9a7def896dbdf`, tree `11be3de73b218a008233f1053598e5248f7f37f8`; integrated into root `267e197` | Archived after independent review and local integration of the reader R0 availability-contract definition and gapless ownership reconciliation. The receipt records wire v3 coverage, recorded versus carried day ceilings, checksum/unconditional GET behavior, rollups, cache lifetimes, derived-empty support and the distinction between recovery code and observed recovery. Both parent tracks remain active for production traces, complete history, ownership/cutoff/lease evidence and scheduled advances. |
| Environmental retirement and export continuation | `01a0941d-8488-7b00-845c-1ad3caeb0b5e` — Resume environmental retirement and export acceptance | `C:/Users/atooz/.codex/worktrees/25f9/plantgeo`, detached clean checkout | Reviewed commit `575c8902c796a51a6e465121791d3defff1964b9`, tree `757aca71689d72d384957144963dc09eeb13b26a`; integrated into root `eee2b0f` | Archived after independent review and local integration of the NASA POWER prepared-versus-published reconciliation. The receipt binds the three published temperature generations and historical window, scopes the stale dead-letter, removes an unsupported VPD count, and preserves missing artifacts and forward-health uncertainty. The environmental-retirement and offline-export parent tracks remain active; no production, PostgreSQL, scheduler, deployment or object-store mutation occurred. |
| Repository conformity continuation | `01a0941d-ac50-70e0-8314-44e098436d20` — Resume repository conformity acceptance | `C:/Users/atooz/.codex/worktrees/a1e1/plantgeo`, detached clean checkout | Reviewed commit `a3ceca8644c5b9d09388238e1702060ee8e7daef`, tree `5dabbe83bfda5ca40f73048d3f2c4df8c42fdd96`; integrated into root `c894436` | Archived after independent review and local integration of one bounded conformity slice. The verified Zustand-only state model now replaces stale Jotai/atomWithQuery guidance in architecture, style guidance and the MapView comment; the broader canonical-core, dead-code and dependency proof gates remain active. No deletion or cross-track cleanup was authorized. |
| Canonical integration | `01a093c6-69e0-79d2-b1c7-bc5e907dfd1b` — Integrate weather and botanical candidates | `C:/Users/atooz/.codex/worktrees/40ae/plantgeo`, `codex/integrate-botanical-weather-evidence` | Final candidate `9284d52738dbb323bbcd1dfe8c22ab6290f2a4b1`, tree `a0797ce0b4a91b2ac051dfa423382fbf3db60104`; root `main` contains the identical candidate tree at `6d2ac6c` before the follow-up custody-ledger commit | Archived after serialized reconciliation, independent verification and root browser evidence intake. Candidate remains partial: populated data and narrow-mobile acceptance are open. No remote/data-writer action. |
| Botanical species-profile author | `01a092bd-c71d-7bb3-bc46-e0dac684f751` — Build botanical species profiles | Preserved predecessor worktree; Git metadata disappeared | Implementation commit `edc6afdeb23f339b40049ec0828551c2fe1a4d45`, tree `01ad55b6220a13604e8fbf8a4ceda773e35d5658`; separate census receipt commit `557c4c0` | Local implementation complete and independently reviewed; task archived after custody was retained. The separate census owner `01a093be-a6b1-7670-bf41-5e999cb0a2c9` is archived; Railway/production census is blocked before DB access and remains open. |
| Botanical profile and agent-wiring continuation | `01a093f2-7452-75d3-9fba-343d14758f71` — Resume botanical profile and agent wiring | `C:/Users/atooz/.codex/worktrees/a93f/plantgeo`, detached clean checkout | Reviewed commit `f122069fd3f635d2f39f55970a3e9b57f925a167`, tree `61d88bd500579d108e5a004cb5dbfb48fc632e5a`; integrated into root `3e35971` | Archived after independent review and focused verification. The exact-UUID, read-only authoring lookup is wired to the HTTP API, agent graph and MCP tool with explicit provenance, missingness, approved companion evidence and refusal of ranking/planting/fuel claims. The Railway/production census remains blocked with no authorized DSN; `pgt`, local databases, WCVP and unpublished fixture releases remain out of scope. |
| Historical weather visual author | `01a093b8-e328-7b52-ae2a-a06257d6aed5` — Implement historical weather visual… | `C:/Users/atooz/.codex/worktrees/15a4/plantgeo`, isolated author checkout | Immutable owner commit `8e53b416ce5dc5287295a707dae2f9c121e1e993`, tree `23ba93ff46458c5d8bd399072ce667d152d11235`; integrated into candidate `9284d527` | Archived after independent review, focused checks, integration and root desktop unavailable-state evidence. Live labels/hover/day transitions remain unexercised because the governed data service is absent. |
| Superseded weather forecast author | `01a09323-6f59-7822-9e6f-146892776749` — Build PlantGeo weather forecast | Archived `C:/Users/atooz/.codex/worktrees/32be/plantgeo` after clean reversion chain | Reverted forecast commits retained in history and superseded shared diff retained in named stash | Archived as superseded custody. No stash was applied or deleted; the historical visual task owns the current repair. |
| Intervention boundary and publication author | `01a0932a-a08f-7712-b643-bfc67bc70d01` — Fix intervention boundaries and publishing | Archived after evidence-only reconciliation | Accepted runtime source `2fc6b30ac1b024e1c955dbf95552495608608a96`; evidence-only receipt `d9e4bd21f46d5d5caf8a0a90a4f39f4f222f1241` | Archived implementation task. Broader intervention boundary, production publication and human-contributor gates remain open in Conductor. |
| PNW land/contact planning author | `01a09326-7319-7930-bee3-ac91e6085776` — Plan PNW land contact layers | Shared main checkout; no implementation branch | Nine planning-only files under `pnw_land_context_reference_plane_20260911` and `pnw_land_contact_experience_20260911`; independent planning review and documentation sweep passed. | Archived after registration and evidence were reconciled. Parent tracks remain `planned`; no ingestion, outreach or implementation is authorized. Registration observed at `2026-09-12T01:48:27Z`. |
| PNW Herbaria source-admission continuation | `01a093f3-0097-7971-b2fb-179de546d643` — Resume PNW Herbaria source admission | `C:/Users/atooz/.codex/worktrees/bfe6/plantgeo`, detached evidence checkout | Metadata refresh commit `47715dd984a4c15afe174bc6e6d9bef8ce1ed704`, tree `888d404c1b404f6af641ab469810f69cf068bb9b`; integrated into root `57ef4fc` | Archived after metadata-only evidence was independently verified and integrated. Seven bounded metadata GETs are retained with URL, timestamp, headers, byte count and SHA-256 receipts; UBC v16.43 EML is byte-identical to the prior capture. WTU still lacks a standalone release-bound EML/field map and UBC lacks an applicable coordinate-withholding statement, so archive acquisition and admission remain blocked. No archive, image, specimen, RTF, script, iframe or media resource was acquired. |
| Multiscale visual-layer continuation | `01a093f3-575a-7e02-bc0d-bc2c7ce7d562` — Finish multiscale visual layer updates | `C:/Users/atooz/.codex/worktrees/4a6e/plantgeo`, detached clean checkout | Reviewed commit `2b29d9f1ad475358e96fc6c0e48aabd9a3e7d29b`, tree `878a071801f472a9cbcd3cb02eb06c2845d5d3c4`; integrated into root `ac4ce70` | Archived after independent review and local integration. Climate and soil scalar surfaces now show unit-bearing numeric labels with `avg` qualifiers for aggregated features, preserve missingness/legends/opacity/style reloads, and leave climate isobands unlabeled. Twenty-six synthetic desktop/mobile captures support the mechanics; live selected-day, dense-basemap, hover, production-performance and full mobile gates remain open. |
| Multiscale visual acceptance continuation | `01a0942e-dd5e-70f0-a1f6-7119f4e4b8af` — Audit integrated scalar-label and weather visual contracts | `C:/Users/atooz/.codex/worktrees/c568/plantgeo`, detached clean checkout | Independently reviewed commit `5cf7f59b23d61d8291c05ff9915e523710606ca1`, tree `f5eed7708673c4ccd11af6d3ac385071a8f8e5b5`; integrated into root `5bbe3dc` | Archived after independent review and local integration. WeatherLayer now rechecks live style readiness after a missed `style.load` and idempotently restores its source plus temperature, label and wind layers. The focused test is recorded; populated data, mobile/touch and live basemap acceptance remain open in the parent QA track. |
| Botanical outcome-label audit continuation | `01a0942e-4f5c-75a0-b46b-795bb3b7dec7` — Reconcile retained strategy outcome-label evidence | `C:/Users/atooz/.codex/worktrees/d018/plantgeo`, detached clean checkout | Independently reviewed commit `88fdd8af12679967c539ef2018144d17debca721`, tree `d59446f8bf81faf7a29ff6d9efccbd3618a3b5b2`; integrated into root `af66dd4` | Archived after independent PASS and local integration. The dated audit records that no intervention/control outcome-label source is admissible, preserves the exact zero-count relations, and keeps `effect_candidate` disabled. No database, Railway, writer, scheduler, publication or deployment access occurred; the strategy lane remains blocked on a real immutable intervention-outcome release. |
| Historical PlantGeo QA candidate | `01a08af2-569e-7532-a16c-79823255e487` — PlantGeo QA and orchestration | `C:/Users/atooz/Programming/plantgeo`, historical notLoaded session | Handoff references candidate `3a5f3902e6f56b0878eab1d72b34789f6653058f`; not integrated into current root | Archived as superseded historical custody. Its later handoff reports six app-test failures and a Python export-order lint issue, so it is not a release or approval candidate for this tree. |
| Historical ingestion-throttle repair | `01a08b00-2a50-73c2-b39b-39523c74ceb2` — Repair PlantGeo Parquet ingestion and… | `C:/Users/atooz/.codex/worktrees/1d40/plantgeo`, historical clean checkout | Commit `3a5f3902e6f56b0878eab1d72b34789f6653058f`, tree `48377a8ee5f7e546c36d7dc484e222e64203950b`; preserved service archive and receipt | Archived as historical custody. The candidate addressed provider cooldown/throttle handling and climate coverage, but was not cherry-picked, pushed, deployed or published; current ingestion and production tracks retain their own gates. |

## Authority and ownership constraints

- Railway, production databases, production object storage, deployments,
  external messages and user contact remain outside these tasks.
- The historical-weather visual author must supply an exact committed head,
  tree, changed paths, base blob identities, focused receipts and independent
  verdict before integration. Working-tree state is never a handoff. The
  superseded forecast task is retained only as archived custody evidence.
- The integration task serializes shared map, reader, capability, HTTP, agent,
  MCP and Conductor changes. Documentation-only commit `820753d` must be
  reconciled into the final candidate, with runtime receipt reuse justified by
  an exact unchanged-runtime diff when appropriate.
- Local agent-created intervention submissions prove synthetic mechanics only.
  They cannot close the community track's human-contributor requirement.
- The completed botanical implementation and census tasks are archived after
  their commits and receipts were retained; blocked production/source gates keep
  their owning Conductor tracks open. The weather visual, integration, PNW
  planning and intervention author tasks are likewise archived with evidence
  retained.

## Next ledger update

### 2026-09-12 live restart addendum

The [live restart receipt](session-restart-20260912-live.md) supersedes the
stale lifecycle labels for the two unresolved owners without rewriting their
historical custody rows. Forecast task `01a09323-6f59-7822-9e6f-146892776749`
was restarted with a read-only superseded-branch audit, remained active through
the bounded wait, and its restart turn was then interrupted/stopped; it returned
to idle and was archived from the sidebar as superseded. No new completed
restart receipt was produced; earlier completed audit evidence remains retained
in its historical custody record. Ingestion task
`01a08b00-2a50-73c2-b39b-39523c74ceb2` was restarted, made no progress for the
bounded wait, was stopped cleanly and is idle; its candidate remains
unintegrated and unresolved. The fresh queue aliases
`client-new-thread:bfb15326-7832-47bc-a0cf-2415b0127c45` and
`client-new-thread:5a83705b-98c6-4717-b9bd-519af214817d` were requested from
current `main` but had not materialized in `list_threads` at observation time.
They are not task IDs and are not completion evidence. The botanical candidate
audit independently returned HOLD for `edc6afd` and `3135d6b`; retain both as
unaccepted custody and keep the current census/transitional lookup state.

The weather owner, botanical census, botanical profile/agent wiring,
integration, PNW planning, PNW Herbaria refresh, multiscale visual continuation
and acceptance continuation, historical QA candidate and historical
ingestion-throttle repair entries are
bound to immutable evidence and archived custody. The root browser receipt is
recorded at `e5eaab6`, weather and botanical runtime integration at
`9284d527`/`3e35971`, Herbaria evidence at `57ef4fc`, scalar-label integration at
`ac4ce70`, weather readiness recovery at `5bbe3dc`, conformity slice at
`c894436`, weather approval at `f659d1f` (updated in the current tree), and
the root verification receipt at
`3182159`; keep the platform QA task active for the remaining populated-data,
mobile, agent-parity and broader track gates. The reader / gapless,
environmental-retirement / offline-export, and conformity continuations are
archived with immutable receipts while their parent tracks remain active for
the remaining production and proof packets. The botanical outcome-label
continuation is archived at root `af66dd4` after its independent PASS; its
source-abstention receipt is
`conductor/retros/strategy_selection_governance_20260726/outcome-label-source-audit-2026-09-12.md`.
The multiscale visual acceptance continuation is archived at root `5bbe3dc`
after independent approval. Both parent tracks remain active for their
remaining production, populated-data, mobile and proof packets. Client queue
identifiers are lifecycle aliases, not task or commit identities.
Five newly dispatched continuations were materialized and are now archived after
independent review and exact-tree integration: botanical profile source
admission (`01a09445-e497-7e30-9cc8-23982fbc5ee4`, root `4072fb0`), reader and
gapless acceptance (`01a09446-41c3-7683-b7cb-a78f9c370916`, root `0669adb`),
weather and visual acceptance (`01a09446-5355-7251-86b1-48a0ea891eed`, root
`37eb50a`), repository conformity (`01a09446-6856-7780-8a33-132eb0ee2e3e`,
root `176ddca`) and environmental/offline export
(`01a09446-85d7-7943-a4aa-cc4f12beb760`, root `1952bb7`). Their queue aliases,
worktrees and read-only boundaries remain bound above; parent tracks stay open
for their unresolved production, source, populated-data and proof gates.
Five further lane-aware, read-only sessions were dispatched and are now
archived after independent review and exact-tree integration: Herbaria
admission (root `1637f97`), botanical profile source contract (root `747f094`),
forecast contract freeze (root `23409e2`), PostgreSQL shrink proof (root
`f0b6b79`) and the service-backed QA gate matrix (root `15347ea`). Their queue
aliases, worktrees, reviewed commits/trees and read-only boundaries remain
bound above. The service-backed QA task materialized as
`01a0945e-953b-7961-8d64-4d8443eb1f0f` and was archived in the sidebar; the
other four remain client aliases because no separate sidebar task IDs were
materialized. The receipts preserve unresolved parent-track gates and
authorize no writers, databases, Railway, object storage, schedulers, APIs,
UI runtime changes or deployment.
Superseded identities remain dated evidence and are not relabeled as the final
candidate.

### 2026-09-12 restart continuation

The [continuation receipt](session-restart-20260912-continuation.md) records a
new bounded lifecycle pass. Botanical task
`01a092bd-c71d-7bb3-bc46-e0dac684f751` was reopened, polled twice without an
assistant or tool event, then stopped at its verified handle and returned idle
with a read-only HOLD report. It made no file or ref changes; its preserved
`edc6afd` implementation and current census/source-admission blockers remain
unaccepted. The completed task can be archived again after this record is
retained, while its source, census and production owners remain open.

Ingestion task `01a08b00-2a50-73c2-b39b-39523c74ceb2` was restarted with a
read-only preflight instruction, then completed the bounded wait without any
assistant, tool, command or revision. It is idle and unresolved; retain it
open. The dedicated visual owner was requested as queue alias
`client-new-thread:9f7291c1-48bc-4e45-bfe1-4845d2d80df8` from current `main` in
an isolated worktree with presentation-only scope. It had not materialized in
`list_threads` at observation and is not completion evidence. No Python or
matching data-writer process was observed, and no external/data mutation was
performed.

### 2026-09-12 lane restart pass

The ingestion owner `01a08b00-2a50-73c2-b39b-39523c74ceb2` was restarted with
a read-only preflight and again completed without any assistant, tool,
command or revision evidence. It remains idle and unresolved; retain the
task open and do not archive it. A lightweight botanical reconciliation lane
returned `HOLD` with no file or ref change after rechecking the current
exact-UUID lookup and its source-admission blockers. Its replacement app task
is queued as `client-new-thread:61f18f53-af63-403b-ad09-21be6a0b6cc8` and had
not materialized in `list_threads`.

The presentation lane produced independently reviewed candidate
`a9ff85e0485171cde8030d33cd3fd8da17b48017`, tree
`997728b9ff38dd9c85773ba2fe17e97e40ef77f3`, from current `main`. Its exact
three-file scope prioritizes stronger wind labels under collision avoidance,
documents that behavior in the legend, and adds a focused layout assertion.
It was integrated locally as root `e54d091`. After restoring the lockfile
dependencies locally, the single final verification sweep passed the
data-boundary check, type-check, lint and the full frontend test run (150 files
passed, 2 skipped; 2,232 tests passed, 13 skipped). The replacement app task remains
queue alias `client-new-thread:b4b4d56b-f44d-4d39-9b58-636d218f8bb8` and had
not materialized at observation. No data, database, writer, Railway,
object-store, scheduler, deployment or push action occurred. The bounded
historical weather approval remains in force; forecast and populated-data
gates remain open.

The lightweight forecast reconciliation lane returned `HOLD`: planning
packet `23409e2` is already integrated in current `main`, while F0
provider/product admission and the April 28, 2025 screenshot/catalogue/reader
reconciliation remain open. The superseded forecast implementation remains
archived custody and is not a candidate. No forecast data, external service,
database, writer, scheduler, deployment or push action occurred.

### 2026-09-12 latest restart reconciliation

The [latest restart receipt](session-restart-20260912-latest.md) records a
fresh read-only restart of botanical task
`01a092bd-c71d-7bb3-bc46-e0dac684f751` and ingestion task
`01a08b00-2a50-73c2-b39b-39523c74ceb2`. Botanical completed with no file or ref
change and returned `HOLD`, but identified a concrete P2 agent-parity defect:
the later web graph pass re-exposes `WAREHOUSE_TOOLS` after the
`allowed_species_id` context has exited (`graph.py:311-320`, `423-425`,
`_run_pass` at `253`; `tools.py:310-313`, `538`). The smallest next packet is a
synthetic web-pass regression for a mismatched UUID and an omitted UUID, then
an owner fix that preserves the constraint or excludes the tool from that
pass. The omission design was implemented locally as `9d895dc`; the affected
graph test passed (`28 passed, 1 skipped`), and the focused Ruff and mypy checks
passed. The botanical task is archived after this receipt; source, census,
publication, Herbaria and recommendation gates remain active.

The ingestion restart completed with no assistant, tool, command or revision
evidence. It remains idle and unresolved and is intentionally retained open.
No database, provider, object-store, writer, scheduler, deployment or push
operation occurred in either restart.

The stale completed QA task `01a08af2-569e-7532-a16c-79823255e487` was pruned
from the Codex sidebar after its completed candidate receipt was rechecked. It
had no live turn or active worktree; the current orchestration task and the
unresolved ingestion owner remain retained.

The coordinator then performed one additional bounded restart of ingestion
task `01a08b00-2a50-73c2-b39b-39523c74ceb2`. Turn
`01a09596-3c03-79c0-9099-c24464715adc` ran from
`2026-09-12T12:27:26Z` through `2026-09-12T12:30:38Z`, completed idle, and
emitted no assistant, tool, command, or revision evidence. It remains open and
unresolved; no mutation or push occurred.

The coordinator also dispatched dedicated weather QA queue alias
`client-new-thread:8eb12603-ce98-42b1-ab04-539779e56f9c` from the current
PlantGeo default branch. The lane is read-only and owns service-backed
historical-weather reconciliation only; it does not reopen forecast work or
authorize source, database, writer, deployment, or push operations. It had not
materialized in `list_threads` at observation and therefore has no completion
evidence yet.
