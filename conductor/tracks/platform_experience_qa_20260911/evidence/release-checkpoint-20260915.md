---
type: evidence
recorded_on: 2026-09-15
status: checkpoint_verified_deployment_pending
---

# First application release checkpoint

Author `/root/workspace_social_audit`; execution and release coordination `/root`; independent reviews remain separately attributed in the linked evidence. The user authorized incremental pushes and live QA and reported no active users. **Deployment is pending:** this receipt does not record a commit, push, Railway success or live production acceptance. Root will add the observed deployment identity and outcome after execution; authorization is not evidence of success.

The final release candidate is **Candidate17 plus five independently reviewed static About-page string replacements**. It preserves the complete reviewed application source, including the shared migration runner and Python changes, rather than claiming a reconstructed frontend-only mixture was tested. The independent verifier approved the five replacements in a coordinator message; root applied the exact reviewed bytes and the affected-file lint exited zero. The full frontend pass below applies to C17 before these copy-only replacements; no new full-suite pass is claimed afterward. The [release scope audit](../../../../.omc/research/runbook-20260914/release/proposed-checkpoint.md) and path manifest identify exact source paths, dependencies, preexisting user work and capture exclusions. Candidate17 binds **1,749 files**. Every included candidate-covered path matched that freeze during the scope audit; all **18 changed/new Python source, test and owning-documentation paths** matched the successful Session6 Linux capture byte for byte. Canonical release documentation added afterward is not part of that source count. Root must review the final staged diff and security/file-budget scan; source identity does not imply every linked raw artifact is committed.

| Evidence lane | Recorded result and acceptance boundary |
| --- | --- |
| Final C17 frontend release sweep | **193 files / 2,542 tests passed, plus 12 tooling tests; exit 0.** This later full pass supplements the historical scoped Session11 receipt; it does not rewrite its earlier failures or lint scope. |
| Python source comparison | Current changed Python paths are byte-identical to the successful Linux comparison: **4,494 passed, 150 skipped, one expected failure**. This is a no-database comparison, not full Python release certification; original Windows failures and the skip ledger remain. Later bounded fixture/wheel proof is in the [Session8 packet](check-receipt-session8-20260914.json). |
| Session12 desktop | **1/1 accepted**, 49.315 seconds. Unobstructed temperature depth frames, distinct 5%/100% rendering, visibility off/on and isolated numeric VPD kPa labels at the bounded September4/5 dates were independently viewed and accepted. |
| Session12 first mobile attempt | **Failed; zero accepted visual stages**, 23.959 seconds. The local bridge path returned HTTP503/fetch failed before the first temperature stage settled. The UI showed its unavailable notice; no unobstructed mobile temperature/VPD acceptance follows. Original failure and clean process/port cleanup remain recorded. |
| Session13 stale moderation | **2/2 passed**, 17.380 seconds: existing identity prerequisite plus fresh consented proposal journey. The first publication persisted; the stale second rejection returned HTTP409/CONFLICT, kept published status and null review note, showed the truthful refusal alert and removed the stale queue row after refresh. Independent result and screenshot review accepted this deterministic stale-review variant. |
| Session15 actual publication dates | **1/1 accepted**, 29.775 seconds. Real current weather and selected moisture dates, weather gap, undated never-published SSURGO, actual August7 watershed release and truthful summary counts were independently checked against responses and paired images. The exposed desktop viewport is covered; the open manager prevents a whole-canvas visual claim. |

The reviewed Session12, Session13 and Session15 attempts each record cleanup with no remaining or unverified owned processes and the applicable local ports closed. Browser candidate identity comes from root's source freeze; their harness manifests alone do not embed or prove application source identity. Read-only environmental suites used bounded public serving reads and fixtures; the social suite used normal local authentication and explicit consent, with no analysis requests. None is a production result.

## Exact evidence bindings

The following SHA-256 values bind the retained evidence at receipt authoring. Relative `.omc` links identify local retained artifacts that are deliberately excluded from version control; the canonical receipts preserve scope and identity without publishing raw/private captures.

