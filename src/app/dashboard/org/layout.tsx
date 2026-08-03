"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { cn } from "@/lib/utils";
import { TeamSwitcher } from "@/components/teams/TeamSwitcher";
import { useActiveOrganization } from "./useActiveOrganization";

interface NavItem {
  href: string;
  label: string;
  exact: boolean;
  roles: readonly string[];
}

const NAV_ITEMS: NavItem[] = [
  { href: "/dashboard/org", label: "Overview", exact: true, roles: ["owner", "member", "viewer"] },
  { href: "/dashboard/org/members", label: "Members", exact: false, roles: ["owner", "member", "viewer"] },
  { href: "/dashboard/org/invitations", label: "Invitations", exact: false, roles: ["owner", "member"] },
  { href: "/dashboard/org/settings", label: "Settings", exact: false, roles: ["owner", "member"] },
];

/** Shared nav shell for organization management: overview, members, invitations, settings. */
export default function OrganizationLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { role } = useActiveOrganization();
  const visibleItems = NAV_ITEMS.filter((item) => !role || item.roles.includes(role));

  return (
    <div className="min-h-screen bg-zinc-950 [font-family:var(--font-onboarding-mono,inherit)]">
      <header className="border-b border-zinc-800 bg-zinc-900/60 px-6 py-4 backdrop-blur-xl">
        <div className="mx-auto flex max-w-4xl items-center justify-between">
          <Link
            href="/dashboard"
            className="flex items-center gap-1.5 text-sm text-zinc-500 transition-colors hover:text-zinc-300"
          >
            <ArrowLeft className="h-4 w-4" />
            Dashboard
          </Link>
          <TeamSwitcher />
        </div>

        <nav className="mx-auto mt-4 flex max-w-4xl gap-1">
          {visibleItems.map((item) => {
            const isActive = item.exact ? pathname === item.href : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-emerald-600/15 text-emerald-400"
                    : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
                )}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
      </header>

      <main className="mx-auto max-w-4xl px-6 py-8">{children}</main>
    </div>
  );
}
