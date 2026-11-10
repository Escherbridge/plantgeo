import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import ContactPage from "@/app/contact/page";
import PrivacyPage from "@/app/privacy/page";
import TermsPage from "@/app/terms/page";
import { readFileSync } from "node:fs";
import { ApplicationShell } from "@/components/layout/ApplicationShell";
import { OrganizationJsonLd } from "@/components/layout/OrganizationJsonLd";
import { SiteFooter } from "@/components/layout/SiteFooter";
import { AI_GENERATED_DISCLAIMER } from "@/lib/regional-intelligence";
import { SISTER_SITES } from "@/lib/sister-sites";

const navigation = vi.hoisted(() => ({ pathname: "/about" }));

vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
}));

vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: null, status: "unauthenticated" }),
  signOut: vi.fn(),
}));

const GOVERNING_LAW =
  "These terms are governed by the laws of the State of Idaho, and any dispute will be resolved in the state or federal courts located in Ada County, Idaho.";

describe("legal pages", () => {
  it("publishes the operator email, phone, and mailing address", () => {
    const { container } = render(<ContactPage />);
    const text = container.textContent ?? "";
    expect(screen.getByRole("heading", { level: 1, name: "Reach the operator." })).toBeTruthy();
    expect(text).toContain("Escherbridge, LLC");
    expect(text).toContain("Ahmed Zaher");
    expect(text).toContain("1140 S Colorado Ln, Boise, ID");
    expect(screen.getByRole("link", { name: "contact@escherbridge.com" }).getAttribute("href")).toBe(
      "mailto:contact@escherbridge.com"
    );
    expect(screen.getByRole("link", { name: "(509) 578-4603" }).getAttribute("href")).toBe(
      "tel:+15095784603"
    );
    expect(text).toMatch(/does not collect a message/);
    expect(text).toMatch(/24 hours/);
    expect(text).not.toMatch(/alerts@plantgeo\.io/);
    expect(text).not.toMatch(/Ada County/);
    expect(text).not.toMatch(/DRAFT:/);
  });

  it("states the account, map, assistant, and browser practices the code implements", () => {
    const { container } = render(<PrivacyPage />);
    const text = container.textContent ?? "";
    expect(text).toMatch(/bcrypt password hash/);
    expect(text).toMatch(/NextAuth session cookie/);
    expect(text).toMatch(/SHA-256/);
    expect(text).toMatch(/30 days/);
    expect(text).toMatch(/OpenRouter/);
    expect(text).toMatch(/Esri World Imagery/);
    expect(text).toMatch(/plantgeo-search/);
    expect(text).toMatch(/at least three records/);
    expect(text).toContain(AI_GENERATED_DISCLAIMER);
    expect(text).toContain("This site is operated by Escherbridge, LLC.");
    expect(text).toContain("1140 S Colorado Ln, Boise, ID");
    expect(text).toMatch(/privacy requests, and data requests/);
    expect(screen.getByRole("link", { name: "contact@escherbridge.com" }).getAttribute("href")).toBe(
      "mailto:contact@escherbridge.com"
    );
    expect(text).not.toMatch(/alerts@plantgeo\.io/);
    expect(text).not.toMatch(/Ada County/);
    expect(text).not.toMatch(/DRAFT:/);
  });

  it("states the publication and assistant rules", () => {
    const { container } = render(<TermsPage />);
    const text = container.textContent ?? "";
    expect(text).toMatch(/location, title, and description/);
    expect(text).toMatch(/not forecast beyond today/);
    expect(text).toMatch(/pending review is visible to signed-in readers/);
    expect(text).toContain(AI_GENERATED_DISCLAIMER);
    expect(text).toContain("This site is operated by Escherbridge, LLC.");
    expect(text).toContain("1140 S Colorado Ln, Boise, ID");
    expect(text).toMatch(/privacy requests, and data requests/);
    expect(screen.getByRole("link", { name: "contact@escherbridge.com" }).getAttribute("href")).toBe(
      "mailto:contact@escherbridge.com"
    );
    expect(text.split(GOVERNING_LAW)).toHaveLength(2);
    expect(text).not.toMatch(/arbitration|class action|jury/i);
    expect(text).not.toMatch(/alerts@plantgeo\.io/);
    expect(text).not.toMatch(/DRAFT:/);
  });
});

describe("site footer", () => {
  it("links contact, privacy, and terms", () => {
    render(<SiteFooter pathname="/privacy" />);
    expect(screen.getByRole("link", { name: "Contact" }).getAttribute("href")).toBe("/contact");
    expect(screen.getByRole("link", { name: "Privacy" }).getAttribute("href")).toBe("/privacy");
    expect(screen.getByRole("link", { name: "Terms" }).getAttribute("href")).toBe("/terms");
    expect(screen.getByRole("link", { name: "Privacy" }).getAttribute("aria-current")).toBe("page");
  });

  it("lists sister sites under An Escherbridge project, as follow links", () => {
    render(<SiteFooter pathname="/about" />);
    expect(screen.getByText("An Escherbridge project")).toBeTruthy();
    for (const site of SISTER_SITES) {
      const link = screen.getByRole("link", { name: site.label });
      expect(link.getAttribute("href")).toBe(site.href);
      expect(link.getAttribute("rel") ?? "").not.toMatch(/nofollow/);
      expect(site.href).not.toContain("plantgeo.aevani.com");
    }
    expect(screen.queryByRole("link", { name: /^PlantGeo/ })).toBeNull();
  });

  it("mounts on a navigable route and stays off a frameless route", () => {
    navigation.pathname = "/about";
    const { unmount } = render(
      <ApplicationShell>
        <p>Page</p>
      </ApplicationShell>
    );
    expect(screen.getByRole("navigation", { name: "Footer" })).toBeTruthy();
    unmount();

    navigation.pathname = "/login";
    render(
      <ApplicationShell>
        <p>Sign in</p>
      </ApplicationShell>
    );
    expect(screen.queryByRole("navigation", { name: "Footer" })).toBeNull();
  });
});

describe("organization JSON-LD", () => {
  it("names PlantGeo and lists the sister origins as sameAs", () => {
    const { container } = render(<OrganizationJsonLd />);
    const script = container.querySelector('script[type="application/ld+json"]');
    const data = JSON.parse(script?.textContent ?? "{}") as {
      name?: string;
      url?: string;
      sameAs?: string[];
    };
    expect(data.name).toBe("PlantGeo");
    expect(data.url).toBe("https://plantgeo.aevani.com");
    expect(data.sameAs).toEqual(SISTER_SITES.map((site) => site.href));
  });

  it("is mounted from the root layout", () => {
    const source = readFileSync(new URL("../../app/layout.tsx", import.meta.url), "utf8");
    expect(source).toContain("<OrganizationJsonLd");
  });
});
