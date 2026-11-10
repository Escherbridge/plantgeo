import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import BlogEssayPage from "@/app/blog/[slug]/page";
import BlogIndexPage from "@/app/blog/page";
import NewsArticlePage from "@/app/news/[slug]/page";
import NewsIndexPage from "@/app/news/page";
import SiteTypePage, {
  generateMetadata as siteTypeMetadata,
  generateStaticParams,
} from "@/app/site-types/[slug]/page";
import SiteTypesPage from "@/app/site-types/page";
import sitemap from "@/app/sitemap";
import { blogRssXml } from "@/lib/content/rss";
import { SITE_TYPES } from "@/lib/content/site-types";
import { readFileSync } from "node:fs";

describe("blog", () => {
  it("lists the two essays and no other posts", () => {
    render(<BlogIndexPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Notes" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Not forecast beyond today" }).getAttribute("href")).toBe(
      "/blog/not-forecast-beyond-today",
    );
    expect(
      screen.getByRole("link", { name: "Plan the land, then pick the tools" }).getAttribute("href"),
    ).toBe("/blog/plan-the-land-then-pick-the-tools");
    expect(screen.getAllByRole("heading", { level: 2 })).toHaveLength(2);
  });

  it("renders an essay at the blog url with article schema and a follow link", async () => {
    const page = await BlogEssayPage({
      params: Promise.resolve({ slug: "plan-the-land-then-pick-the-tools" }),
    });
    const { container } = render(page);
    const script = container.querySelector('script[type="application/ld+json"]');
    const data = JSON.parse(script?.textContent ?? "{}") as {
      "@graph"?: { "@type"?: string; mainEntityOfPage?: string; datePublished?: string }[];
    };
    const article = data["@graph"]?.find((node) => node["@type"] === "Article");
    expect(article?.datePublished).toBe("2026-11-09");
    expect(article?.mainEntityOfPage).toBe(
      "https://plantgeo.aevani.com/blog/plan-the-land-then-pick-the-tools",
    );
    const catalogue = screen.getByRole("link", { name: "Aevani's catalogue" });
    expect(catalogue.getAttribute("href")).toBe("https://aevani.com");
    expect(catalogue.getAttribute("rel") ?? "").not.toMatch(/nofollow/);
    expect(screen.getByText(/Escherbridge/)).toBeTruthy();
  });

  it("404s an unknown blog slug", async () => {
    await expect(
      BlogEssayPage({ params: Promise.resolve({ slug: "not-a-post" }) }),
    ).rejects.toThrow();
  });
});

const CHILD_FIGURES: Record<string, readonly string[]> = {
  "wui-parcel": [
    "9.4 percent",
    "more than 44 million homes",
    "32 percent of all housing nationwide",
    "47 percent",
    "31 percent",
    "179,000 square kilometers",
    "2.6 million homes",
    "0 to 5 feet",
    "5 to 30 feet",
    "30 to 100 feet",
    "12 feet wide",
    "vertical clearance of 15 feet",
    "more than 180 tests",
    "3 or more structures per acre",
    "one structure per 40 acres",
    "usually less than 1,000 acres",
    "9 August 2023",
    "16 September 2024",
    "not a PlantGeo endorsement",
    "will not predict a fire",
    "claims no agency deployments",
    "no referral share is quoted",
  ],
  "riparian-watershed-edges": [
    "1:24,000 or larger",
    "1:63,360 or larger",
    "1:100,000",
    "ninety percent",
    "167 feet",
    "0.02 inches",
    "1 October 2023",
    "1947 to 1992",
    "EPA 841-B-08-002",
    "does not impose legally binding requirements",
    "90%",
    "will not invent a buffer width",
  ],
  "homestead-mapped-soils": [
    "more than 95 percent",
    "Onsite investigation is needed",
    "8,000; 12,000; 15,000; 15,840; 20,000; 24,000",
    "2 July 2026",
    "1:12,000 or 1:24,000",
    "1:250,000",
    "NRS-122",
    "No Ada County soil-series names",
    "does not forecast what the soil will do next year",
    "no referral share is quoted",
  ],
};

const FORBIDDEN_CLAIMS = [
  /PlantGeo forecasts/i,
  /we forecast/i,
  /PlantGeo is deployed/i,
  /referral share is \d/i,
  /Firewise certification from PlantGeo/i,
];

function jsonGraph(container: HTMLElement): Record<string, unknown>[] {
  const script = container.querySelector('script[type="application/ld+json"]');
  const data = JSON.parse(script?.textContent ?? "{}") as { "@graph"?: Record<string, unknown>[] };
  return data["@graph"] ?? [];
}

