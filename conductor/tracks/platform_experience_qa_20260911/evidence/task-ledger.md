---
type: evidence
recorded_on: 2026-09-12
observed_at: 2026-09-12T06:41:31Z
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
Superseded identities remain dated evidence and are not relabeled as the final
candidate.

## 2026-09-12 acceptance-execution custody

This bounded addition records the acceptance continuations and their local
integration on `codex/reader-ui-contract-20260912` in
`C:/Users/atooz/.codex/worktrees/0831/plantgeo`. Historical rows, observations and
ownership above remain unchanged. The coordinator's shared checkout is task
custody, not a newly measured candidate or an operational authorization.

| Role | Task identity | Checkout custody | Immutable source and bounded outcome |
| --- | --- | --- | --- |
| Acceptance coordinator | `01a09475-01c4-7252-8710-a8a57559c919` | `C:/Users/atooz/Programming/plantgeo` | Coordinates the local evidence integration and supplied the reader review outcome below. No shared-checkout HEAD or production state is inferred. |
| Reader author and local integrator | `01a0947d-457c-7e33-8fce-71dec96e5b34` | `C:/Users/atooz/.codex/worktrees/0831/plantgeo` | Final reader commit `ee81a641d6f1f3fcd38bb96a7e860b0065710419`, tree `410864c3b6f77702d269178bc93e6bf3ee15a98c`; reader correction PASS within its recorded local scope. |
| Independent reader reviewer | `01a0949c-ff3f-72e1-96b8-f49cf11b5272` | `C:/Users/atooz/.codex/worktrees/5265/plantgeo` | CHANGES REQUESTED on `3143a227d680feb4c2379b936115e20030dd8b7f`, tree `56c8613f1bca6f2ec5bd03f194f8638ae268cf78`; then PASS on `ee81a641d6f1f3fcd38bb96a7e860b0065710419`, tree `410864c3b6f77702d269178bc93e6bf3ee15a98c`, as reported by the coordinator. The final verdict does not erase the initial review. |
| Gapless publication author | `01a0947d-554e-7002-8e47-53f88e76bc85` | `C:/Users/atooz/.codex/worktrees/511c/plantgeo` | Reviewed local packet `ff5fc9f5a971083fbcc5254067818734c55c69e3`, tree `eba5b0ad50e793b2565020d233dae303e7f6d8ab`; historical scope and publication blockers reconciled, remaining writer gates open. |
| Renderer boundary author | `01a0947d-704e-7d31-bf21-7d3f47002a61` | `C:/Users/atooz/.codex/worktrees/3b59/plantgeo` | Reviewed local packet `7f6f2e5945496fc9b3ab1bddece35e76eb3224a0`, tree `b63a22b4e5e4a290a1b70a180d70b1e84d679f9b`; retained synthetic artifacts bounded, live renderer and mobile gates open. |
| Executor reconciliation author | `01a0947d-86e9-7472-90d4-7e7c230ef838` | `C:/Users/atooz/.codex/worktrees/ad33/plantgeo` | Reviewed local packet `f84da6169740604112c71d95379c6493109e4e5e`, tree `ad9dcc1e65938a28a60269c7e5514339db0bbe75`; definition custody reconciled, effective ownership, cutoff and recovery proofs remain open. |
| Operational release packet author | `01a0947d-9cac-78a3-93ee-2ca18b7b05cd` | `C:/Users/atooz/.codex/worktrees/b292/plantgeo` | Reviewed local packet `5af6c398944b0ab34c4c5b6a900f3c55cf709156`, tree `81dae514941b60bb4d6375627cfdf3d7168771ee`; retained release custody only, no release authorization or fresh operational validation. |
| Production verdict author | `01a0947d-b44b-7540-b06e-8532c19de1fb` | `C:/Users/atooz/.codex/worktrees/229e/plantgeo` | Reviewed local packet `6b1088dd6c834140c82ee21970a2ea7e91e231c2`, tree `0f5d7bfeb00f9b411c269d65239487dd8a14279c`; verdict RED, production acceptance blocked. |

