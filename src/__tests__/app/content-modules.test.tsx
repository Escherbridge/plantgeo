import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import BlogEssayPage from "@/app/blog/[slug]/page";
import BlogIndexPage from "@/app/blog/page";
import NewsArticlePage from "@/app/news/[slug]/page";
import NewsIndexPage from "@/app/news/page";
import SiteTypePage from "@/app/site-types/[slug]/page";
import SiteTypesPage from "@/app/site-types/page";
import sitemap from "@/app/sitemap";
import { blogRssXml } from "@/lib/content/rss";
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

describe("news and site types", () => {
  it("says news is empty", () => {
    render(<NewsIndexPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Nothing has been announced." })).toBeTruthy();
    expect(screen.getByText(/There are none to report yet/)).toBeTruthy();
  });

  it("names the land kinds without linking unpublished pages", () => {
    render(<SiteTypesPage />);
    expect(
      screen.getByRole("heading", { level: 1, name: "The researched pages are not written yet." }),
    ).toBeTruthy();
    const text = document.body.textContent ?? "";
    expect(text).toMatch(/wildland–urban interface/);
    expect(text).toMatch(/none of those names is a link/);
    expect(text).toMatch(/PlantGeo publishes no forecasts/);
    expect(screen.getByRole("link", { name: "sample plan" }).getAttribute("href")).toBe(
      "/plan/sample-ada-county",
    );
    const aevani = screen.getByRole("link", { name: "Catalogue items from Aevani" });
    expect(aevani.getAttribute("href")).toBe("https://aevani.com");
    expect(aevani.getAttribute("rel") ?? "").not.toMatch(/nofollow/);
    expect(text).toMatch(/nothing from that catalogue is for sale yet/);
    expect(text).not.toMatch(/\d+%/);
  });

  it("404s unknown news and site-type slugs", () => {
    expect(() => NewsArticlePage()).toThrow();
    expect(() => SiteTypePage()).toThrow();
  });
});

describe("discovery files", () => {
  it("adds the module indexes and blog canonicals to the sitemap", () => {
    const urls = sitemap().map((entry) => entry.url);
    for (const url of [
      "https://plantgeo.aevani.com/blog",
      "https://plantgeo.aevani.com/news",
      "https://plantgeo.aevani.com/site-types",
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
    expect(rss).not.toContain("railway.app");
    expect(rss).not.toContain("plantgeo.app");
    const feedPage = readFileSync("src/app/feed/page.tsx", "utf8");
    expect(feedPage).toContain("What people want to plant.");
    expect(readFileSync("src/app/feed.xml/route.ts", "utf8")).toContain("blogRssXml");
  });
});
