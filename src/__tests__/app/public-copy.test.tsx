import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import AboutPage, { metadata as aboutMetadata } from "@/app/about/page";
import AffiliatePage, { metadata as affiliateMetadata } from "@/app/affiliate/page";
import HomePage, { metadata as homeMetadata } from "@/app/page";
import { CrawlableHome } from "@/components/home/CrawlableHome";

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
  it("publishes the headline, the forecast refusal, and a follow link to Aevani", () => {
    render(<CrawlableHome />);
    expect(
      screen.getByRole("heading", { level: 1, name: "Plan the land on a map you can inspect." })
    ).toBeTruthy();
    expect(screen.getByText(/PlantGeo publishes no forecasts/)).toBeTruthy();
    expect(screen.getByText(/nothing from that catalogue is for sale yet/)).toBeTruthy();
    followLink("Catalogue items from Aevani");
    expect(screen.getByRole("link", { name: "Open the map" }).getAttribute("href")).toBe("/");
    expect(screen.getByRole("link", { name: "Read the About page" }).getAttribute("href")).toBe(
      "/about"
    );
    expect(screen.getByRole("link", { name: "Become an affiliate" }).getAttribute("href")).toBe(
      "/affiliate"
    );
  });

  it("keeps that block in the home document and sets the document title", () => {
    render(<HomePage />);
    expect(document.getElementById("crawlable-home")?.textContent).toMatch(
      /Plan the land on a map you can inspect/
    );
    expect(homeMetadata.title).toBe("PlantGeo — The whole map, unbundled");
    expect(homeMetadata.description).toMatch(/Pacific Northwest extract/);
    expect(homeMetadata.description).toMatch(/publishes no forecasts/);
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
