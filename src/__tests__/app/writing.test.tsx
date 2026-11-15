import { describe, expect, it } from "vitest";
import { render, within } from "@testing-library/react";

import sitemap from "@/app/sitemap";
import EssayPage from "@/app/writing/[slug]/page";
import WritingIndexPage from "@/app/writing/page";
import { SiteFooter } from "@/components/layout/SiteFooter";
import { ESSAYS } from "@/lib/writing/essays";

describe("writing index", () => {
  it("lists all five essays and exposes the top-of-funnel sample plan gate", () => {
    const { container } = render(<WritingIndexPage />);
    const view = within(container);
    expect(ESSAYS.length).toBeGreaterThanOrEqual(5);
    for (const essay of ESSAYS) {
      expect(view.getByRole("link", { name: essay.title }).getAttribute("href")).toBe(
        `/blog/${essay.slug}`
      );
    }
    expect(
      view.getByRole("link", { name: "Open the Ada County Sample Plan" }).getAttribute("href")
    ).toBe("/plan/sample-ada-county");
    expect(
      view.getByRole("link", { name: "Start your own site plan" }).getAttribute("href")
    ).toBe("/plan/new");

    const { container: footerContainer } = render(<SiteFooter pathname="/about" />);
    expect(within(footerContainer).getByRole("link", { name: "Blog" }).getAttribute("href")).toBe(
      "/blog"
    );
  });

  it("lists every essay once, at its blog canonical", () => {
    const urls = sitemap().map((entry) => entry.url);
    expect(urls).toContain("https://plantgeo.aevani.com/writing");
    for (const essay of ESSAYS) {
      expect(urls).toContain(`https://plantgeo.aevani.com/blog/${essay.slug}`);
      expect(urls).not.toContain(`https://plantgeo.aevani.com/writing/${essay.slug}`);
    }
  });
});

describe("essays", () => {
  it("renders the three new practical field-walk posts with funnel gates and verified sources", async () => {
    const slugs = [
      "walking-a-five-acre-parcel-after-rain",
      "sun-arc-aspect-and-windbreaks-for-orchards-and-beds",
      "pairing-wetland-and-soil-maps-with-a-shovel-test",
    ] as const;

    for (const slug of slugs) {
      const page = await EssayPage({ params: Promise.resolve({ slug }) });
      const { container, unmount } = render(page);
      const view = within(container);
      const text = container.textContent ?? "";

      expect(view.getByRole("heading", { level: 1 })).toBeTruthy();
      expect(
        view.getByRole("link", { name: "Open the Ada County Sample Plan" }).getAttribute("href")
      ).toBe("/plan/sample-ada-county");
      expect(
        view.getByRole("link", { name: "Start your own site plan" }).getAttribute("href")
      ).toBe("/plan/new");
      expect(text).toMatch(/Map & legal note:/);
      expect(text).toMatch(/PlantGeo publishes no forecasts/);
      expect(text).not.toMatch(/\d+%/);
      expect(text).not.toMatch(/PrimusNeo/i);
      unmount();
    }
  });

  it("renders the forecast essay honestly and moves legal boilerplate to bottom note", async () => {
    const page = await EssayPage({ params: Promise.resolve({ slug: "not-forecast-beyond-today" }) });
    const { container } = render(page);
    const view = within(container);
    expect(
      view.getByRole("heading", { level: 1, name: "Not forecast beyond today" })
    ).toBeTruthy();
    const text = container.textContent ?? "";
    expect(text).toMatch(/nothing has produced a forecast row/i);
    expect(text).toMatch(/1,568-cell lattice/);
    const about = view.getByRole("link", { name: "About PlantGeo" });
    expect(about.getAttribute("href")).toBe("https://plantgeo.aevani.com/about");
    expect(about.getAttribute("rel") ?? "").not.toMatch(/nofollow/);
  });

  it("renders the planning essay with follow links to Aevani and no quoted referral percentage", async () => {
    const page = await EssayPage({
      params: Promise.resolve({ slug: "plan-the-land-then-pick-the-tools" }),
    });
    const { container } = render(page);
    const view = within(container);
    expect(
      view.getByRole("heading", { level: 1, name: "Plan the land, then pick the tools" })
    ).toBeTruthy();
    const text = container.textContent ?? "";
    expect(text).toMatch(/no referral rate is being quoted/i);
    expect(text).not.toMatch(/\d+%/);
    for (const name of ["Aevani", "Aevani's catalogue", "Aevani homepage"]) {
      const link = view.getByRole("link", { name });
      expect(link.getAttribute("href")).toBe("https://aevani.com");
      expect(link.getAttribute("rel") ?? "").not.toMatch(/nofollow/);
    }
    const community = view.getByRole("link", {
      name: "PlantGeo community — public strategy requests",
    });
    expect(community.getAttribute("href")).toBe("https://plantgeo.aevani.com/community");
    expect(community.getAttribute("rel") ?? "").not.toMatch(/nofollow/);
    expect(view.getByText("validated strategy evidence not published")).toBeTruthy();
  });
});
