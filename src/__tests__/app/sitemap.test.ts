import { describe, expect, it } from "vitest";

import robots from "@/app/robots";
import sitemap from "@/app/sitemap";

const REQUIRED_URLS = [
  "https://plantgeo.aevani.com/",
  "https://plantgeo.aevani.com/contact",
  "https://plantgeo.aevani.com/privacy",
  "https://plantgeo.aevani.com/terms",
  "https://plantgeo.aevani.com/affiliate",
  "https://plantgeo.aevani.com/plan/new",
  "https://plantgeo.aevani.com/plan/sample-ada-county",
];

describe("sitemap.xml", () => {
  it("lists the public routes on the canonical origin", () => {
    const urls = sitemap().map((entry) => entry.url);
    for (const url of REQUIRED_URLS) {
      expect(urls).toContain(url);
    }
    expect(urls.some((url) => url.includes("plantgeo.app"))).toBe(false);
    expect(urls.some((url) => url.includes("railway.app"))).toBe(false);
  });
});

describe("robots.txt", () => {
  it("points crawlers at the sitemap", () => {
    expect(robots().sitemap).toBe("https://plantgeo.aevani.com/sitemap.xml");
  });
});
