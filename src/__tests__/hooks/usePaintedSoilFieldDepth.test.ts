import { afterEach, describe, expect, it } from "vitest";
import { cleanup, renderHook } from "@testing-library/react";
import { DEFAULT_SOIL_FIELD_DEPTHS, type SoilFieldDepth } from "@/lib/environmental/soil-field";
import { usePaintedSoilFieldDepth } from "@/hooks/usePaintedSoilFieldDepth";
import { useSoilStore } from "@/stores/soil-store";

/**
 * The drawn soil depth is a property of the ANSWER: a retained frame (react-query's placeholder
 * while the new depth loads) is still the old depth's data, however many times it is re-rendered
 * or re-`setData`ed. See src/components/map/AGENTS.md §soil-painted-depth.
 */
const ANSWER = { type: "FeatureCollection", features: [] };

type Props = { answer: unknown; retained: boolean; depth: SoilFieldDepth };

afterEach(() => {
  cleanup();
  useSoilStore.setState({ paintedFieldDepth: DEFAULT_SOIL_FIELD_DEPTHS });
});

describe("usePaintedSoilFieldDepth", () => {
  it("advances the drawn depth only when the requested depth's own answer is live", () => {
    const painted = () => useSoilStore.getState().paintedFieldDepth.moisture;
    const { rerender } = renderHook(
      ({ answer, retained, depth }: Props) =>
        usePaintedSoilFieldDepth("moisture", { answer, isShowingRetainedAnswer: retained }, depth),
      { initialProps: { answer: ANSWER, retained: false, depth: "surface" } as Props }
    );

    // Root zone requested; the observer still shows surface's retained frame, then an opacity
    // drag re-renders that same frame.
    rerender({ answer: ANSWER, retained: true, depth: "root-zone" });
    rerender({ answer: { ...ANSWER }, retained: true, depth: "root-zone" });
    expect(painted()).toBe("surface");

    // A hidden layer has no answer at all, so it cannot claim a depth either.
    rerender({ answer: undefined, retained: false, depth: "root-zone" });
    expect(painted()).toBe("surface");

    rerender({ answer: ANSWER, retained: false, depth: "root-zone" });
    expect(painted()).toBe("root-zone");
  });
});
