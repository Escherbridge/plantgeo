import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import sitemap from "@/app/sitemap";
import EssayPage from "@/app/writing/[slug]/page";
import WritingIndexPage from "@/app/writing/page";
import { SiteFooter } from "@/components/layout/SiteFooter";
import { ESSAYS } from "@/lib/writing/essays";

describe("writing index", () => {
  it("lists both essays and the footer points at the index", () => {
    render(<WritingIndexPage />);
    for (const essay of ESSAYS) {
      expect(screen.getByRole("link", { name: essay.title }).getAttribute("href")).toBe(
        `/blog/${essay.slug}`
      );
    }
    render(<SiteFooter pathname="/about" />);
    expect(screen.getByRole("link", { name: "Blog" }).getAttribute("href")).toBe("/blog");
  });

  it("lists the essays once, at their blog canonicals", () => {
    const urls = sitemap().map((entry) => entry.url);
    expect(urls).toContain("https://plantgeo.aevani.com/writing");
    expect(urls).toContain("https://plantgeo.aevani.com/blog/not-forecast-beyond-today");
    expect(urls).toContain("https://plantgeo.aevani.com/blog/plan-the-land-then-pick-the-tools");
    expect(urls).not.toContain("https://plantgeo.aevani.com/writing/not-forecast-beyond-today");
  });
});

describe("essays", () => {
  it("renders the forecast essay", async () => {
    const page = await EssayPage({ params: Promise.resolve({ slug: "not-forecast-beyond-today" }) });
    render(page);
    expect(
      screen.getByRole("heading", { level: 1, name: "Not forecast beyond today" })
    ).toBeTruthy();
    expect(screen.getByText(/Nothing has produced a forecast row/)).toBeTruthy();
    expect(screen.getByText(/1,568-cell lattice/)).toBeTruthy();
    const about = screen.getByRole("link", { name: "About PlantGeo" });
    expect(about.getAttribute("href")).toBe("https://plantgeo.aevani.com/about");
    expect(about.getAttribute("rel") ?? "").not.toMatch(/nofollow/);
  });

  it("renders the planning essay with follow links to Aevani", async () => {
    const page = await EssayPage({
      params: Promise.resolve({ slug: "plan-the-land-then-pick-the-tools" }),
    });
    render(page);
    expect(
      screen.getByRole("heading", { level: 1, name: "Plan the land, then pick the tools" })
    ).toBeTruthy();
    expect(screen.getByText(/No referral rate is being quoted/)).toBeTruthy();
    for (const name of ["Aevani", "Aevani's catalogue", "Aevani homepage"]) {
      const link = screen.getByRole("link", { name });
      expect(link.getAttribute("href")).toBe("https://aevani.com");
      expect(link.getAttribute("rel") ?? "").not.toMatch(/nofollow/);
    }
    const community = screen.getByRole("link", {
      name: "PlantGeo community — public strategy requests",
    });
    expect(community.getAttribute("href")).toBe("https://plantgeo.aevani.com/community");
    expect(community.getAttribute("rel") ?? "").not.toMatch(/nofollow/);
    expect(screen.getByText("validated strategy evidence not published")).toBeTruthy();
  });
});
