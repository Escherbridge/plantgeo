# Intervention single-intake and document composition plan

**Read-only intake preparation is complete.** The immutable candidate is `2fc6b30ac1b024e1c955dbf95552495608608a96`, tree `739509a6cfef8a601bae631e4810c697b6988a41`, with parent `89e8494422b8232c8f16dbffdcf2321c7ea17bc8` and base tree `777fe20689dd68c337677cc49e3fd4e63bfd4bcc`. The supplied source contains exactly **56 changed paths**. All source, evidence and document-base pin inspections passed. No source was applied, no tracked file was edited, no HEAD was changed and no test or database operation was run by this lane.

The exact machine inventory is [intervention-intake-preparation-20260911.json](intervention-intake-preparation-20260911.json), 59,250 bytes, SHA-256 `94f87ff0975a05b60394b02b91f8f86b166648e4e6a0dce17adf9b52478ea5b0`. It pins every changed path at base, canonical `416342fa5bea836e6a062549b87c2706f53ce45e`, and intervention source, including deleted-file absence. It also retains all evidence/E2E blob and SHA-256 pins, the exact MapView diff, seven shared-document comparisons and the complete supplied validation receipt.

## Current authorized order

The parent's latest instruction supersedes the earlier weather preparation's scheduling shorthand:

1. Root completes and commits the botanical/QA evidence checkpoint.
2. Root reconciles QA/PNW documentation commit `abdf99b993117f77a1912c05ccabd3df082a8903` before intervention. The coordinating reviewer reports 16 Conductor-only paths, no direct overlap with the 56 intervention paths, and two PNW registry rows plus one runbook row requiring additive preservation. This is a later coordination input, not part of the `416342f` pin inspection below.
3. After root supplies that exact checkpoint and authorizes the bounded executor step, take accepted intervention commit `2fc6b30ac1b024e1c955dbf95552495608608a96` **once with `--no-commit`**, reconcile the three declared shared-document packets below, and retain exact authored source/document pins. Do not act before that handoff. An independent reviewer evaluates the result before root commits it.
4. Root retains the exact post-intervention source/document pins and clean status, then sends that base to the dedicated weather owner. The weather owner rebases/rebuilds its two approved commits and implements the shared weather work itself. Root must not apply the older weather commits or duplicate those edits.
5. The returned immutable weather candidate is composed with the already accepted intervention source, followed by **one new full combined sweep** after all changes. No interim source tests are requested here.

Recheck the final transfer's actual target blobs against this inventory because `416342f` is the inspected boundary, not a promise that later files remain unchanged. The peer's bounded independent acceptance is recorded; this preparation is not a new code-approval verdict.

## Source overlap and the MapView duplicate trap

There are **zero divergent source-path overlaps** between the intervention delta and canonical changes from `89e8494` to `416342f`. There are also **zero overlaps with the 14 concrete shared weather runtime paths named in the weather handoff**. In particular, intervention changes neither `LayerManager.tsx` nor `wildfire.ts`. Its changed runtime routers are `src/lib/server/trpc/routers/contributions.ts` and `src/lib/server/trpc/routers/interventions.ts`.

`src/components/map/MapView.tsx` is the explicit reserved common surface. Its base/canonical blob is `5355ec7751bafe589aa9e012b3c9557d17063fdd`. The intervention source adds exactly these two lines, with no removals:

```tsx
import InterventionPublicationSync from "./InterventionPublicationSync";
```

```tsx
            <InterventionPublicationSync />
```

The import sits beside `MapKeyboardShortcuts`; the JSX immediately follows `<MapKeyboardShortcuts />`. The stored `evidence/mapview-publication-sync.patch` contains the same additions and is **already applied in `2fc6b30`**. Do not apply that patch separately after taking the commit. The same commit contains the corresponding sibling stub in `src/__tests__/components/map-view-render-count.test.tsx`; do not duplicate it either.

Boundary authoring has no root/LayerManager mount. `InterventionSubmitModal` mounts the editor through the existing MapProvider and portal. The parent reserves the intervention mount, routers and components while weather works from the new base. Later weather changes must retain this mount and shared lifecycle cleanup. Changes to shared documentation, registry and runbook are additive composition responsibilities, not proven runtime merge conflicts.

## Three packets, seven distinct shared documents

All three packets are committed under `conductor/tracks/intervention_boundary_authoring_20260911/evidence/`. They intentionally leave the seven target files untouched in the peer source.

