import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import CommunityPage, { metadata } from "@/app/community/page";

describe("Community request publication disclosure", () => {
  it("discloses public publication in both metadata and the visible page", () => {
    const { container } = render(<CommunityPage />);
    const page = (container.textContent ?? "").replace(/\s+/g, " ");
    expect(metadata.description).toMatch(/location, title, and description on the public map immediately/i);
    expect(metadata.description).not.toMatch(/private|rather than published/i);
    expect(screen.getByRole("heading", { name: "Put a strategy request on the map." })).toBeTruthy();
    expect(page).toMatch(/location, title, and description on the public map immediately/i);
    expect(page).toMatch(/anyone can read a published request without signing in/i);
    expect(page).not.toMatch(/private by default|keeps the where to itself|stay with the account|a ledger, not a broadcast/i);
  });

  it("explains social access and keeps the submission and review destinations reachable", () => {
    const { container } = render(<CommunityPage />);
    const page = (container.textContent ?? "").replace(/\s+/g, " ");
    expect(page).toMatch(/sign in to read comments/i);
    expect(page).toMatch(/contributor access is required to post requests, add comments, or like a feature/i);
    expect(page).toMatch(/check the pin location, title, and description, then confirm that they can be published before posting/i);
    expect(page).toMatch(/submitted proposals can be visible to signed-in readers during review/i);
    expect(screen.getAllByRole("link", { name: "Open the map" }).every((link) => link.getAttribute("href") === "/")).toBe(true);
    expect(screen.getByRole("link", { name: "Proposals awaiting review" }).getAttribute("href")).toBe("/feed");
  });
});
