import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import AuthLayout from "@/app/(auth)/layout";
import InviteLayout from "@/app/invite/layout";
import JoinLayout from "@/app/join/layout";
import { RegisterView } from "@/app/(auth)/register/RegisterView";

vi.mock("next/font/google", () => ({
  Fraunces: () => ({ variable: "font-display" }),
  IBM_Plex_Mono: () => ({ variable: "font-mono" }),
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn() }),
}));

function expectWayfinding() {
  const back = screen.getByRole("link", { name: "Back to the map" });
  expect(back.getAttribute("href")).toBe("/");
  const skip = screen.getByRole("link", { name: "Skip to the form" });
  expect(skip.getAttribute("href")).toMatch(/^#/);
}

describe("auth wayfinding", () => {
  it("gives login and register the site-plan leads", () => {
    render(<RegisterView />);
    expect(
      screen.getByText("Create an account to save site plans and come back to them."),
    ).toBeTruthy();
  });

  it("puts a skip link and a map link on the auth shell", () => {
    render(
      <AuthLayout>
        <p>Form</p>
      </AuthLayout>,
    );
    expectWayfinding();
    expect(screen.getByRole("link", { name: "Skip to the form" }).getAttribute("href")).toBe(
      "#auth-card",
    );
  });

  it("puts the same links on invite and join", () => {
    const invite = render(
      <InviteLayout>
        <p>Invite</p>
      </InviteLayout>,
    );
    expectWayfinding();
    invite.unmount();

    render(
      <JoinLayout>
        <p>Join</p>
      </JoinLayout>,
    );
    expectWayfinding();
    expect(screen.getByRole("link", { name: "Skip to the form" }).getAttribute("href")).toBe(
      "#console-card",
    );
  });
});
