import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { AgentInteraction } from "@/components/map/AgentInteraction";

describe("AgentInteraction", () => {
  it("keeps simultaneous workspace and map-popup precision choices independent", () => {
    const onAnalyze = vi.fn();
    render(<>
      <AgentInteraction embedded coordinates={[-118.123456, 45.612345]} onAnalyze={onAnalyze} onProposeIntervention={vi.fn()} onClose={vi.fn()} />
      <AgentInteraction coordinates={[-117, 44]} onAnalyze={vi.fn()} onProposeIntervention={vi.fn()} onClose={vi.fn()} />
    </>);
    const embedded = within(screen.getByRole("region", { name: "Confirm proposal location analysis" }));
    const popup = within(screen.getByRole("dialog", { name: "Location actions" }));
    fireEvent.click(embedded.getByRole("radio", { name: /high-precision selected location/i }));
    expect((popup.getByRole("radio", { name: /approximate location/i }) as HTMLInputElement).checked).toBe(true);
    expect((embedded.getByRole("radio", { name: /high-precision selected location/i }) as HTMLInputElement).checked).toBe(true);
    expect(onAnalyze).not.toHaveBeenCalled();
    fireEvent.click(embedded.getByRole("button", { name: "Send for analysis" }));
    expect(onAnalyze).toHaveBeenCalledWith("exact");
  });

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