| Packet | Targets and exact action |
| --- | --- |
| `shared-file-packet.md` | Update the existing community registry row and add the boundary companion once in `conductor/tracks.md`; add the community intervention checkpoint row once in `conductor/RUNBOOK.md`; append **Intervention publication refresh** to `src/lib/map/AGENTS.md`. Its MapView instructions document the already-applied source lines. |
| `boundary-doc-packet.md` | Append **Intervention boundary authoring** to `src/lib/map/AGENTS.md`, **Intervention boundary editor** to `src/components/map/AGENTS.md`, and **Recommendation site authoring and revisions** to `src/components/panels/AGENTS.md`. These describe authoring restrictions, gesture/focus cleanup and preservation of original complex geometry on cancel/resubmit. |
| `community-publication-doc-handoff.md` plus its `.patch` | Add **Community review publication contract — 2026-09-11** to `src/components/panels/AGENTS.md`, **Community publication and revision — 2026-09-11** to `src/lib/server/AGENTS.md`, and **Intervention original-geometry validation — 2026-09-11** to `src/lib/server/services/AGENTS.md`. Preserve the older bodies as superseded context. |

`src/lib/map/AGENTS.md` and `src/components/panels/AGENTS.md` each receive two different sections. Both sections must survive; neither packet is a whole-file replacement for the other.

The five AGENTS targets still match their declared base blobs at `416342f`:

| Target | Declared and current Git blob |
| --- | --- |
| `src/lib/map/AGENTS.md` | `d0b70e5174428db6fb180a77b55a4dc691c4b1c6` |
| `src/components/map/AGENTS.md` | `7c8ba0a8a82e751f1f1eec9485db88fe58323b9c` |
| `src/components/panels/AGENTS.md` | `ecd1fc2634494734476482cc2e2d0c8ba30814ca` |
| `src/lib/server/AGENTS.md` | `c611969bf345acd4322228b75f5b0f791515c34b` |
| `src/lib/server/services/AGENTS.md` | `4d27138d460a0ef0e45ec3ee981003b97f9d7c74` |

Only `conductor/tracks.md` and `conductor/RUNBOOK.md` have changed since their packet bases at the inspected boundary. Reconcile the supplied rows against their actual current content, preserving botanical, platform QA, weather, shrink/pivot and production-gate entries, including the later QA/PNW commit's two PNW registry rows and one runbook row. Never restore the peer's older whole-file versions.

The proposed registry rows and both incoming track metadata files use `in_progress`. The canonical registry declares `active`, `planned`, `blocked`, `complete` and `historical`. Record both intervention/community tracks as **active** consistently in current registry/metadata/plan/spec status fields when integrating, retaining their accepted bounded local evidence and open gates. Do not mark them complete merely because local acceptance passed. Community ML-label questions and future separate lifecycle-state migration remain open. Preserve historical track bodies and dated checkbox evidence while making current status coherent.

The shared publication contract is significant: only canonical review publication sets `published`; historical `approved` recovery is individualized and requires original author identity, consent, valid geometry and a new review note. The stale-review fix binds every decision to the displayed SHA-256 review version plus a SQL race predicate. Lifecycle must not overwrite publication status. The compatibility re-export at `src/lib/server/services/intervention-geometry.ts` preserves existing server imports while the pure schema moves to `src/lib/geo/intervention-geometry.ts`.

## Exact acceptance boundaries to retain

The supplied final receipt records **2,328 passing frontend tests, 13 skipped, 151 passing files and two skipped files**; **eight real community PostGIS cases**; zero boundary/type/lint exits; and lint **zero errors with 545 warnings**. The final browser run records **three passes, exit zero, in 1.2 minutes**. This is intervention peer evidence; it is not the current canonical host warning count or a combined weather/intervention receipt.

The independent review initially requested a stale-review correction and stronger browser evidence, then accepted the correction and final bounded candidate. The eight-case database surface includes the complete stale-card sequence. Browser evidence includes an unchanged warm empty viewport becoming visibly published, revision tile bytes and a rendered hover result, style replacement, mobile touch authoring and keyboard authoring. The four committed screenshots and review/receipt documents are hash-pinned in the manifest. This preparation has not rerun or visually reapproved them.

Keep these limits explicit:

- The isolated local cluster was **PostgreSQL 17.5 / PostGIS 3.5.2** at loopback port 55439, rather than the documented PG16/PostGIS3.4 combination. The supplied native PG16 login did not authenticate and its installation lacked PostGIS; native settings/data were left unchanged.
- The fixture uses **bounded baseline-derived community/auth DDL**, not a replay of all application/Alembic migrations.
- Browser tiles use the actual `geo.intervention_tiles` SQL through a **local HTTP adapter**, not the Martin executable. Basemap and unrelated environmental responses are fixtures.
- Cross-tab delivery is within one browser profile, not cross-device push. Draft style restoration and prior-handler-state cleanup retain focused mock evidence; the actual browser style-swap evidence covers publication.
- The 13 skipped tests are unrelated opt-in database cases. No production records, counts, deployment, community-to-ML acceptance or source publication were part of this candidate.
- The peer reports disposable databases removed and task-owned services/cluster stopped. This read-only preparation does not perform or claim a fresh port/database census.

The safe intake is the one source commit plus the three additive documentation packets, with no separate MapView patch and no test run until the weather-owned shared implementation is composed. Final runtime and action gates remain with their owners.
