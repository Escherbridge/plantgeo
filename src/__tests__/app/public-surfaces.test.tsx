import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AffiliateApplicationForm } from "@/app/affiliate/AffiliateApplicationForm";
import { FeedIntro } from "@/app/feed/FeedIntro";
import { GET as llmsTxt } from "@/app/llms.txt/route";
import { SiteFooter } from "@/components/layout/SiteFooter";
import { LLMS_TXT } from "@/lib/llms-txt";

const session = vi.hoisted(() => ({ status: "unauthenticated" }));

vi.mock("next-auth/react", () => ({
  useSession: () => ({ data: null, status: session.status }),
}));

describe("llms.txt", () => {
  it("serves the published summary and does not advertise an OpenAPI file", async () => {
    const response = llmsTxt();
    expect(response.headers.get("content-type")).toContain("text/plain");
    const body = await response.text();
    expect(body).toBe(LLMS_TXT);
    expect(body).toContain("keys are issued by hand for now. Ask through /contact.");
    expect(body).toContain("https://plantgeo.aevani.com/plan/sample");
    expect(body).toContain("Boise, Idaho");
    expect(body).not.toContain("openapi.json");
    expect(body).not.toContain("streetAddress");
  });
});

describe("signed-out feed intro", () => {
  afterEach(() => {
    session.status = "unauthenticated";
  });

  it("does not promise entries that stay hidden until sign-in", () => {
    session.status = "unauthenticated";
    render(<FeedIntro />);
    expect(
      screen.getByText(
        "You'll see proposals awaiting review here once you sign in. Anyone with an account can read them.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/Every entry below/)).toBeNull();
  });

  it("describes the list once the reader is signed in", () => {
    session.status = "authenticated";
    render(<FeedIntro />);
    expect(screen.getByText(/Every entry below is a recommendation/)).toBeTruthy();
  });
});

describe("affiliate honeypot", () => {
  it("keeps the fax field out of the accessibility tree", () => {
    render(<AffiliateApplicationForm />);
    expect(screen.queryByRole("textbox", { name: /fax/i })).toBeNull();
    expect(screen.queryByText("Fax")).toBeNull();
    const fax = document.getElementById("affiliate-fax");
    expect(fax).toBeTruthy();
    expect(fax?.getAttribute("tabindex")).toBe("-1");
    expect(fax?.getAttribute("autocomplete")).toBe("off");
    expect(fax?.closest("[aria-hidden='true']")).toBeTruthy();
    expect(fax?.closest("[inert]")).toBeTruthy();
  });
});

describe("sister-site ticker", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("offers a pause control and clips the track", () => {
    render(<SiteFooter pathname="/about" />);
    const pause = screen.getByRole("button", { name: "Pause" });
    expect(pause.getAttribute("aria-pressed")).toBe("false");
    expect(document.querySelector(".sister-ticker-track")).toBeTruthy();
    const track = pause.parentElement;
    expect(track?.className).toContain("overflow-hidden");
    fireEvent.click(pause);
    expect(screen.getByRole("button", { name: "Play" }).getAttribute("aria-pressed")).toBe("true");
    expect(document.querySelector(".sister-ticker-track")).toBeNull();
  });

  it("starts paused when the reader prefers reduced motion", () => {
    vi.stubGlobal(
      "matchMedia",
      (query: string) => ({
        matches: query === "(prefers-reduced-motion: reduce)",
        addEventListener: () => {},
        removeEventListener: () => {},
      }),
    );
    render(<SiteFooter pathname="/about" />);
    expect(screen.getByRole("button", { name: "Play" }).getAttribute("aria-pressed")).toBe("true");
    expect(document.querySelector(".sister-ticker-track")).toBeNull();
  });
});