| Artifact | SHA-256 |
| --- | --- |
| [Frozen C17 source manifest](../../../../.omc/research/runbook-20260914/source-manifest-17.json) | `d2e12b9af07b6b9039c623362d0ed19b7a534d599ced4add5446b126d24b9635` |
| [Final full frontend log](../../../../.omc/research/runbook-20260914/release/frontend-full-candidate17.log) | `aa4ae5c3ccb4f965a26e48cebe78efdd20ca05f0eee03431d780a80f01379203` |
| [Final full frontend exit](../../../../.omc/research/runbook-20260914/release/frontend-full-candidate17.exit) | `13bf7b3039c63bf5a50491fa3cfd8eb4e699d1ba1436315aef9cbe5711530354` |
| [Python Linux check packet](check-receipt-session6-20260914.json) | `fc6ff11e9bd2ef966b3411886774adf45f81950389592599f4ac3848cd661331` |
| [Session11 engineering packet](check-receipt-session11-20260914.json) | `403edd9f8cbb766d0905a32988d479f28043eaaf1fd97207a3a1c4c5cbe90f39` |
| [Release path manifest](../../../../.omc/research/runbook-20260914/release/proposed-path-manifest.json) | `539629d62f75fe77922c2afec14aa61c1871daf72711addeb136a95275fe8c43` |
| [Session12 independent desktop/mobile result review](../../../../.omc/research/runbook-20260914/session12/independent-result-review.md) | `558c1131a46e69201a1e7ff0e58a3c591c6eba83e2a476ad17f81b421940b200` |
| [Session12 retained result bindings](../../../../.omc/research/runbook-20260914/session12/independent-result-bindings.json) | `7352f453b3a77216b3b67967b0e5e6d6b6178da899d3b8bb34f6c7c87ebea3d6` |
| [Session13 independent stale-review result](../../../../.omc/research/runbook-20260914/session13/independent-result-review.md) | `577efc0b7d99f5fc6da618696c89ea68d5acd64f9f8802e10988f29feec7c7c6` |
| [Session15 independent actual-date result](../../../../.omc/research/runbook-20260914/session15/independent-result-review.md) | `52167a94abbeb8f2ed966a5c319fc8bb555379bf768c60272a28a9272c0c21f5` |
| [Reviewed About patch manifest](../../../../.omc/research/runbook-20260914/about-copy-review/manifest.json) | `187e603fa0da93e2824c3a42470fdc6a26ba181613238f8216f98a9e30021289` |
| [Final About source](../../../../src/app/about/page.tsx) | `77d829a06a1fd365a3a0006a462a145afb0feab2c0cbfdb37712d53305dc2d02` |
| [About lint log](../../../../.omc/research/runbook-20260914/release/about-lint.log) | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| [About lint exit](../../../../.omc/research/runbook-20260914/release/about-lint.exit) | `9585fbece5c402088f2d6a2def7e60d1c7ffecc24b16d32d5823a064b008935b` |

## Release and runbook limits

Docker still runs boundary, type, lint, full npm tests and build; successful deployment/readiness and live QA must be observed separately. Historical SQL, journal, migration contract and lockfile are unchanged. The new shared helper must ship with both migration callsites, its Docker runtime copy and tooling test. A commit may trigger multiple Railway services: root must observe the actual frontend/data-service/job deployments rather than assume identical gates or inert restarts.

Preexisting availability work and the rest of the About-page changes are preserved. Five About strings were subsequently corrected through a separate reviewed copy batch; the release scope audit describes the earlier pre-correction state and must not be read as the final About hash. Raw browser/API/DB captures, private inputs, `.agentgraph` and `.omc` remain excluded. Earlier failed packets and attempts retain their original identities; this receipt does not erase them.

**Whole-platform QA remains open.** These are bounded engineering/browser results, not whole-case promotions: the formal matrix remains 0/220 accepted. The 150-test Python skip ledger includes 103 unconfigured database cases and 34 pending botanical registrations. All-layer/rung/history coverage, pending mobile variants, provider/live-model behavior, source admission, operational refresh and production validation remain separate obligations. The [progress rollup](progress-rollup-20260915.md) measures recorded checkboxes, not release readiness or percentage of the entire runbook completed.
