import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RegisterForm } from "@/components/auth/RegisterForm";
import { LoginForm } from "@/components/auth/LoginForm";
import { LoginView } from "@/app/(auth)/login/LoginView";

const mocks = vi.hoisted(() => ({ push: vi.fn(), signIn: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push }),
  useSearchParams: () => new URLSearchParams("registered=1"),
}));
vi.mock("next-auth/react", () => ({ signIn: mocks.signIn }));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(fetch).mockReset();
  mocks.signIn.mockReset();
});
afterEach(cleanup);

function registrationCredentials(email = "person@example.com", password = "valid password") {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
}

describe("registration form", () => {
  it("omits a blank optional name, normalizes email and preserves the callback", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response("{}", { status: 201 }));
    render(<RegisterForm callbackUrl="/invite/valid-token" />);
    registrationCredentials(" Person@Example.com ");
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/login?registered=1&callbackUrl=%2Finvite%2Fvalid-token"));
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual({ email: "person@example.com", password: "valid password" });
  });

  it("rejects malformed email before sending a request", () => {
    render(<RegisterForm />);
    registrationCredentials("not-an-email");
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(screen.getByRole("alert").textContent).toBe("Enter a valid email address.");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("rejects a password above bcrypt's byte limit before submitting", () => {
    render(<RegisterForm />);
    registrationCredentials("person@example.com", "é".repeat(37));
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(screen.getByRole("alert").textContent).toContain("Password is too long");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("reenables submission after a network failure and lets the user retry", async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(new Response("{}", { status: 201 }));
    render(<RegisterForm />);
    registrationCredentials();
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Check your connection"));
    const button = screen.getByRole("button", { name: "Create account" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/login?registered=1"));
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});

describe("sign-in after registration", () => {
  it("explains existing passwords without claiming email verification blocks sign-in", () => {
    render(<LoginView />);
    expect(screen.getByText(/its password has not changed/).textContent).toContain("Sign in with your account password");
    expect(screen.queryByText(/before signing in/)).toBeNull();
    expect(screen.getByRole("link", { name: "Forgot password?" }).getAttribute("href")).toBe("/forgot-password");
  });

  it("reenables sign-in after a network failure and preserves the callback on retry", async () => {
    mocks.signIn.mockRejectedValueOnce(new TypeError("Network failure"))
      .mockResolvedValueOnce({ ok: true, status: 200, error: null });
    render(<LoginForm callbackUrl="/dashboard" />);
    registrationCredentials();
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Check your connection"));
    const button = screen.getByRole("button", { name: "Sign in" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith("/dashboard"));
  });

  it("does not navigate when the provider returns no sign-in response", async () => {
    mocks.signIn.mockResolvedValue(undefined);
    render(<LoginForm />);
    registrationCredentials();
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("could not be completed"));
    expect(mocks.push).not.toHaveBeenCalled();
  });
});
