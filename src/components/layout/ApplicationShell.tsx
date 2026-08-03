"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import TopBar from "@/components/layout/TopBar";

/**
 * Routes that supply their own chrome or must stay frameless. Everything else
 * gets the global bar by default so new pages are navigable on arrival.
 */
const ROUTES_WITHOUT_GLOBAL_NAVIGATION = [
  "/login",
  "/register",
  "/forgot-password",
  "/reset-password",
  "/verify-email",
  "/onboarding",
  "/dashboard",
  "/invite",
  "/join",
  "/embed",
];

function hasGlobalNavigation(pathname: string): boolean {
  return !ROUTES_WITHOUT_GLOBAL_NAVIGATION.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`)
  );
}

export function ApplicationShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  if (!hasGlobalNavigation(pathname)) {
    return <div className="application-shell-without-top-bar">{children}</div>;
  }

  return (
    <>
      <a
        href="#application-content"
        className="sr-only font-editorial-label text-label uppercase focus:not-sr-only focus:absolute focus:top-0 focus:left-0 focus:z-50 focus:bg-ink focus:px-comfortable focus:py-snug focus:text-paper"
      >
        Skip to content
      </a>
      <TopBar />
      <div id="application-content" className="application-shell-with-top-bar">
        {children}
      </div>
    </>
  );
}