describe("news and site types", () => {
  it("says news is empty", () => {
    render(<NewsIndexPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Nothing has been announced." })).toBeTruthy();
    expect(screen.getByText(/There are none to report yet/)).toBeTruthy();
  });

  it("publishes the site-type index with the three children and a labeled sample", () => {
    const { container } = render(<SiteTypesPage />);
    const view = within(container);
    expect(
      view.getByRole("heading", { level: 1, name: "The whole map, with the grain left on." }),
    ).toBeTruthy();
    expect(view.getByRole("link", { name: "WUI parcels" }).getAttribute("href")).toBe(
      "/site-types/wui-parcel",
    );
    expect(view.getByRole("link", { name: "Riparian and watershed edges" }).getAttribute("href")).toBe(
      "/site-types/riparian-watershed-edges",
    );
    expect(view.getByRole("link", { name: "Homestead, mapped soils" }).getAttribute("href")).toBe(
      "/site-types/homestead-mapped-soils",
    );
    expect(view.getByRole("link", { name: "Not forecast beyond today" }).getAttribute("href")).toContain(
      "/blog/not-forecast-beyond-today",
    );
    expect(view.getByRole("link", { name: "Plan the land, then pick the tools" }).getAttribute("href")).toContain(
      "/blog/plan-the-land-then-pick-the-tools",
    );
    const text = container.textContent ?? "";
    expect(text).toMatch(/PlantGeo publishes no forecasts and claims no agency deployments/);
    expect(text).toMatch(/is a sample on live public data, not a client/);
    expect(text).toMatch(/not for sale yet/);
    expect(text).toMatch(/Author: Escherbridge/);
    const sample = view.getByRole("link", { name: "Ada County plan" });
    expect(sample.getAttribute("href")).toContain("/plan/sample-ada-county");
    expect(sample.parentElement?.textContent ?? "").toMatch(/sample/);
    const catalogue = view.getByRole("link", { name: "Aevani’s catalogue" });
    expect(catalogue.getAttribute("href")).toBe("https://aevani.com");
    expect(catalogue.getAttribute("rel") ?? "").not.toMatch(/nofollow/);
    expect(catalogue.parentElement?.textContent ?? "").toMatch(/not for sale yet/);
    for (const claim of FORBIDDEN_CLAIMS) expect(text).not.toMatch(claim);

    const graph = jsonGraph(container);
    const collection = graph.find((node) => node["@type"] === "CollectionPage");
    const crumbs = graph.find((node) => node["@type"] === "BreadcrumbList");
    const list = collection?.mainEntity as {
      "@type"?: string;
      numberOfItems?: number;
      itemListElement?: { name?: string; url?: string }[];
    };
    expect(list?.["@type"]).toBe("ItemList");
    expect(list?.numberOfItems).toBe(3);
    expect(list?.itemListElement?.map((item) => item.url)).toEqual([
      "https://plantgeo.aevani.com/site-types/wui-parcel",
      "https://plantgeo.aevani.com/site-types/riparian-watershed-edges",
      "https://plantgeo.aevani.com/site-types/homestead-mapped-soils",
    ]);
    const crumbItems = crumbs?.itemListElement as { name?: string }[] | undefined;
    expect(crumbItems?.map((item) => item.name)).toEqual(["Home", "Site types"]);
  });

  it("publishes each site-type child with sources, an illustrative scenario, and article schema", async () => {
    expect(generateStaticParams().map((entry) => entry.slug)).toEqual(SITE_TYPES.map((page) => page.slug));
    for (const page of SITE_TYPES) {
      const rendered = await SiteTypePage({ params: Promise.resolve({ slug: page.slug }) });
      const { container } = render(rendered);
      const view = within(container);
      expect(view.getByRole("heading", { level: 1, name: page.h1 })).toBeTruthy();
      expect(view.getByRole("heading", { level: 2, name: "Sources" })).toBeTruthy();
      expect(view.getByRole("heading", { level: 2, name: "An illustrative scenario" })).toBeTruthy();
      const text = container.textContent ?? "";
      expect(text).toMatch(/Every stat on this page is cited/);
      expect(text).toMatch(/This is a worked example, not a client/);
      expect(text).toMatch(/Author: Escherbridge/);
      expect(text).toMatch(/Last reviewed: 2026-11-10, America\/Denver/);
      expect(text).toMatch(/not for sale yet/);
      for (const figure of CHILD_FIGURES[page.slug] ?? []) expect(text).toContain(figure);
      for (const source of page.sources) {
        expect(text).toContain(source.url);
        for (const extra of source.sameAs ?? []) expect(text).toContain(extra);
      }
      for (const claim of FORBIDDEN_CLAIMS) expect(text).not.toMatch(claim);
      const samples = view.getAllByRole("link").filter((link) =>
        (link.getAttribute("href") ?? "").includes("/plan/sample-ada-county"),
      );
      expect(samples.length).toBeGreaterThan(0);
      for (const link of samples) {
        expect(`${link.textContent ?? ""} ${link.parentElement?.textContent ?? ""}`).toMatch(/sample/i);
      }
      const catalogue = view.getAllByRole("link").find((link) =>
        /aevani\.com\/?$/.test(link.getAttribute("href") ?? ""),
      );
      expect(catalogue?.getAttribute("rel") ?? "").not.toMatch(/nofollow/);
      expect(catalogue?.parentElement?.textContent ?? "").toMatch(/not for sale yet/);
      for (const href of ["/blog/not-forecast-beyond-today", "/blog/plan-the-land-then-pick-the-tools"]) {
        expect(view.getAllByRole("link").some((link) => (link.getAttribute("href") ?? "").includes(href))).toBe(
          true,
        );
      }

      const graph = jsonGraph(container);
      const article = graph.find((node) => node["@type"] === "Article");
      const crumbs = graph.find((node) => node["@type"] === "BreadcrumbList");
      expect(article?.headline).toBe(page.h1);
      expect(article?.datePublished).toBe("2026-11-10");
      expect(article?.mainEntityOfPage).toBe(`https://plantgeo.aevani.com/site-types/${page.slug}`);
      expect(article?.author).toMatchObject({ name: "Escherbridge" });
      expect(article?.publisher).toMatchObject({ name: "Escherbridge", legalName: "Escherbridge, LLC" });
      const citations = article?.citation as {
        url?: string;
        datePublished?: string;
        dateModified?: string;
      }[];
      page.sources.forEach((source, index) => {
        expect(citations[index]?.url).toBe(source.url);
        expect(citations[index]?.datePublished).toBe(source.datePublished);
        expect(citations[index]?.dateModified).toBe(source.dateModified);
      });
      const crumbItems = crumbs?.itemListElement as { name?: string }[];
      expect(crumbItems.map((item) => item.name)).toEqual(["Home", "Site types", page.crumb]);
      const metadata = await siteTypeMetadata({ params: Promise.resolve({ slug: page.slug }) });
      expect(metadata.title).toBe(page.seoTitle);
      expect(metadata.description).toBe(page.metaDescription);
    }
  });

  it("404s unknown news and site-type slugs", async () => {
    expect(() => NewsArticlePage()).toThrow();
    await expect(SiteTypePage({ params: Promise.resolve({ slug: "not-a-site-type" }) })).rejects.toThrow();
  });
});

describe("discovery files", () => {
  it("adds the module indexes and blog canonicals to the sitemap", () => {
    const urls = sitemap().map((entry) => entry.url);
    for (const url of [
      "https://plantgeo.aevani.com/blog",
      "https://plantgeo.aevani.com/news",
      "https://plantgeo.aevani.com/site-types",
      "https://plantgeo.aevani.com/site-types/wui-parcel",
      "https://plantgeo.aevani.com/site-types/riparian-watershed-edges",
      "https://plantgeo.aevani.com/site-types/homestead-mapped-soils",
      "https://plantgeo.aevani.com/blog/not-forecast-beyond-today",
      "https://plantgeo.aevani.com/blog/plan-the-land-then-pick-the-tools",
    ]) {
      expect(urls).toContain(url);
    }
    expect(urls.filter((url) => url.includes("railway.app") || url.includes("plantgeo.app"))).toEqual(
      [],
    );
  });

  it("serves the essays as RSS and leaves the product feed page in place", () => {
    const rss = blogRssXml();
    expect(rss).toContain("<rss");
    expect(rss).toContain("Not forecast beyond today");
    expect(rss).toContain("https://plantgeo.aevani.com/blog/not-forecast-beyond-today");
    expect(rss).toContain("The interface is a map class, not a forecast");
    expect(rss).toContain("https://plantgeo.aevani.com/site-types/wui-parcel");
    expect(rss).toContain("https://plantgeo.aevani.com/site-types/riparian-watershed-edges");
    expect(rss).toContain("https://plantgeo.aevani.com/site-types/homestead-mapped-soils");
    expect(rss).not.toContain("railway.app");
    expect(rss).not.toContain("plantgeo.app");
    const feedPage = readFileSync("src/app/feed/page.tsx", "utf8");
    expect(feedPage).toContain("What people want to plant.");
    expect(readFileSync("src/app/feed.xml/route.ts", "utf8")).toContain("blogRssXml");
  });
});
