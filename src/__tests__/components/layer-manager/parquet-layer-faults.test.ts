/**
 * The fault/notice stack, exercised as the pure function it became on 2026-09-18 (style review
 * W3, S12). It lived inline in `LayerManager.tsx` and could only be reached by rendering the whole
 * map; the entries below are the ones whose CONDITIONS are subtle enough that a future edit could
 * quietly drop them -- `gbif-empty`'s five-part gate, `botanical-viewport-read`'s tone flip, and
 * the land-context caption the stack now carries through.
 */
import { describe, expect, it } from "vitest";
import {
  buildParquetLayerFaults,
  type BotanicalLaneReport,
  type ParquetLayerFaultInput,
} from "@/components/map/layer-manager/parquet-layer-faults";

const QUIET_BOTANICAL: BotanicalLaneReport = {
  isQueryEnabled: false,
  band: "aggregate",
  hasViewportBbox: true,
  resultState: undefined,
  resultNote: null,
  isError: false,
  truncated: false,
  withheldCount: 0,
  occurrencesVisible: false,
  gbifVisible: false,
  gbifReadPhase: "idle",
  gbifFeatureCount: 0,
  hasDetailAnswer: false,
  detailTruncated: false,
  viewportCaption: null,
  viewportPhase: "idle",
  viewportErrorKind: null,
};

/** Every lane quiet: the stack must be empty unless a case below turns something on. */
function quietInput(overrides: Partial<ParquetLayerFaultInput> = {}): ParquetLayerFaultInput {
  return {
    burnSeverityEnabled: false,
    burnSnapshot: undefined,
    wavecLanes: [],
    vegetationEnabled: false,
    vegetationUnavailable: false,
    weatherEnabled: false,
    weatherUnavailable: false,
    fire: {
      isDrawn: false,
      state: "ready",
      truncated: false,
      absenceReason: null,
      isLaneNeverWritten: false,
    },
    botanical: QUIET_BOTANICAL,
    botanicalDetailMinZoom: 11,
    landContextFault: null,
    ...overrides,
  };
}

function idsOf(faults: ReturnType<typeof buildParquetLayerFaults>): string[] {
  return faults.map((fault) => fault.layerId);
}

function withBotanical(overrides: Partial<BotanicalLaneReport>): ParquetLayerFaultInput {
  return quietInput({ botanical: { ...QUIET_BOTANICAL, ...overrides } });
}

/** The state a GBIF-empty notice is owed in: detail band, settled read, no GBIF points back. */
function gbifEmptyLane(overrides: Partial<BotanicalLaneReport> = {}): ParquetLayerFaultInput {
  return withBotanical({
    isQueryEnabled: true,
    band: "detail",
    gbifVisible: true,
    gbifReadPhase: "success",
    hasDetailAnswer: true,
    gbifFeatureCount: 0,
    resultState: "detail",
    ...overrides,
  });
}

describe("a quiet map says nothing", () => {
  it("returns no entries when every lane is off and every read is clean", () => {
    expect(buildParquetLayerFaults(quietInput())).toEqual([]);
  });
});

describe("gbif-empty", () => {
  it("says the viewport returned no GBIF points once the read has settled", () => {
    const faults = buildParquetLayerFaults(gbifEmptyLane());

    expect(idsOf(faults)).toContain("gbif-empty");
    expect(faults.find((fault) => fault.layerId === "gbif-empty")?.message).toBe(
      "No GBIF occurrence points were returned for this viewport and current filters."
    );
  });

  it("refuses to call a CAPPED result an absence", () => {
    const faults = buildParquetLayerFaults(gbifEmptyLane({ detailTruncated: true }));

    expect(faults.find((fault) => fault.layerId === "gbif-empty")?.message).toContain(
      "prevents a complete assessment"
    );
  });

  it("stays silent while the read is still in flight", () => {
    expect(idsOf(buildParquetLayerFaults(gbifEmptyLane({ gbifReadPhase: "loading" })))).not.toContain(
      "gbif-empty"
    );
  });

  it("stays silent when no detail answer is in hand to be empty", () => {
    expect(idsOf(buildParquetLayerFaults(gbifEmptyLane({ hasDetailAnswer: false })))).not.toContain(
      "gbif-empty"
    );
  });

  it("stays silent when the viewport could not be measured", () => {
    expect(idsOf(buildParquetLayerFaults(gbifEmptyLane({ hasViewportBbox: false })))).not.toContain(
      "gbif-empty"
    );
  });

  it("stays silent when GBIF points actually came back", () => {
    expect(idsOf(buildParquetLayerFaults(gbifEmptyLane({ gbifFeatureCount: 3 })))).not.toContain(
      "gbif-empty"
    );
  });

  it("gives way to the below-floor notice outside the detail band", () => {
    const faults = buildParquetLayerFaults(gbifEmptyLane({ band: "aggregate" }));

    expect(idsOf(faults)).not.toContain("gbif-empty");
    expect(idsOf(faults)).toContain("gbif-below-detail-floor");
  });
});