The [reader correction receipt](../../parquet_reader_cutover_acceptance_20260901/evidence/reader-ui-contract-20260912.md),
[publication packet](../../gapless_parquet_publication_20260901/evidence/publication-evidence-packet-20260912.md),
[renderer boundary](../../multiscale_polygon_surface_20260901/evidence/renderer-local-proof-boundary-20260912.md),
[executor reconciliation](../../gapless_parquet_publication_20260901/evidence/executor-definition-reconciliation-20260912.md),
[operational release packet](../../parquet_production_acceptance_20260901/evidence/operational-release-packet-20260912/README.md)
and [RED verdict](../../parquet_production_acceptance_20260901/evidence/final-verdict-20260912.md)
retain each lane's evidence and open gates.

The five evidence deltas were cherry-picked in the requested order, with source
commit trailers, after the final reader commit. Their local integration custody
is distinct from the author commits and trees above:

| Order | Source commit | Applied commit | Applied tree |
| --- | --- | --- | --- |
| 1 — gapless publication | `ff5fc9f5a971083fbcc5254067818734c55c69e3` | `e9a3bd1983a83e1082b789ac2cc841ea7f9bbefd` | `865ce294a85ee7ca4c793059ba32ef9d22e514d1` |
| 2 — renderer boundary | `7f6f2e5945496fc9b3ab1bddece35e76eb3224a0` | `a4f83eba30c336f6e1882ce5163930f764ac498a` | `9bee1f2b970521b44359d2c3b6585aec94ce89f1` |
| 3 — executor reconciliation | `f84da6169740604112c71d95379c6493109e4e5e` | `c7d0050f32fd472d97f2bb7e2c599de224cce18c` | `7cf0b8276b070a47536b6782ad3a5450513c4cb2` |
| 4 — operational release | `5af6c398944b0ab34c4c5b6a900f3c55cf709156` | `43bf932c303bb48b8fc08c98a6ad9ad0524adcc4` | `3d62ac0448fa8495103160efd69eabac0019ca54` |
| 5 — local production RED verdict | `6b1088dd6c834140c82ee21970a2ea7e91e231c2` | `2469180d87ae28a832436f21b2a13a6383d0298d` | `58c2a45b16c2ad7aab8b85c11ab0df3fd29b484b` |

Only `conductor/tracks.md` conflicted, in the first cherry-pick. Resolution kept
the final reader row from `ee81a641` and the incoming gapless publication row;
every registry status was preserved. No incomplete track was closed or archived.
The reader, gapless publication and multiscale tracks remain `active`; production
acceptance remains `blocked`, with its local verdict **RED** and G0–G7 open.

The audit is separately preserved and excluded: commit
`ad7ae728b48d975bca248187716ad03896506313`, tree
`b3973d15965061496b6a6fac0fe297ba0ab80bd6`, parent/base
`64f4f892bd2b744cc097c7f76a1f239997b80f52`. Its sole added path,
`conductor/tracks/platform_experience_qa_20260911/evidence/remaining-acceptance-matrix-20260912.md`,
is not imported. These are the Git-verified identities confirmed by the
coordinator's custody correction. The audit commit, `15347eaa` and `843b4b3` are
not integration ancestors; no merge or cherry-pick of `main` was performed.

The five author packets retain their original parent/source attribution to
`843b4b313e03447594b23a67f75c3062b2b1a024`, tree
`9533bb9e5423240630935df0cd012cd8ead15504`. Original source-manifest hashes,
documentation-review receipts and retained measurements continue to describe
those frozen source snapshots, not the integrated tree. The final documentation
commit appends this section and replaces exactly two absent audit-file links,
in the renderer boundary and production acceptance matrix, with the exact
commit/tree/path custody above. Those integration-only edits receive separate
documentation validation and independent review; original packet receipts do
not certify the substituted text. The final commit/tree and validation result
are reported in the integration handoff, avoiding a circular self-hash here.

Application receipt reuse is conditional on exact equality to `ee81a641` of the
`src` tree and every tracked blob outside `conductor/`, including package and
test-harness files, plus a changed-test selector plan with no application
surface. This integration executes documentation checks only; it makes no new
application-test, service, populated-data, mobile/touch, deployment, scheduled
burn-in or production-release claim. Local author and reviewer custody does not
fill any unbound operational-owner or release-reviewer gate in the RED packet.

