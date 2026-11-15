import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import maplibregl from "maplibre-gl";

const supported = (
  maplibregl as unknown as { supported: { mockReturnValue: (value: boolean) => void } }
).supported;
import { MapErrorBoundary } from "@/components/map/MapErrorBoundary";
import { MapWebGLFallback } from "@/components/map/MapWebGLFallback";
import { browserWebGLState, markWebGLAvailable, webGLIsAvailable } from "@/components/map/webgl-status";
import MapView from "@/components/map/MapView";

describe("WebGL fallback", () => {
  afterEach(() => {
    markWebGLAvailable();
    supported.mockReturnValue(true);
  });

  it("uses the published fallback copy and a browser help link", () => {
    render(<MapWebGLFallback />);
    expect(
      screen.getByRole("heading", { name: "This browser can't draw the map." }),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "PlantGeo needs WebGL to show the 3D map, and it's turned off or unavailable here. The rest of this page still works. Turning on hardware acceleration in your browser settings usually fixes it.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "How to turn on hardware acceleration" }).getAttribute("href")).toBe(
      "https://support.google.com/chrome/answer/6258784",
    );
  });

  it("does not treat a context-less test canvas as a user-facing WebGL failure", () => {
    expect(browserWebGLState()).toBe("unknown");
  });

  it("treats a missing supported() as able to build a test map, and a false result as unavailable", () => {
    expect(webGLIsAvailable(undefined)).toBe(true);
    expect(webGLIsAvailable(() => false)).toBe(false);
    expect(webGLIsAvailable(() => true)).toBe(true);
    expect(
      webGLIsAvailable(() => {
        throw new Error("Failed to initialize WebGL");
      }),
    ).toBe(false);
  });

  it("keeps the map slot when WebGL cannot start and does not construct MapLibre", async () => {
    supported.mockReturnValue(false);
    render(<MapView />);
    expect(
      await screen.findByRole("heading", { name: "This browser can't draw the map." }),
    ).toBeTruthy();
    expect(maplibregl.Map).not.toHaveBeenCalled();
  });

  it("renders the fallback from the map error boundary instead of letting the throw escape", () => {
    function BrokenMap(): never {
      throw new Error("Failed to initialize WebGL");
    }
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <MapErrorBoundary>
        <BrokenMap />
      </MapErrorBoundary>,
    );
    expect(
      screen.getByRole("heading", { name: "This browser can't draw the map." }),
    ).toBeTruthy();
    error.mockRestore();
  });
});
