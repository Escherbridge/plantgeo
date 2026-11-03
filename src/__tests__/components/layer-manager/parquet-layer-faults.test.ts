/**
 * The fault/notice stack, exercised as the pure function it became on 2026-09-18 (style review
 * W3, S12). It lived inline in `LayerManager.tsx` and could only be reached by rendering the whole
 * map; the entries below are the ones whose CONDITIONS are subtle enough that a future edit could
 * quietly drop them -- the land-context caption the stack now carries through. (The botanical
 * `gbif-empty` / `botanical-viewport-read` cases went with those layers on 2026-10-03.)
 */
import { describe, expect, it } from "vitest";
import {
  buildParquetLayerFaults,
  type ParquetLayerFaultInput,
} from "@/components/map/layer-manager/parquet-layer-faults";

/** Every lane quiet: the stack must be empty unless a case below turns something on. */
function quietInput(overrides: Partial<ParquetLayerFaultInput> = {}): ParquetLayerFaultInput {
  return {
    burnSeverityEnabled: false,
    burnSnapshot: undefined,
    wavecLanes: [],
    sampledLanes: [],
    satelliteVegetation: { isDrawn: false, period: null, unavailableReason: null },
    fire: {
      isDrawn: false,
      state: "ready",
      truncated: false,
      absenceReason: null,
      isLaneNeverWritten: false,
    },
    landContextFault: null,
    ...overrides,
  };
}

describe("a quiet map says nothing", () => {
  it("returns no entries when every lane is off and every read is clean", () => {
    expect(buildParquetLayerFaults(quietInput())).toEqual([]);
  });
});

describe("sampled layers distinguish coverage from publication and request failures", () => {
  const ready = { state: "ready" as const, requestedDay: "2026-08-06", servedDay: "2026-08-06", data: [], truncated: true };

  it.each(["weather", "vegetation"] as const)("discloses truncated %s even when the returned subset is empty", (layerId) => {
    const faults = buildParquetLayerFaults(quietInput({ sampledLanes: [{ layerId, isDrawn: true, result: ready, requestFailed: false, sampleCount: 0 }] }));
    expect(faults).toEqual(expect.arrayContaining([
      expect.objectContaining({ layerId: `${layerId}-truncated`, tone: "notice" }),
      expect.objectContaining({ layerId: `${layerId}-empty`, tone: "notice" }),
    ]));
    expect(faults.every((fault) => fault.tone === "notice")).toBe(true);
  });

  it("keeps a refresh failure visible beside retained, truncated readings", () => {
    const faults = buildParquetLayerFaults(quietInput({ sampledLanes: [{ layerId: "weather", isDrawn: true, result: ready, requestFailed: true, sampleCount: 2 }] }));
    expect(faults.find((fault) => fault.layerId === "weather-request-failed")).toMatchObject({ tone: "fault", message: expect.stringContaining("retained readings") });
    expect(faults.find((fault) => fault.layerId === "weather-truncated")).toMatchObject({ tone: "notice" });
    expect(faults.some((fault) => fault.layerId === "weather-empty")).toBe(false);
  });

  it("reports transport failure before any typed answer", () => {
    const faults = buildParquetLayerFaults(quietInput({ sampledLanes: [{ layerId: "vegetation", isDrawn: true, result: undefined, requestFailed: true, sampleCount: 0 }] }));
    expect(faults).toEqual([expect.objectContaining({ layerId: "vegetation-request-failed", tone: "fault", message: expect.stringContaining("No readings are shown") })]);
  });

  it("does not announce hidden measured data while satellite vegetation is selected", () => {
    const faults = buildParquetLayerFaults(quietInput({
      sampledLanes: [{ layerId: "vegetation", isDrawn: false, result: ready, requestFailed: true, sampleCount: 0 }],
      satelliteVegetation: { isDrawn: true, period: "Aug 2026", unavailableReason: null },
    }));
    expect(faults).toEqual([expect.objectContaining({ layerId: "vegetation-satellite-period", tone: "notice", message: expect.stringContaining("served acquisition date is not reported") })]);
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
