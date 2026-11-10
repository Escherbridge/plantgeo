import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import RootLayout, { metadata } from "@/app/layout";
import { metadata as affiliateMetadata } from "@/app/affiliate/page";
import { metadata as samplePlanMetadata } from "@/app/plan/sample/page";
import { SITE_ORIGIN, siteDiscoveryGraph } from "@/lib/site-discovery";

vi.mock("next/font/google", () => ({
  Archivo: () => ({ variable: "font-display" }),
  Newsreader: () => ({ variable: "font-text" }),
  IBM_Plex_Mono: () => ({ variable: "font-label" }),
}));

vi.mock("@/lib/providers", () => ({
  Providers: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock("@/components/layout/ApplicationShell", () => ({
  ApplicationShell: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock("@/components/ui/toast", () => ({
  Toaster: () => null,
}));

const FORBIDDEN_HOSTS = ["railway.app", "plantgeo.app"];

function openGraphUrl(): string | URL | null | undefined {
  const openGraph = metadata.openGraph;
  if (!openGraph || !("url" in openGraph)) return undefined;
  return openGraph.url;
}

describe("site discovery metadata", () => {
  it("sets metadataBase and a self-path canonical and og:url on the root layout", () => {
    const metadataBase = metadata.metadataBase;
    if (!(metadataBase instanceof URL)) {
      throw new Error("metadataBase must be a URL");
    }
    expect(metadataBase.origin).toBe(SITE_ORIGIN);
    expect(metadata.alternates?.canonical).toBe("./");
    expect(openGraphUrl()).toBe("./");
    expect(metadata.openGraph).toMatchObject({
      images: [expect.objectContaining({ url: `${SITE_ORIGIN}/og.png` })],
    });

    const serialized = `${metadataBase.origin} ${String(metadata.alternates?.canonical)} ${String(openGraphUrl())}`;
    for (const host of FORBIDDEN_HOSTS) {
      expect(serialized).not.toContain(host);
    }
  });

  it("keeps explicit route canonicals on the paths those pages already publish", () => {
    expect(affiliateMetadata.alternates?.canonical).toBe("/affiliate");
    expect(samplePlanMetadata.alternates?.canonical).toBe("/plan/sample");
  });

  it("renders Organization and WebSite JSON-LD in the root document", () => {
    const html = renderToStaticMarkup(
      <RootLayout>
        <main>page</main>
      </RootLayout>,
    );
    expect(html).toContain('type="application/ld+json"');
    expect(html).toContain(JSON.stringify(siteDiscoveryGraph));

    const graph = siteDiscoveryGraph["@graph"];
    expect(graph.map((node) => node["@type"])).toEqual(["Organization", "WebSite"]);
    expect(graph[0]).toMatchObject({
      name: "Escherbridge, LLC",
      url: SITE_ORIGIN,
    });
    expect(graph[1]).toMatchObject({
      name: "PlantGeo",
      url: SITE_ORIGIN,
      publisher: { "@id": `${SITE_ORIGIN}/#organization` },
    });

    const serialized = JSON.stringify(siteDiscoveryGraph);
    for (const host of FORBIDDEN_HOSTS) {
      expect(serialized).not.toContain(host);
    }
    expect(serialized).not.toContain("sameAs");
    expect(Object.keys(graph[0])).not.toContain("logo");
    expect(serialized).not.toContain('"logo"');
  });
});
