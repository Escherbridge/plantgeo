import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import ContactPage, { metadata as contactMetadata } from "@/app/contact/page";
import PrivacyPage from "@/app/privacy/page";
import TermsPage from "@/app/terms/page";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { ApplicationShell } from "@/components/layout/ApplicationShell";
import { SiteFooter } from "@/components/layout/SiteFooter";
import { SiteDiscoveryJsonLd, siteDiscoveryGraph } from "@/lib/site-discovery";
import { AI_GENERATED_DISCLAIMER } from "@/lib/regional-intelligence";
import { SISTER_SITES } from "@/lib/sister-sites";
import { OPERATOR_EMAIL, OPERATOR_LINE, OPERATOR_LOCATION } from "@/lib/site-operator";

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

const STREET_FORM =
  /\b\d{1,6}\s+(?:[NSEW]\.?\s+)?[A-Za-z0-9.'-]+(?:\s+[A-Za-z0-9.'-]+){0,3}\s+(?:St|Street|Ave|Avenue|Ln|Lane|Rd|Road|Dr|Drive|Blvd|Boulevard|Way|Ct|Court|Pl|Place|Cir|Circle|Ter|Terrace|Hwy|Highway)\b/;
const ZIP_FORM = /\b\d{5}(?:-\d{4})?(?![\d-])/;
const POST_OFFICE_BOX = /\bP\.?\s*O\.?\s*Box\b/i;

function expectNoPostalForm(text: string) {
  expect(text).not.toMatch(STREET_FORM);
  expect(text).not.toMatch(ZIP_FORM);
  expect(text).not.toMatch(POST_OFFICE_BOX);
}

function line(text: string | null | undefined): string {
  return (text ?? "").replace(/\s+/g, " ").trim();
}

function renderedOperatorLine(container: HTMLElement): string {
  const paragraphs = [...container.querySelectorAll("p")].map((paragraph) => line(paragraph.textContent));
  return paragraphs.find((paragraph) => paragraph === OPERATOR_LINE) ?? "";
}

const PRIVATE_PAGE_DIRS = new Set([
  "(auth)",
  "admin",
  "api",
  "dashboard",
  "embed",
  "invite",
  "join",
  "moderation",
  "onboarding",
]);

function publicPageSources(): string[] {
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      if (PRIVATE_PAGE_DIRS.has(entry)) continue;
      const full = path.join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (entry.endsWith(".ts") || entry.endsWith(".tsx") || entry.endsWith(".md")) files.push(full);
    }
  };
  walk("src/app");
  walk("src/lib/content");
  walk("src/lib/writing");
  walk("src/components/layout");
  files.push("src/lib/site-operator.ts", "src/lib/site-discovery.tsx");
  return files.map((file) => readFileSync(file, "utf8"));
}

describe("legal pages", () => {
  it("publishes the operator email, phone, and city without a street address", () => {
    const { container } = render(<ContactPage />);
    const text = container.textContent ?? "";
    expect(screen.getByRole("heading", { level: 1, name: "Reach the operator." })).toBeTruthy();
    expect(text).toContain("Escherbridge, LLC");
    expect(text).toContain("Ahmed Zaher");
    expect(renderedOperatorLine(container)).toBe(OPERATOR_LINE);
    expect(OPERATOR_LINE).toBe(`${OPERATOR_LOCATION}. ${OPERATOR_EMAIL}`);
    expectNoPostalForm(text);
    expectNoPostalForm(String(contactMetadata.description));
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
    expect(renderedOperatorLine(container)).toBe(OPERATOR_LINE);
    expectNoPostalForm(text);
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
    expect(renderedOperatorLine(container)).toBe(OPERATOR_LINE);
    expectNoPostalForm(text);
    expect(text).toMatch(/privacy requests, and data requests/);
    expect(screen.getByRole("link", { name: "contact@escherbridge.com" }).getAttribute("href")).toBe(
      "mailto:contact@escherbridge.com"
    );
    expect(text.split(GOVERNING_LAW)).toHaveLength(2);
    expect(text).not.toMatch(/arbitration|class action|jury/i);
    expect(text).not.toMatch(/alerts@plantgeo\.io/);
    expect(text).not.toMatch(/DRAFT:/);
  });

  it("rejects a street suffix, a five-digit ZIP, or a post-office box on public pages", () => {
    for (const source of publicPageSources()) expectNoPostalForm(source);
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
  it("keeps the sister origins on the single discovery graph", () => {
    const { container } = render(<SiteDiscoveryJsonLd />);
    const scripts = container.querySelectorAll('script[type="application/ld+json"]');
    expect(scripts).toHaveLength(1);
    const data = JSON.parse(scripts[0]?.textContent ?? "{}") as {
      "@graph"?: { "@type"?: string; name?: string; url?: string; sameAs?: string[] }[];
    };
    const organization = data["@graph"]?.find((node) => node["@type"] === "Organization") as {
      "@type"?: string;
      name?: string;
      url?: string;
      sameAs?: string[];
      address?: Record<string, string>;
    };
    const website = data["@graph"]?.find((node) => node["@type"] === "WebSite");
    expect(organization?.name).toBe("Escherbridge, LLC");
    expect(organization?.address).toEqual({
      "@type": "PostalAddress",
      addressLocality: "Boise",
      addressRegion: "ID",
      addressCountry: "US",
    });
    expect(organization?.address).not.toHaveProperty("streetAddress");
    expect(organization?.address).not.toHaveProperty("postalCode");
    expect(organization?.url).toBe("https://plantgeo.aevani.com");
    expect(organization?.sameAs).toEqual(SISTER_SITES.map((site) => site.href));
    expect(website?.name).toBe("PlantGeo");
    expect(data).toEqual(siteDiscoveryGraph);
  });

  it("is mounted once from the root layout", () => {
    const source = readFileSync("src/app/layout.tsx", "utf8");
    expect(source).toContain("<SiteDiscoveryJsonLd");
    expect(source).not.toContain("OrganizationJsonLd");
  });
});
