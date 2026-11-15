import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import AboutPage, { metadata as aboutMetadata } from "@/app/about/page";
import AffiliatePage, { metadata as affiliateMetadata } from "@/app/affiliate/page";
import HomePage, { metadata as homeMetadata } from "@/app/page";
import { generateMetadata as planMetadata } from "@/app/plan/[publicId]/page";
import { CrawlableHome } from "@/components/home/CrawlableHome";
import { SamplePlanBanner } from "@/components/plans/SamplePlanBanner";
import { SAMPLE_PLAN_LABEL, SAMPLE_PLAN_PUBLIC_ID } from "@/lib/plans/sample";

vi.mock("@/components/map/MapView", () => ({
  default: function MapViewStub() {
    return <div>Map</div>;
  },
}));

function followLink(name: string) {
  const link = screen.getByRole("link", { name });
  expect(link.getAttribute("href")).toBe("https://aevani.com");
  expect(link.getAttribute("rel") ?? "").not.toMatch(/nofollow/);
  return link;
}

describe("crawlable home", () => {
  it("leads with field-walk planning copy, funnel CTAs, bottom legal notes, and Aevani follow link", () => {
    render(<CrawlableHome />);
    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Read sun, slope, and water before you stake a single bed.",
      })
    ).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Open the Ada County Sample Plan" }).getAttribute("href")
    ).toBe("/plan/sample-ada-county");
    expect(
      screen.getByRole("link", { name: "Start your own site plan" }).getAttribute("href")
    ).toBe("/plan/new");
    expect(screen.getByRole("link", { name: "field guides" }).getAttribute("href")).toBe("/guides");
    expect(screen.getByRole("link", { name: "land-planning blog" }).getAttribute("href")).toBe(
      "/blog"
    );
    expect(screen.getByRole("link", { name: "become an affiliate" }).getAttribute("href")).toBe(
      "/affiliate"
    );
    followLink("Catalogue items from Aevani");
    expect(screen.getByText(/PlantGeo publishes no forecasts/)).toBeTruthy();
    expect(screen.getByText(/nothing from that preview catalogue is for sale yet/)).toBeTruthy();
    expect(document.body.textContent ?? "").not.toMatch(/\d+%/);
  });

  it("keeps that block in the home document and sets the document metadata", () => {
    render(<HomePage />);
    expect(document.getElementById("crawlable-home")?.textContent).toMatch(
      /Read sun, slope, and water before you stake a single bed/
    );
    expect(homeMetadata.title).toBe(
      "PlantGeo — Read sun, slope, drainage, and soils on your land"
    );
    expect(homeMetadata.description).toMatch(/Ada County sample parcel/);
    expect(homeMetadata.description).toMatch(/publishes no forecasts/);
  });
});

describe("sample plan route", () => {
  it("indexes the public sample parcel and normalizes legacy banner strings", async () => {
    const sampleMeta = await planMetadata({
      params: Promise.resolve({ publicId: SAMPLE_PLAN_PUBLIC_ID }),
    });
    expect(sampleMeta.title).toBe("Ada County Sample Plan — PlantGeo");
    expect(sampleMeta.alternates?.canonical).toBe("/plan/sample-ada-county");
    expect(sampleMeta.robots).toBeUndefined();

    const privateMeta = await planMetadata({
      params: Promise.resolve({ publicId: "plan_abc12345" }),
    });
    expect(privateMeta.robots).toEqual({ index: false, follow: false });

    const { rerender } = render(<SamplePlanBanner label="Sample plan — not a real customer" />);
    expect(screen.getByRole("status").textContent).toBe(SAMPLE_PLAN_LABEL);
    rerender(<SamplePlanBanner label={SAMPLE_PLAN_LABEL} />);
    expect(screen.getByRole("status").textContent).toBe(SAMPLE_PLAN_LABEL);
  });
});

describe("about site-planning paragraph", () => {
  it("adds the planning paragraph without replacing the lead", () => {
    const { container } = render(<AboutPage />);
    const text = container.textContent ?? "";
    expect(text).toMatch(/assembled entirely from/);
    expect(text).toMatch(/It is also a site-planning tool/);
    expect(text).toMatch(/nothing on it is for sale/);
    expect(text).toMatch(/A referral share for that path has not been set/);
    expect(text).not.toMatch(/Google Maps Platform has no first-party equivalent/);
    expect(text).toMatch(/Valhalla isolines/);
    followLink("Aevani's catalogue");
    expect(aboutMetadata.title).toBe("About PlantGeo — The whole map, unbundled");
  });
});

describe("affiliate page", () => {
  it("invites a site plan, quotes no rate, and links the catalogue as a follow link", () => {
    render(<AffiliatePage />);
    expect(
      screen.getByRole("heading", { level: 1, name: "Invite people to plan a site." })
    ).toBeTruthy();
    const text = document.body.textContent ?? "";
    expect(text).toMatch(/Those items are not for sale yet/);
    expect(text).toMatch(
      /A referral share has not been set, so this page does not quote a rate or a payout date/
    );
    expect(text).toMatch(/it is not a customer/);
    expect(text).not.toMatch(/\d+%/);
    followLink("Aevani catalogue items");
    expect(screen.getByLabelText("Name")).toBeTruthy();
    expect(screen.getByLabelText("Email")).toBeTruthy();
    expect(affiliateMetadata.alternates).toEqual({ canonical: "/affiliate" });
  });
});
