import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ fetchBounded: vi.fn() }));

vi.mock("@/lib/server/http/bounded-upstream", () => ({
  fetchBounded: mocks.fetchBounded,
}));

import { toAsciiHeaderValue } from "@/app/api/tiles/vegetation/header-value";
import { GET } from "@/app/api/tiles/vegetation/ndvi/[time]/[z]/[y]/[x]/route";
import { GIBS_NDVI_PRODUCT, getNDVITileUrl, resolveGibsNdviDate } from "@/lib/vegetation";

/** Every code unit of a legal HTTP header value fits in one byte. */
const BYTE_STRING = /^[\x20-\x7E]*$/;

function tileRequest(time: string, z = "6", y = "22", x = "10") {
  return GET(new Request(`https://plantgeo.test/api/tiles/vegetation/ndvi/${time}/${z}/${y}/${x}`), {
    params: Promise.resolve({ time, z, y, x }),
  });
}

function upstreamTile(overrides: Partial<{ ok: boolean; status: number }> = {}) {
  return {
    ok: true,
    status: 200,
    headers: new Headers(),
    bytes: new Uint8Array([0x89, 0x50, 0x4e, 0x47]),
    text: "",
    bodyError: null,
    ...overrides,
  };
}

describe("header value sanitisation", () => {
  it("keeps typography in the attribution the UI displays", () => {
    // Guards against "fixing" the crash by flattening the display constant.
    const hasNonByteStringCharacter = [...GIBS_NDVI_PRODUCT.attribution].some(
      (character) => character.codePointAt(0)! > 255
    );
    expect(hasNonByteStringCharacter).toBe(true);
  });

  it("yields a value a Response can actually be constructed with", () => {
    const headerValue = toAsciiHeaderValue(GIBS_NDVI_PRODUCT.attribution);
    expect(() =>
      new Response(null, { headers: { "X-Data-Source": headerValue } })
    ).not.toThrow();
    expect(headerValue).toMatch(BYTE_STRING);
  });

  it("survives display strings that would otherwise throw while building a Response", () => {
    const hazards = [
      "NASA EOSDIS GIBS — MODIS/Terra NDVI 8-Day",
      "Copernicus – Sentinel‑2 “surface reflectance”",
      "Météo-France · 25 °C",
      "ISRIC — World Soil Information…",
      "unattributed\r\nX-Injected: yes",
      "日本語のみ",
    ];

    for (const hazard of hazards) {
      const headerValue = toAsciiHeaderValue(hazard);
      expect(headerValue, hazard).toMatch(BYTE_STRING);
      const response = new Response(null, { headers: { "X-Data-Source": headerValue } });
      expect(response.headers.get("x-data-source")).toBe(headerValue);
    }
  });

  it("drops the newline rather than smuggling a second header", () => {
    const response = new Response(null, {
      headers: { "X-Data-Source": toAsciiHeaderValue("source\r\nX-Injected: yes") },
    });
    expect(response.headers.get("x-injected")).toBeNull();
  });
});

describe("NDVI tile proxy route", () => {
  beforeEach(() => {
    mocks.fetchBounded.mockReset();
  });

  it("returns the tile with an attribution header instead of throwing", async () => {
    mocks.fetchBounded.mockResolvedValue(upstreamTile());

    const response = await tileRequest("2025-06-15");

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("image/png");
    expect(response.headers.get("x-data-source")).toMatch(BYTE_STRING);
    expect(response.headers.get("x-data-source")).not.toBe("");
    expect(response.headers.get("x-composite-date")).toBe("2025-06-15");
  });

  it("relays an upstream 404 rather than substituting a neighbouring date", async () => {
    mocks.fetchBounded.mockResolvedValue(upstreamTile({ ok: false, status: 404 }));

    const response = await tileRequest("2025-06-15");

    expect(response.status).toBe(404);
  });

  it("refuses a malformed time before reaching upstream", async () => {
    const response = await tileRequest("july-2025");

    expect(response.status).toBe(422);
    expect(mocks.fetchBounded).not.toHaveBeenCalled();
  });

  it("refuses a zoom deeper than the product is published at", async () => {
    const response = await tileRequest("2025-06-15", "12", "1000", "600");

    expect(response.status).toBe(404);
    expect(mocks.fetchBounded).not.toHaveBeenCalled();
  });
});

describe("GIBS NDVI temporal extent", () => {
  // The published extent starts 2025-02-12; the WMTS capabilities document is
  // the source of truth. Anything earlier 404s at GIBS.
  const EXTENT_START = "2025-02-12";

  it("refuses months before the product was published", () => {
    expect(resolveGibsNdviDate(2024, 7)).toBeNull();
    expect(resolveGibsNdviDate(2025, 1)).toBeNull();
    expect(resolveGibsNdviDate(2000, 3)).toBeNull();
  });

  it("emits no tile URL at all for an out-of-extent month", () => {
    // An empty template is what makes the layer report "unavailable" instead of
    // issuing requests that every one of them 404s.
    expect(getNDVITileUrl(2024, 7)).toBe("");
  });

  it("never resolves a date earlier than the published extent", () => {
    const now = new Date(Date.UTC(2026, 5, 20));
    for (let year = 2000; year <= 2026; year += 1) {
      for (let month = 1; month <= 12; month += 1) {
        const resolved = resolveGibsNdviDate(year, month, now);
        if (resolved === null || resolved === "default") continue;
        expect(resolved >= EXTENT_START, `${year}-${month} resolved to ${resolved}`).toBe(true);
      }
    }
  });

  it("resolves in-extent months to their representative composite date", () => {
    const now = new Date(Date.UTC(2026, 5, 20));
    expect(resolveGibsNdviDate(2025, 2, now)).toBe("2025-02-15");
    expect(resolveGibsNdviDate(2025, 6, now)).toBe("2025-06-15");
  });

  it("tracks the newest composite for the current month and refuses the future", () => {
    const now = new Date(Date.UTC(2026, 5, 20));
    expect(resolveGibsNdviDate(2026, 6, now)).toBe("default");
    expect(resolveGibsNdviDate(2026, 7, now)).toBeNull();
  });
});
