"use client";

/**
 * The three herbarium rows and the two lanes that feed them, in one hook.
 *
 * Extracted from `LayerManager.tsx` on 2026-09-18 (style review W3, S12). Every comment below
 * moved with the code it explains; nothing about the behaviour changed. What this buys is that
 * the band exclusivity, the two-lane split and the click resolution can be read -- and later
 * tested -- without the twenty other layers around them.
 *
 * Rationale: see `src/components/map/AGENTS.md` section "The botanical viewport lanes".
 */

import { useCallback, useEffect, useMemo } from "react";
import {
  botanicalBandForZoom,
  useBotanicalOccurrencesQuery,
  type BotanicalBand,
} from "@/hooks/useViewportProxiedLayers";
import {
  useBotanicalOccurrences,
  type BotanicalOccurrencesPhase,
} from "@/hooks/useBotanicalOccurrences";
import { useBotanicalOccurrenceStore } from "@/stores/botanical-occurrence-store";
import {
  presentBotanicalCell,
  presentBotanicalOccurrence,
} from "@/lib/environmental/botanical-presentation";
import {
  botanicalOccurrencesToGeoJSON,
  describeBotanicalOccurrencesState,
} from "@/components/map/layers/BotanicalOccurrencesLayer";
import { botanicalRichnessToGeoJSON } from "@/components/map/layers/BotanicalRichnessLayer";
import {
  botanicalEffortToGeoJSON,
  type BotanicalEffortMeasure,
} from "@/components/map/layers/BotanicalCollectionEffortLayer";
import { GBIF_COLLECTION_KEY } from "@/lib/environmental/botanical-governance-status";
import type { BotanicalLaneReport } from "@/components/map/layer-manager/parquet-layer-faults";

export interface UseBotanicalViewportLanesOptions {
  /** The shared viewport bbox string; null whenever the camera cannot be measured. */
  bbox: string | null;
  zoom: number;
  occurrencesVisible: boolean;
  richnessVisible: boolean;
  effortVisible: boolean;
  gbifVisible: boolean;
}

export interface BotanicalViewportLanes {
  /** `"detail"` above the specimen floor, `"aggregate"` below it. */
  band: BotanicalBand;
  /** UBC specimen points from the PROXY lane, already stripped of GBIF's own collection. */
  occurrencesGeoJSON: GeoJSON.FeatureCollection | null;
  /** The proxy lane's read phase, handed to the layer so a failed read takes it down. */
  occurrencesReadPhase: BotanicalOccurrencesPhase;
  /** GBIF points from the PROXY lane, filtered to GBIF's own collection key. */
  gbifGeoJSON: GeoJSON.FeatureCollection | null;
  richnessGeoJSON: GeoJSON.FeatureCollection | null;
  effortGeoJSON: GeoJSON.FeatureCollection | null;
  /** The aggregate answer's generation, drawn onto the richness layer. */
  aggregateReleaseSetId: string | null;
  /** Which effort measure the filters panel has selected. */
  effortMeasure: BotanicalEffortMeasure;
  /** Resolves a clicked occurrence id across BOTH lanes into the store the panel reads. */
  onSelectOccurrence: (occurrenceId: string) => void;
  /** Everything the fault stack asks about these two lanes. */
  laneReport: BotanicalLaneReport;
}

