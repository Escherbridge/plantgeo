"use client";

import Link from "next/link";
import { useSession } from "next-auth/react";
import { UserMenu } from "@/components/auth/UserMenu";

const DASHBOARD_SECTIONS = [
  {
    href: "/",
    title: "Explore the map",
    description: "View environmental layers, select a date and investigate a location.",
  },
  {
    href: "/dashboard/org",
    title: "Organization",
    description: "Profile, members, invitations and workspace settings.",
  },
  {
    href: "/dashboard/conversations",
    title: "AI Conversations",
    description: "Revisit your saved location analyses.",
  },
  {
    href: "/dashboard/analytics",
    title: "Analytics",
    description: "Published layer and feature counts across the platform.",
  },
] as const;

const AFFILIATE_SECTION = {
  href: "/dashboard/affiliate",
  title: "Your invite link",
  description: "Send this link to a landowner, a grower, a school, or a homestead.",
} as const;

const AFFILIATE_REVIEW_SECTION = {
  href: "/dashboard/admin/affiliates",
  title: "Affiliate applications",
  description: "Approve or decline applications. Approval issues a code.",
} as const;

/** Working account destinations; see AGENTS.md for deferred analytics widgets. */
export default function DashboardPage() {
  const { data: session } = useSession();
  const role = session?.user?.platformRole;
  const sections = [
    ...DASHBOARD_SECTIONS,
    ...(role === "affiliate" ? [AFFILIATE_SECTION] : []),
    ...(role === "admin" ? [AFFILIATE_REVIEW_SECTION] : []),
  ];

  return (
    <div className="viewport-below-top-bar bg-[hsl(var(--background))] flex flex-col">
      <header className="min-h-14 shrink-0 border-b border-[hsl(var(--border))] flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-4">
          <Link href="/" className="text-lg font-bold text-emerald-500">PlantGeo</Link>
          <span className="text-[hsl(var(--muted-foreground))] text-sm">/</span>
          <span className="text-sm text-[hsl(var(--foreground))]">Dashboard</span>
        </div>
        <UserMenu />
      </header>
      <main className="mx-auto min-h-0 w-full max-w-5xl flex-1 overflow-y-auto px-4 py-8 sm:px-6">
        <h1 className="text-xl font-semibold text-[hsl(var(--foreground))]">Your workspace</h1>
        <p className="mt-2 text-sm text-[hsl(var(--muted-foreground))]">
          Explore a location, revisit an analysis or manage your organization.
        </p>
        <nav aria-label="Dashboard sections" className="mt-6 grid gap-4 sm:grid-cols-3">
          {sections.map((section) => (
            <Link
              key={section.href}
              href={section.href}
              className="rounded-lg border border-[hsl(var(--border))] bg-[hsl(var(--card))] p-5 transition-colors hover:border-emerald-500/60"
            >
              <h2 className="text-sm font-semibold text-[hsl(var(--foreground))]">{section.title}</h2>
              <p className="mt-2 text-sm text-[hsl(var(--muted-foreground))]">{section.description}</p>
            </Link>
          ))}
        </nav>
      </main>
    </div>
  );
}