### Visual pair integration after the approved evidence candidate

The visual continuation starts from approved integration
`33f8a885796ea7aa2885e8da6cf9055cc27124cf`, tree
`8c0ac6182b37b746cc840968d9dafd6d69d1e35c`, on the same reader integration
branch and worktree. The documentation-only validation and unchanged-runtime
claim above belong to that earlier integration. This later visual pair changes
runtime source and receives its own final integrated checks and bounded browser
receipt. All earlier custody entries remain historical evidence.

| Role | Task and source checkout | Immutable source and review history |
| --- | --- | --- |
| Original visual author | `01a09485-9640-7ef3-8901-4e651ae6bbb5`; `C:/Users/atooz/.codex/worktrees/2764/plantgeo` | Commit `f62666363abd06e8c4aab287ad5f7353b1ec305d`, tree `10d00468d1c3d621814f7111056a39ce3ca24865`. Initial internal PASS was followed by external compatibility CHANGES REQUESTED. The source is preserved immutable and integrated only with the refusal repair; it was never accepted as a standalone integrated candidate. |
| Refusal repair author | `01a094d1-776f-7920-a448-6394f5c7c736`; `C:/Users/atooz/.codex/worktrees/067b/plantgeo` | Commit `51d7f36ce007fba12ac35fa4e86737056a4051cd`, tree `a63b9eead80f852b75fe611eccc5b5124bf88e6d`. Source/custody PASS; preserves the original author commit as its parent and repairs refusal while native layout is pending. |
| External compatibility reviewer | `01a0949c-ff3f-72e1-96b8-f49cf11b5272` | CHANGES REQUESTED on `f626663` alone, then PASS on the ordered pair `f626663` + `51d7f36` for local integration, as supplied by the coordinator. This approval does not close production or M3 gates. |

The authorized pair was cherry-picked in order, without importing either source
parent history. Both applied commits retain their original source trailers:

| Order | Source commit | Applied commit | Applied tree |
| --- | --- | --- | --- |
| 1 — visual feature | `f62666363abd06e8c4aab287ad5f7353b1ec305d` | `86e3661ad4f34ddfeebde739d2e5340f22f465f4` | `d3d09dbc359aa81d105b4b652d0588626d88b91c` |
| 2 — P2 refusal repair | `51d7f36ce007fba12ac35fa4e86737056a4051cd` | `0fa16a53b2936c95a053cd68ea4590c10728f4f9` | `d1bb86c0ea52554ecbecd92ee282971038a75868` |

Only the multiscale `plan.md` conflicted, during the first cherry-pick. Resolution
preserved the approved plan verbatim, including “September 12 — current local
evidence boundary,” then appended the complete “September 12 — isolated vegetation
scalar field candidate” section from the visual patch. Its active frontmatter
and every open M3 gate remain unchanged. The combined pair contains 82 paths;
this ledger-only custody follow-up is separate from that pair.

The [visual packet](../../multiscale_polygon_surface_20260901/evidence/scalar-field-vegetation-20260912.md)
and its [refusal repair receipt](../../multiscale_polygon_surface_20260901/evidence/scalar-field-refusal-repair-20260912/report.json)
remain source-bound evidence. Final integrated validation consists of one run
each of `check:data-boundary`, `type-check`, `lint` and `test:changed` against
`33f8a885`, plus the selected scalar fixture scenarios `field,mixed-days,detail,mode-race,refusal-layout,reload`.
That browser selection is exactly 12 cases across desktop and mobile; it is not
the full 29-case fixture or live-data acceptance. The frozen integrated result,
exact path/blob comparisons, check counts and separate reviewer verdict are bound
in the final integration handoff, rather than attributed to the original author
receipts or embedded as a circular commit hash here.

The default-off `NEXT_PUBLIC_SCALAR_FIELD_RENDERER_LAYERS` flag remains unset in
the application environment; the standalone synthetic fixture opts in only
inside its own bundle. Reader paths retain their approved `33f8a885` blobs.
The excluded audit commit and matrix remain absent, production acceptance stays
**blocked / RED**, and all operational-owner, live-data, M3 and release gates
remain open. This continuation performs no push, deployment or live
infrastructure/data access.
