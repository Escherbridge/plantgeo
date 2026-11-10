import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import RootLayout, { metadata } from "@/app/layout";
import { metadata as affiliateMetadata } from "@/app/affiliate/page";
import { metadata as samplePlanMetadata } from "@/app/plan/sample/page";
import { metadata as writingMetadata } from "@/app/writing/page";
import { generateMetadata as essayMetadata } from "@/app/writing/[slug]/page";
import robots from "@/app/robots";
import { SITE_ORIGIN } from "@/lib/public-site";
import { siteDiscoveryGraph } from "@/lib/site-discovery";
import { SISTER_SITES } from "@/lib/sister-sites";

vi.mock("next/font/google", () => ({
  Archivo: () => ({ variable: "font-display" }),
  Newsreader: () => ({ variable: "font-text" }),
  IBM_Plex_Mono: () => ({ variable: "font-label" }),
}));

vi.mock("@/lib/providers", () => ({
  Providers: ({ children }: { children: ReactNode }) => children,
}));

vi.mock("@/components/layout/ApplicationShell", () => ({
  ApplicationShell: ({ children }: { children: ReactNode }) => children,
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

  it("keeps explicit route canonicals, including the writing essays", async () => {
    expect(affiliateMetadata.alternates?.canonical).toBe("/affiliate");
    expect(samplePlanMetadata.alternates?.canonical).toBe("/plan/sample");
    expect(writingMetadata.alternates?.canonical).toBe("/writing");
    const essay = await essayMetadata({
      params: Promise.resolve({ slug: "not-forecast-beyond-today" }),
    });
    expect(essay.alternates?.canonical).toBe("/blog/not-forecast-beyond-today");
    const planning = await essayMetadata({
      params: Promise.resolve({ slug: "plan-the-land-then-pick-the-tools" }),
    });
    expect(planning.alternates?.canonical).toBe("/blog/plan-the-land-then-pick-the-tools");
  });

  it("points robots.txt at the sitemap on the public origin", () => {
    expect(robots().sitemap).toBe(`${SITE_ORIGIN}/sitemap.xml`);
    expect(robots().rules).toEqual({ userAgent: "*", allow: "/" });
  });

  it("renders one Organization and WebSite graph, with the sister sameAs list", () => {
    const html = renderToStaticMarkup(
      <RootLayout>
        <main>page</main>
      </RootLayout>,
    );
    expect(html.match(/type="application\/ld\+json"/g)).toHaveLength(1);
    expect(html).toContain(JSON.stringify(siteDiscoveryGraph));

    const graph = siteDiscoveryGraph["@graph"];
    expect(graph.map((node) => node["@type"])).toEqual(["Organization", "WebSite"]);
    expect(graph[0]).toMatchObject({
      name: "Escherbridge, LLC",
      url: SITE_ORIGIN,
      address: {
        "@type": "PostalAddress",
        addressLocality: "Boise",
        addressRegion: "ID",
        addressCountry: "US",
      },
      sameAs: SISTER_SITES.map((site) => site.href),
    });
    expect(Object.keys(graph[0]?.address ?? {}).sort()).toEqual([
      "@type",
      "addressCountry",
      "addressLocality",
      "addressRegion",
    ]);
    expect(graph[1]).toMatchObject({
      name: "PlantGeo",
      url: SITE_ORIGIN,
      publisher: { "@id": `${SITE_ORIGIN}/#organization` },
    });

    const serialized = JSON.stringify(siteDiscoveryGraph);
    for (const host of FORBIDDEN_HOSTS) {
      expect(serialized).not.toContain(host);
    }
    expect(Object.keys(graph[0] ?? {})).not.toContain("logo");
    expect(serialized).not.toContain('"logo"');
    expect(serialized).not.toContain("streetAddress");
    expect(serialized).not.toContain("postalCode");
  });
});
