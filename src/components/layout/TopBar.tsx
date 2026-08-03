"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { signOut, useSession } from "next-auth/react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { cn } from "@/lib/utils";

const NAVIGATION_ITEMS = [
  { href: "/", label: "Map" },
  { href: "/community", label: "Community" },
  { href: "/about", label: "About" },
] as const;

const focusRing =
  "focus-visible:outline-2 focus-visible:outline-accent focus-visible:-outline-offset-2";

const labelType = "font-editorial-label text-label uppercase";

function isActiveRoute(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

function Wordmark() {
  return (
    <Link
      href="/"
      className={cn(
        "flex h-full shrink-0 items-center pr-comfortable pl-page-inset font-editorial-display text-[0.9375rem] leading-none font-black tracking-[-0.04em] text-ink uppercase",
        focusRing
      )}
    >
      <span>Plant</span>
      <span className="ml-hairline bg-accent px-hairline py-[0.15rem] text-accent-ink">
        Geo
      </span>
    </Link>
  );
}

function DesktopNavigation({ pathname }: { pathname: string }) {
  return (
    <nav aria-label="Primary" className="hidden h-full md:flex">
      {NAVIGATION_ITEMS.map((item) => {
        const active = isActiveRoute(pathname, item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex h-full items-center rule-left-hairline rule-top-heavy border-l-rule-faint px-comfortable transition-colors",
              labelType,
              active
                ? "border-t-accent text-ink"
                : "border-t-transparent text-ink-muted hover:text-ink",
              focusRing
            )}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}

function SignedOutActions({ compact }: { compact?: boolean }) {
  return (
    <Link
      href="/login"
      className={cn(
        "flex items-center bg-ink px-comfortable text-paper transition-opacity hover:opacity-85",
        labelType,
        compact ? "mx-page-inset h-11 justify-center" : "h-full",
        focusRing
      )}
    >
      Log in
    </Link>
  );
}

function SignedInActions({
  identity,
  compact,
}: {
  identity: string;
  compact?: boolean;
}) {
  const [signingOut, setSigningOut] = useState(false);

  async function handleSignOut() {
    if (signingOut) return;
    setSigningOut(true);
    await signOut({ callbackUrl: "/" });
  }

  return (
    <div className={cn("flex", compact ? "flex-col" : "h-full items-stretch")}>
      <span
        title={identity}
        className={cn(
          "flex items-center text-ink-muted",
          labelType,
          compact
            ? "px-page-inset pb-snug"
            : "hidden max-w-[18ch] truncate px-comfortable lg:flex"
        )}
      >
        {identity}
      </span>

      <Link
        href="/dashboard"
        className={cn(
          "flex items-center px-comfortable text-ink transition-colors hover:text-accent",
          labelType,
          compact
            ? "h-11 rule-top-hairline border-t-rule-faint px-page-inset"
            : "h-full rule-left-hairline border-l-rule-faint",
          focusRing
        )}
      >
        Dashboard
      </Link>

      <button
        type="button"
        onClick={handleSignOut}
        disabled={signingOut}
        className={cn(
          "flex items-center bg-ink px-comfortable text-paper transition-opacity hover:opacity-85 disabled:opacity-50",
          labelType,
          compact ? "mx-page-inset mt-snug h-11 justify-center" : "h-full",
          focusRing
        )}
      >
        {signingOut ? "Signing out" : "Sign out"}
      </button>
    </div>
  );
}

function AccountArea({ compact }: { compact?: boolean }) {
  const { data: session, status } = useSession();

  if (status === "loading") {
    return (
      <div
        aria-hidden
        className={cn(
          "bg-rule-faint",
          compact ? "mx-page-inset h-11" : "my-snug mr-page-inset w-24"
        )}
      />
    );
  }

  if (status !== "authenticated" || !session?.user) {
    return <SignedOutActions compact={compact} />;
  }

  return (
    <SignedInActions
      identity={session.user.name ?? session.user.email ?? "Signed in"}
      compact={compact}
    />
  );
}

/**
 * Global navigation. ApplicationShell decides which routes mount it; see
 * src/components/layout/AGENTS.md.
 */
export default function TopBar() {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  const closeMenu = useCallback(() => setMenuOpen(false), []);

  useEffect(() => {
    closeMenu();
  }, [pathname, closeMenu]);

  useEffect(() => {
    if (!menuOpen) return;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      toggleRef.current?.focus();
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [menuOpen]);

  return (
    <header className="relative z-40 flex h-[var(--application-top-bar-height)] items-stretch justify-between rule-bottom-heavy border-b-rule bg-paper">
      <div className="flex min-w-0 items-stretch">
        <Wordmark />
        <DesktopNavigation pathname={pathname} />
      </div>

      <div className="hidden items-stretch md:flex">
        <AccountArea />
      </div>

      <button
        ref={toggleRef}
        type="button"
        aria-expanded={menuOpen}
        aria-controls={menuId}
        onClick={() => setMenuOpen((open) => !open)}
        className={cn(
          "flex items-center rule-left-hairline border-l-rule-faint px-page-inset text-ink md:hidden",
          labelType,
          focusRing
        )}
      >
        {menuOpen ? "Close" : "Menu"}
      </button>

      <div
        id={menuId}
        hidden={!menuOpen}
        className="absolute inset-x-0 top-full rule-bottom-massive border-b-rule bg-paper md:hidden"
      >
        <nav aria-label="Primary" className="flex flex-col">
          {NAVIGATION_ITEMS.map((item) => {
            const active = isActiveRoute(pathname, item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center justify-between rule-top-hairline border-t-rule-faint px-page-inset py-comfortable",
                  labelType,
                  active ? "text-ink" : "text-ink-muted",
                  focusRing
                )}
              >
                {item.label}
                {active && (
                  <span aria-hidden className="h-tight w-roomy bg-accent" />
                )}
              </Link>
            );
          })}
        </nav>

        <div className="rule-top-medium border-t-rule py-comfortable">
          <AccountArea compact />
        </div>
      </div>
    </header>
  );
}
