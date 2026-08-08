"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { signOut, useSession } from "next-auth/react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  editorialFocusRing,
  editorialFocusRingInverse,
} from "@/components/ui/editorial";
import { cn } from "@/lib/utils";

const NAVIGATION_ITEMS = [
  { href: "/", label: "Map" },
  { href: "/feed", label: "Feed" },
  { href: "/community", label: "Community" },
  { href: "/about", label: "About" },
] as const;

/** Appended for expert/admin sessions only — mirrors expertProcedure at src/lib/server/trpc/init.ts. */
const MODERATION_NAV_ITEM = { href: "/moderation", label: "Moderation" } as const;
const MODERATION_ROLES = ["expert", "admin"];

type NavigationItem = { href: string; label: string };

/** Nav chrome steps up from `text-label`; see src/components/layout/AGENTS.md §contrast. */
const navType = "font-editorial-label text-nav uppercase";

/** Ink-filled controls carry the inverse ring — accent on ink is only 2.16:1. */
const solidControl = cn(
  "bg-ink text-paper transition-colors hover:bg-accent hover:text-accent-ink disabled:opacity-70",
  editorialFocusRingInverse
);

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
        editorialFocusRing
      )}
    >
      <span>Plant</span>
      <span className="ml-hairline bg-accent px-hairline py-[0.15rem] text-accent-ink">
        Geo
      </span>
    </Link>
  );
}

function DesktopNavigation({
  pathname,
  items,
}: {
  pathname: string;
  items: readonly NavigationItem[];
}) {
  return (
    <nav aria-label="Primary" className="hidden h-full md:flex">
      {items.map((item) => {
        const active = isActiveRoute(pathname, item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex h-full items-center rule-left-hairline rule-top-heavy border-l-rule-faint px-comfortable transition-colors",
              navType,
              // The accent rule carries the active state alongside `aria-current`,
              // so selection is never signalled by colour alone. Hover thickens
              // the same rule rather than only shifting text colour.
              active
                ? "border-t-accent text-ink"
                : "border-t-transparent text-ink-muted hover:border-t-rule-faint hover:text-ink",
              editorialFocusRing
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
        "flex items-center px-comfortable",
        navType,
        solidControl,
        compact ? "mx-page-inset h-11 justify-center" : "h-full",
        editorialFocusRingInverse
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
      {/* Truncation is CSS-only, so assistive tech still reads the full identity. */}
      <span
        title={identity}
        className={cn(
          "flex items-center text-ink-muted",
          navType,
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
          navType,
          compact
            ? "h-11 rule-top-hairline border-t-rule-faint px-page-inset"
            : "h-full rule-left-hairline border-l-rule-faint",
          editorialFocusRing
        )}
      >
        Dashboard
      </Link>

      {/* The org tree was reachable only from onboarding and invite-accept redirects: once a
          signed-in user navigated away, /dashboard/org could only be reached by typing the
          URL. Signed-in-only by construction, since SignedInActions renders for no one else. */}
      <Link
        href="/dashboard/org"
        className={cn(
          "flex items-center px-comfortable text-ink transition-colors hover:text-accent",
          navType,
          compact
            ? "h-11 rule-top-hairline border-t-rule-faint px-page-inset"
            : "h-full rule-left-hairline border-l-rule-faint",
          editorialFocusRing
        )}
      >
        Organization
      </Link>

      <button
        type="button"
        onClick={handleSignOut}
        disabled={signingOut}
        aria-busy={signingOut}
        className={cn(
          "flex items-center px-comfortable",
          navType,
          solidControl,
          compact ? "mx-page-inset mt-snug h-11 justify-center" : "h-full"
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
    // Sized to the resolved control so the bar does not reflow when the session
    // lands, and announced rather than left as a silent flash of empty chrome.
    return (
      <div
        role="status"
        className={cn(
          "flex items-center",
          compact ? "px-page-inset" : "mr-page-inset"
        )}
      >
        <span
          aria-hidden
          className={cn("bg-rule-faint", compact ? "h-11 w-full" : "h-6 w-28")}
        />
        <span className="sr-only">Checking your session</span>
      </div>
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
  const { data: session } = useSession();
  const [menuOpen, setMenuOpen] = useState(false);
  const headerRef = useRef<HTMLElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  const role = (session?.user as { platformRole?: string } | undefined)?.platformRole;
  const navigationItems: readonly NavigationItem[] =
    role && MODERATION_ROLES.includes(role)
      ? [...NAVIGATION_ITEMS, MODERATION_NAV_ITEM]
      : NAVIGATION_ITEMS;

  const closeMenu = useCallback(() => setMenuOpen(false), []);

  useEffect(() => {
    closeMenu();
  }, [pathname, closeMenu]);

  // Disclosure dismissal, per the APG disclosure pattern: Escape restores focus
  // to the toggle, while a pointer or a Tab that leaves the bar just closes it —
  // this is a disclosure, not a modal, so focus is never trapped inside it.
  useEffect(() => {
    if (!menuOpen) return;

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      toggleRef.current?.focus();
    }

    function handlePointerDown(event: PointerEvent) {
      const target = event.target;
      if (target instanceof Node && headerRef.current?.contains(target)) return;
      setMenuOpen(false);
    }

    function handleFocusIn(event: FocusEvent) {
      const target = event.target;
      if (target instanceof Node && headerRef.current?.contains(target)) return;
      setMenuOpen(false);
    }

    document.addEventListener("keydown", handleKeyDown);
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("focusin", handleFocusIn);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("focusin", handleFocusIn);
    };
  }, [menuOpen]);

  return (
    <header
      ref={headerRef}
      className="relative z-40 flex h-(--application-top-bar-height) items-stretch justify-between rule-bottom-heavy border-b-rule bg-paper"
    >
      <div className="flex min-w-0 items-stretch">
        <Wordmark />
        <DesktopNavigation pathname={pathname} items={navigationItems} />
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
          "flex items-center rule-left-hairline border-l-rule-faint px-page-inset text-ink transition-colors hover:text-accent md:hidden",
          navType,
          editorialFocusRing
        )}
      >
        {menuOpen ? "Close" : "Menu"}
      </button>

      {/* `hidden` is the HTML attribute, not the class: it takes the whole panel
          out of the tab order and the accessibility tree while collapsed. */}
      <div
        id={menuId}
        hidden={!menuOpen}
        className="absolute inset-x-0 top-full rule-bottom-massive border-b-rule bg-paper md:hidden"
      >
        <nav aria-label="Primary, compact" className="flex flex-col">
          {navigationItems.map((item) => {
            const active = isActiveRoute(pathname, item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex min-h-11 items-center justify-between rule-top-hairline border-t-rule-faint px-page-inset py-comfortable transition-colors",
                  navType,
                  active ? "text-ink" : "text-ink-muted hover:text-ink",
                  editorialFocusRing
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
