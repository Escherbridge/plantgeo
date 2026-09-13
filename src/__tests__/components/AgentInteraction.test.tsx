import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AgentInteraction } from "@/components/map/AgentInteraction";

describe("AgentInteraction", () => {
  it("calls onProposeIntervention with no args when the propose button is clicked", () => {
    const onProposeIntervention = vi.fn();
    render(
      <AgentInteraction
        coordinates={[-118, 45.61]}
        onAnalyze={vi.fn()}
        onProposeIntervention={onProposeIntervention}
        onClose={vi.fn()}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: /propose intervention here/i }));
    expect(onProposeIntervention).toHaveBeenCalledTimes(1);
  });

  it("still calls onAnalyze from the send-for-analysis button", () => {
    const onAnalyze = vi.fn();
    render(
      <AgentInteraction
        coordinates={[-118, 45.61]}
        onAnalyze={onAnalyze}
        onProposeIntervention={vi.fn()}
        onClose={vi.fn()}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: /send for analysis/i }));
    expect(onAnalyze).toHaveBeenCalledWith("approximate");
  });

  it("calls onClose when cancel is clicked", () => {
    const onClose = vi.fn();
    render(
      <AgentInteraction
        coordinates={[-118, 45.61]}
        onAnalyze={vi.fn()}
        onProposeIntervention={vi.fn()}
        onClose={onClose}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