describe("botanical-viewport-read", () => {
  it("carries the proxy lane's own sentence verbatim", () => {
    const faults = buildParquetLayerFaults(
      withBotanical({
        occurrencesVisible: true,
        band: "detail",
        viewportCaption: "Answered from the grid-0.25 support rung.",
        viewportPhase: "success",
      })
    );

    const entry = faults.find((fault) => fault.layerId === "botanical-viewport-read");
    expect(entry?.message).toBe("Answered from the grid-0.25 support rung.");
    // A rung substitution is a real answer, so it is a notice and never dressed as an outage.
    expect(entry?.tone).toBe("notice");
  });

  it("flips to a fault only when the proxy read itself failed", () => {
    const faults = buildParquetLayerFaults(
      withBotanical({
        occurrencesVisible: true,
        band: "detail",
        viewportCaption: "Specimen records could not be loaded (request_failed).",
        viewportPhase: "error",
        viewportErrorKind: "transport_fault",
      })
    );

    expect(faults.find((fault) => fault.layerId === "botanical-viewport-read")?.tone).toBe("fault");
  });

  // Style review W8, S4: a governed 400/503 arrives in the same `error` phase a dead socket does,
  // and before this the tone was read off the phase alone -- so the plane declining a question it
  // understood was dressed as an outage. The KIND is the discriminator, not the phase.
  it("keeps a notice tone for a governed refusal, which arrives in the same error phase", () => {
    const faults = buildParquetLayerFaults(
      withBotanical({
        occurrencesVisible: true,
        band: "detail",
        viewportCaption:
          "The botanical-occurrences plane is unavailable: no generation is published for this region.",
        viewportPhase: "error",
        viewportErrorKind: "governed_refusal",
      })
    );

    const entry = faults.find((fault) => fault.layerId === "botanical-viewport-read");
    expect(entry?.tone).toBe("notice");
    expect(entry?.message).toContain("no generation is published for this region");
  });

  it("stays silent when the lane reports nothing worth saying", () => {
    const faults = buildParquetLayerFaults(
      withBotanical({ occurrencesVisible: true, band: "detail", viewportCaption: null })
    );

    expect(idsOf(faults)).not.toContain("botanical-viewport-read");
  });

  it("stays silent below the detail band, where that lane does not draw", () => {
    const faults = buildParquetLayerFaults(
      withBotanical({
        occurrencesVisible: true,
        band: "aggregate",
        viewportCaption: "Answered from the grid-0.25 support rung.",
        viewportPhase: "success",
      })
    );

    expect(idsOf(faults)).not.toContain("botanical-viewport-read");
  });
});

describe("the land-context caption is carried through, not rebuilt", () => {
  it("appends whatever the land-context lane authored, tone included", () => {
    const fault = {
      layerId: "land-context-request-failed",
      tone: "fault" as const,
      message: "The land-context boundary read for this view failed before returning a state.",
    };

    expect(buildParquetLayerFaults(quietInput({ landContextFault: fault }))).toEqual([fault]);
  });

  it("adds nothing when that lane has nothing to say", () => {
    expect(buildParquetLayerFaults(quietInput({ landContextFault: null }))).toEqual([]);
  });
});

describe("the wave-C lanes and the fire lane keep their split", () => {
  it("calls an upstream outage a fault and a capped read a notice", () => {
    const faults = buildParquetLayerFaults(
      quietInput({
        wavecLanes: [
          {
            layerId: "sensors",
            isDrawn: true,
            state: "upstream_unavailable",
            truncated: false,
            subject: "Sensor station readings",
          },
          {
            layerId: "watersheds",
            isDrawn: true,
            state: "ready",
            truncated: true,
            subject: "Watershed boundaries",
          },
        ],
      })
    );

    expect(faults.find((fault) => fault.layerId === "sensors")?.tone).toBe("fault");
    expect(faults.find((fault) => fault.layerId === "watersheds-truncated")?.tone).toBe("notice");
  });

  it("quotes the fire lane's governed-absence evidence rather than paraphrasing it", () => {
    const faults = buildParquetLayerFaults(
      quietInput({
        fire: {
          isDrawn: true,
          state: "absent",
          truncated: false,
          absenceReason: "upstream published an empty day",
          isLaneNeverWritten: false,
        },
      })
    );

    const entry = faults.find((fault) => fault.layerId === "fire-absent");
    expect(entry?.tone).toBe("notice");
    expect(entry?.message).toContain("upstream published an empty day");
  });

  it("tells a lane that was never written apart from one missing day", () => {
    const neverWritten = buildParquetLayerFaults(
      quietInput({
        fire: {
          isDrawn: true,
          state: "not_generated",
          truncated: false,
          absenceReason: null,
          isLaneNeverWritten: true,
        },
      })
    );
    const oneDayMissing = buildParquetLayerFaults(
      quietInput({
        fire: {
          isDrawn: true,
          state: "not_generated",
          truncated: false,
          absenceReason: null,
          isLaneNeverWritten: false,
        },
      })
    );

    expect(neverWritten[0].message).toContain("has never been written");
    expect(oneDayMissing[0].message).toContain("This day has not been written");
  });
});