export function useBotanicalViewportLanes({
  bbox,
  zoom,
  occurrencesVisible,
  richnessVisible,
  effortVisible,
  gbifVisible,
}: UseBotanicalViewportLanesOptions): BotanicalViewportLanes {
  // The three herbarium specimen rows, over ONE read.
  //
  // The plane answers `detail` (individual specimens) at zoom >= 11 and `aggregate` (support
  // cells) below it, from the same route on the same inputs -- so one query serves all three
  // toggles and they can never disagree about which generation they are drawing. The zoom band
  // is what decides which of them can draw at all, which is the exclusivity
  // `BotanicalOccurrencesLayer`'s own docstring delegates to "whichever container chooses which
  // layer to mount". This hook is that container's data half.
  //
  // The floor is 11 and the `ZOOM_TIERS` ladder's rungs are 0/5/9/13, so `botanicalBandForZoom`
  // is a bare comparison rather than a `resolveZoomTier` call -- rounding onto the ladder would
  // send a zoom-11 detail request to the z9 aggregate rung.
  const botanicalFilters = useBotanicalOccurrenceStore((state) => state.filters);
  const setBotanicalResponse = useBotanicalOccurrenceStore((state) => state.setLastResponse);
  const setSelectedBotanicalFeature = useBotanicalOccurrenceStore(
    (state) => state.setSelectedFeature
  );
  const band = botanicalBandForZoom(zoom);
  // Enabled when a toggle that could actually DRAW at this band is on. A lit occurrence switch
  // at zoom 4 fetches nothing, because the detail layer cannot draw there and the aggregate
  // layers are off -- the gate is about what is drawable, not about what is switched on.
  //
  // NEVER at the detail band (single-upstream-read decision, 2026-09-18, W8-D). Both toggles that
  // draw there -- the UBC layer and GBIF -- now read exclusively from the proxy lane below, which
  // already carries everything this tRPC read used to add: filters are echoed in the request URL
  // (`buildRequestUrl`) and the release-set pin comes back on the answer (`releaseSetId`). Reading
  // both lanes at once was two upstream round trips for one screen; this query stays for the two
  // aggregate layers only, which have no proxy-served rung yet. See
  // `src/components/map/AGENTS.md` section "The botanical viewport lanes" for the full argument.
  const isQueryEnabled = band === "detail" ? false : richnessVisible || effortVisible;
  // Empty filter strings are "unset" in the store, never sent as an empty query parameter --
  // the service would read `family=` as a filter matching nothing.
  const botanicalQuery = useBotanicalOccurrencesQuery(bbox, {
    enabled: isQueryEnabled,
    zoom,
    taxonConceptId: botanicalFilters.taxon_concept_id || undefined,
    family: botanicalFilters.family || undefined,
    collectionKey: botanicalFilters.collection_key || undefined,
    eventStart: botanicalFilters.event_start || undefined,
    eventEnd: botanicalFilters.event_end || undefined,
    spatialQuality: botanicalFilters.spatial_quality,
  });
  // ENABLEMENT SCOPES THIS ANSWER, NOT ITS PRESENCE AND NOT THE BAND ALONE (style review W8 B3,
  // W9 S1).
  //
  // `useBotanicalOccurrencesQuery` is configured `placeholderData: keepPreviousData`, and a
  // react-query observer that has been DISABLED still serves the previous key's answer -- so
  // `botanicalQuery.data` keeps holding whatever this lane last landed, for as long as the hook is
  // mounted. Testing `botanicalQuery.data !== undefined` let a retained zoom-6 answer supply the
  // release-set pin for a zoom-11 screen it was never read for; W8's fix scoped it by band, which
  // closed the DETAIL case and left the identical one at the AGGREGATE band, where the observer is
  // equally disabled whenever both aggregate toggles are off (`isQueryEnabled`, above) and the
  // retained frame is equally still in hand.
  //
  // `isQueryEnabled` is the whole predicate, not one half of a conjunction: it is false at the
  // detail band BY CONSTRUCTION, so it subsumes the band test rather than sitting beside it. One
  // predicate, so the two can never be applied to different consumers again -- which is exactly
  // how the defect survived: `isError` below already used this one, and nothing else did.
  //
  // Every tRPC-sourced value in this hook reads `aggregateBandAnswer` and nothing reads
  // `botanicalQuery.data` directly, so enabling and answering are asked once for the cells, the
  // counts, the notes, the truncation flag, the reported state, the store publication and the pin
  // alike. The answer's OWN discriminant is still checked separately below: enabled says a read
  // was issued for this band, `state === "aggregate"` says the answer in hand is that band's.
  const aggregateBandAnswer = isQueryEnabled ? botanicalQuery.data : undefined;
  // The RETURNED state, never the requested band. A retained frame outlives the zoom it was
  // fetched for, so even within the aggregate band the answer's own discriminant is what keeps
  // specimen points out of the two choropleths.
  const botanicalAggregate =
    aggregateBandAnswer?.state === "aggregate" ? aggregateBandAnswer : null;
  // Presented into the snake_case vocabulary the three layer components were built against;
  // see src/lib/environmental/botanical-presentation.ts for why the two vocabularies differ.
  const botanicalCells = useMemo(
    () => (botanicalAggregate?.cells ?? []).map(presentBotanicalCell),
    [botanicalAggregate]
  );

  // The detail lane, over the proxy route -- now the ONLY read at the detail band (W8-D,
  // 2026-09-18).
  //
  // WHY THIS ONE IS AUTHORITATIVE. W3-A ran both lanes at once: the proxy
  // (`useBotanicalOccurrences` -> `/api/botanical-occurrences`) selects a serving rung from zoom
  // AND bbox size, so a wide viewport is answered from a coarser rung instead of refused (owner
  // decision 2026-09-18); the tRPC read carried the release-set pin and the filters-panel state.
  // The proxy answer is a strict superset of what the tRPC detail answer added: `buildRequestUrl`
  // already echoes every filter the store holds, and the answer already carries `releaseSetId`
  // and `servingRung`/the §4a pointer. So collapsing onto the proxy lane is additive, not a
  // narrowing -- nothing the filters panel or the release-set caption reads had a source only the
  // tRPC lane could provide. The tRPC read stays enabled for the two AGGREGATE layers only, which
  // have no proxy-served rung yet (the follow-up W3-A flagged); GBIF's toggle now shares this same
  // read too (`gbifVisible` joins the gate below) instead of running its own.
  const botanicalViewport = useBotanicalOccurrences({
    bbox,
    zoom,
    enabled: (occurrencesVisible || gbifVisible) && band === "detail",
    taxonConceptId: botanicalFilters.taxon_concept_id || undefined,
    family: botanicalFilters.family || undefined,
    collectionKey: botanicalFilters.collection_key || undefined,
    eventStart: botanicalFilters.event_start || undefined,
    eventEnd: botanicalFilters.event_end || undefined,
    spatialQuality: botanicalFilters.spatial_quality,
  });
  // The answer's OWN state, never the requested band -- same rule as `botanicalAggregate` above.
  const botanicalViewportDetail =
    botanicalViewport.answer?.state === "detail" ? botanicalViewport.answer : null;
  // `presentBotanicalOccurrence` is the only sanctioned seam between the proxy's camelCase and
  // the layer components' snake_case; see src/lib/environmental/botanical-presentation.ts.
  const botanicalViewportFeatures = useMemo(
    () => (botanicalViewportDetail?.features ?? []).map(presentBotanicalOccurrence),
    [botanicalViewportDetail]
  );
  // Excludes GBIF's own collection_key: GBIF draws through its own component/toggle, and without
  // this exclusion a reader with BOTH toggles on would see every GBIF point drawn twice (once per
  // source's independent MapLibre source/layer set). Any OTHER future collection_key still falls
  // through to this, the general layer -- only GBIF is carved out, because only GBIF has its own
  // sibling component so far.
  const occurrencesGeoJSON = useMemo(
    () =>
      botanicalViewportDetail === null
        ? null
        : botanicalOccurrencesToGeoJSON(
            botanicalViewportFeatures.filter(
              (feature) => feature.collection_key !== GBIF_COLLECTION_KEY
            ),
            botanicalViewportDetail.publishedAt
          ),
    [botanicalViewportDetail, botanicalViewportFeatures]
  );
  // One sentence for whatever the proxy lane currently reports -- including which rung answered
  // when it is not the one this zoom asked for. Null when the layer is simply drawing.
  const viewportCaption = describeBotanicalOccurrencesState(botanicalViewport);
  // The proxy lane's OWN phase, unmapped -- GBIF now reads the same one request the UBC layer
  // does (W8-D, 2026-09-18), so there is only one read-state vocabulary to consult, not two to
  // reconcile. `useBotanicalOccurrences` already distinguishes a failed request (`error`), an
  // in-flight or retained one (`loading`), and a landed answer with zero rows (`empty`) --
  // exactly the mapping this hook used to build by hand from the tRPC query's flags.
  const gbifReadPhase: BotanicalOccurrencesPhase = botanicalViewport.phase;
  // GBIF draws through its OWN component/toggle (`GbifOccurrencesLayer`), independently
  // switchable from the UBC layer, even though both now read the same proxy answer --
  // collection_key is the only thing that tells the two sources apart, so the split happens here,
  // once, on the shared feature list, rather than teaching either map component about the other's
  // source. See `GbifOccurrencesLayer.tsx`'s module doc for why this is a new component rather
  // than a parameterized mode of the UBC one.
  const gbifFeatures = useMemo(
    () =>
      botanicalViewportFeatures.filter(
        (feature) => feature.collection_key === GBIF_COLLECTION_KEY
      ),
    [botanicalViewportFeatures]
  );
  const gbifGeoJSON = useMemo(
    () =>
      botanicalViewportDetail === null
        ? null
        : botanicalOccurrencesToGeoJSON(gbifFeatures, botanicalViewportDetail.publishedAt),
    [botanicalViewportDetail, gbifFeatures]
  );
  const richnessGeoJSON = useMemo(
    () =>
      botanicalAggregate === null
        ? null
        : botanicalRichnessToGeoJSON(
            botanicalCells,
            botanicalAggregate.publishedAt,
            botanicalAggregate.releaseSetId
          ),
    [botanicalAggregate, botanicalCells]
  );
  const effortGeoJSON = useMemo(
    () =>
      botanicalAggregate === null
        ? null
        : botanicalEffortToGeoJSON(
            botanicalCells,
            botanicalAggregate.publishedAt,
            botanicalAggregate.releaseSetId
          ),
    [botanicalAggregate, botanicalCells]
  );
  // Published to the store so `BotanicalFilters` and the details panel describe the SAME answer
  // the map is drawing rather than issuing a second read of their own. Written in an effect
  // rather than during render because it is a store write; the dependency is the query result
  // object, which react-query keeps referentially stable until a new answer lands.
  //
  // THE BAND CHOOSES THE LANE, and nothing else does (style review W8, B3). Each band has exactly
  // one lane that reads it -- proxy at `detail`, tRPC above it -- and the answer published here is
  // that lane's or it is nothing. It is NOT a preference order over both: a fallback ordering let
  // a retained aggregate answer describe a detail screen.
  //
  // WITHHELD, NEVER STALE. While the band's own lane has no answer in hand the store is cleared
  // rather than left holding the other band's. A panel captioned with a generation the points on
  // screen were not read from is a false provenance claim; an empty caption is merely an absent
  // one. See `engineering-principles.md` section 4, "provenance travels with data".
  useEffect(() => {
    if (band === "detail") {
      setBotanicalResponse(
        botanicalViewportDetail === null
          ? null
          : {
              state: botanicalViewportDetail.state,
              releaseSetId: botanicalViewportDetail.releaseSetId,
              publishedAt: botanicalViewportDetail.publishedAt,
            }
      );
      return;
    }
    setBotanicalResponse(
      aggregateBandAnswer === undefined
        ? null
        : {
            state: aggregateBandAnswer.state,
            releaseSetId:
              "releaseSetId" in aggregateBandAnswer ? aggregateBandAnswer.releaseSetId : null,
            publishedAt:
              "publishedAt" in aggregateBandAnswer ? aggregateBandAnswer.publishedAt : null,
          }
    );
  }, [band, aggregateBandAnswer, botanicalViewportDetail, setBotanicalResponse]);
  // The generation the answer was actually served from, published back into the store.
  //
  // `BotanicalFilters` was built expecting a reader to TYPE a `release_set_id` and gates its
  // whole form until one is set, because when it was written this plane had no pointer route on
  // the client. It does now: `getBotanicalOccurrences` resolves `/current` server-side and the
  // browser never names a generation. So the id flows the other way -- the answer reports which
  // generation it came from, and the panel displays it. Written from the RESPONSE rather than
  // from a second `/current` read, so the id the panel shows is provably the one the cells on
  // the map were read from and not a pointer that has since moved.
  // Keyed on the BAND, the same discriminator the store publication above uses: the pin names the
  // generation the drawn cells came from, so it may only ever come from the lane that drew them.
  // The band picks the lane; `aggregateBandAnswer`'s enablement gate then decides whether that
  // lane actually spoke, so a retained frame from a disabled observer can reach neither.
  const servedBotanicalReleaseSetId =
    band === "detail"
      ? botanicalViewportDetail?.releaseSetId ?? null
      : botanicalAggregate?.releaseSetId ?? null;
  const setBotanicalReleaseSetId = useBotanicalOccurrenceStore((state) => state.setReleaseSetId);
  // Null is WRITTEN, not skipped: while the band's lane is still reading there is no generation to
  // name, and holding the previous band's would pin a release the map is not showing. The filters
  // panel gates on `release_set_id` being set, so an in-flight read correctly reads as "not yet
  // known" rather than as a stale certainty. `release_set_id` shapes no request on either lane, so
  // clearing it cannot feed back into a refetch.
  useEffect(() => {
    setBotanicalReleaseSetId(servedBotanicalReleaseSetId);
  }, [servedBotanicalReleaseSetId, setBotanicalReleaseSetId]);
  // Clicking a specimen opens the details panel, the same way every other layer with a detail
  // surface does it: the layer reports an id, this hook resolves it against the features it
  // already holds, and the store slice the panel reads is the only thing that changes. Resolved
  // here rather than in the layer because the layer only carries MapLibre feature properties --
  // four fields -- and the panel needs the whole record including rights and attribution.
  //
  // UBC points and GBIF points now come from the SAME proxy read (W8-D, 2026-09-18), so one
  // search over `botanicalViewportFeatures` covers both lanes a click could have landed on.
  const onSelectOccurrence = useCallback(
    (occurrenceId: string) => {
      const selected = botanicalViewportFeatures.find(
        (feature) => feature.occurrence_id === occurrenceId
      );
      setSelectedBotanicalFeature(selected ?? null);
    },
    [botanicalViewportFeatures, setSelectedBotanicalFeature]
  );

  const laneReport: BotanicalLaneReport = {
    isQueryEnabled,
    band,
    hasViewportBbox: bbox !== null,
    resultState: aggregateBandAnswer?.state,
    resultNote:
      aggregateBandAnswer !== undefined && "note" in aggregateBandAnswer
        ? aggregateBandAnswer.note
        : null,
    // The same predicate `aggregateBandAnswer` uses, for the same reason: a disabled observer keeps
    // reporting the last key's `isError`, which would raise a fault for a read nobody issued. Read
    // off the query rather than the answer because an error has no answer to carry it.
    isError: isQueryEnabled && botanicalQuery.isError === true,
    truncated: botanicalAggregate?.truncated === true,
    // From the proxy answer: withheld-locality counts are a detail-band fact, and the detail
    // band's tRPC lane no longer runs (W8-D, 2026-09-18).
    withheldCount: botanicalViewportDetail?.counts.withheld ?? 0,
    occurrencesVisible,
    gbifVisible,
    gbifReadPhase,
    gbifFeatureCount: gbifFeatures.length,
    // From the proxy answer, not the tRPC one: `gbif-empty`'s "settled" gate now runs on the same
    // detail-band read the UBC layer draws from (W8-D, 2026-09-18).
    hasDetailAnswer: botanicalViewportDetail !== null,
    detailTruncated: botanicalViewportDetail?.truncated === true,
    viewportCaption,
    viewportPhase: botanicalViewport.phase,
    viewportErrorKind: botanicalViewport.error?.kind ?? null,
  };

  return {
    band,
    occurrencesGeoJSON,
    occurrencesReadPhase: botanicalViewport.phase,
    gbifGeoJSON,
    richnessGeoJSON,
    effortGeoJSON,
    aggregateReleaseSetId: botanicalAggregate?.releaseSetId ?? null,
    effortMeasure: botanicalFilters.effort_measure,
    onSelectOccurrence,
    laneReport,
  };
}
