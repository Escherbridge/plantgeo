import { afterEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import EmbedCodeGenerator, { buildEmbedSnippet } from "@/components/embed/EmbedCodeGenerator";
import { useMapStore } from "@/stores/map-store";

const INITIAL_MAP_STATE = useMapStore.getState();
afterEach(() => useMapStore.setState(INITIAL_MAP_STATE, true));

describe("buildEmbedSnippet", () => {
  it("writes exactly the query params EmbedMap.tsx parses (center, zoom, style)", () => {
    const { embedSrc, iframeCode } = buildEmbedSnippet({
      baseUrl: "https://plantgeo.example",
      lat: "45.5152",
      lng: "-122.6784",
      zoom: "9.0",
      style: "satellite",
      markers: "",
      width: "800",
      height: "500",
    });

    expect(embedSrc).toBe(
      "https://plantgeo.example/embed?center=45.5152%2C-122.6784&zoom=9.0&style=satellite"
    );
    expect(iframeCode).toContain(`src="${embedSrc}"`);
    expect(iframeCode).toContain('width="800"');
    expect(iframeCode).toContain('height="500"');
  });

  it("omits the markers param entirely when the field is blank, rather than writing markers=", () => {
    const { embedSrc } = buildEmbedSnippet({
      baseUrl: "https://plantgeo.example",
      lat: "0",
      lng: "0",
      zoom: "4",
      style: "dark",
      markers: "   ",
      width: "800",
      height: "500",
    });
    expect(embedSrc).not.toContain("markers");
  });

  it("joins multiple pipe-delimited markers the same way EmbedMap.tsx's parseMarkers splits them", () => {
    const { embedSrc } = buildEmbedSnippet({
      baseUrl: "https://plantgeo.example",
      lat: "45.5",
      lng: "-122.6",
      zoom: "10",
      style: "light",
      markers: "45.5,-122.6,Portland|47.6,-122.3,Seattle",
      width: "800",
      height: "500",
    });
    const url = new URL(embedSrc);
    expect(url.searchParams.get("markers")).toBe("45.5,-122.6,Portland|47.6,-122.3,Seattle");
  });
});

describe("EmbedCodeGenerator", () => {
  it("seeds its fields from the live viewport and basemap style, not a hardcoded default", () => {
    useMapStore.setState({
      viewport: { ...INITIAL_MAP_STATE.viewport, latitude: 45.5152, longitude: -122.6784, zoom: 11.2 },
      currentStyle: "satellite",
    });

    render(<EmbedCodeGenerator />);

    const snippet = screen.getByText(/<iframe/).textContent ?? "";
    expect(snippet).toContain("center=45.5152%2C-122.6784");
    expect(snippet).toContain("zoom=11.2");
    expect(snippet).toContain("style=satellite");
  });
});
