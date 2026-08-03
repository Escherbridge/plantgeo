"use client";

import Link from "next/link";
import { BadgeCheck, Globe, Loader2, Settings, UserPlus, Users } from "lucide-react";
import { trpc } from "@/lib/trpc/client";
import { useActiveOrganization } from "./useActiveOrganization";

const ORG_TYPE_LABEL: Record<string, string> = {
  nonprofit: "Nonprofit",
  cooperative: "Cooperative",
  business: "Business",
  individual: "Individual",
  government: "Government",
};

/** Organization overview: profile summary, verification, and quick actions. */
export default function OrganizationOverviewPage() {
  const { organization, role, isLoading, isError, error, refetch } = useActiveOrganization();
  const membersQuery = trpc.teams.listMembers.useQuery(
    { teamId: organization?.id ?? "" },
    { enabled: Boolean(organization?.id) }
  );

  if (isLoading) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-center">
        <Loader2 className="h-5 w-5 animate-spin text-emerald-500" />
        <p className="text-sm text-zinc-400">Loading organization…</p>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-center">
        <p className="text-sm text-red-400">{error?.message}</p>
        <button
          type="button"
          onClick={() => refetch()}
          className="rounded-md bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:bg-zinc-700"
        >
          Try again
        </button>
      </div>
    );
  }

  if (!organization) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-center">
        <p className="text-sm text-zinc-400">You&rsquo;re not part of an organization yet.</p>
        <Link
          href="/onboarding"
          className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500"
        >
          Set up an organization
        </Link>
      </div>
    );
  }

  const canManage = role === "owner" || role === "member";
  const memberCount = membersQuery.data?.length;

  return (
    <div className="flex flex-col gap-6">
      <div className="rounded-xl border border-zinc-800 bg-zinc-900/50 p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="truncate text-2xl italic text-zinc-50 [font-family:var(--font-onboarding-display,inherit)]">
                {organization.name}
              </h1>
              {organization.isVerified && (
                <span
                  title="Verified organization"
                  className="flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-emerald-400"
                >
                  <BadgeCheck className="h-3 w-3" />
                  Verified
                </span>
              )}
            </div>
            {organization.orgType && (
              <p className="mt-1 text-xs uppercase tracking-[0.2em] text-zinc-500">
                {ORG_TYPE_LABEL[organization.orgType] ?? organization.orgType}
              </p>
            )}
            {organization.description && (
              <p className="mt-3 max-w-xl text-sm leading-relaxed text-zinc-400">
                {organization.description}
              </p>
            )}
            {organization.website && (
              <a
                href={organization.website}
                target="_blank"
                rel="noreferrer"
                className="mt-3 inline-flex items-center gap-1.5 text-xs text-emerald-400 transition-colors hover:text-emerald-300"
              >
                <Globe className="h-3.5 w-3.5" />
                {organization.website.replace(/^https?:\/\//, "")}
              </a>
            )}
            {organization.specialties && organization.specialties.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {organization.specialties.map((tag) => (
                  <span
                    key={tag}
                    className="rounded-full border border-zinc-700 bg-zinc-800 px-2 py-0.5 text-xs text-zinc-300"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            )}
          </div>

          <div className="flex flex-col items-end gap-1 text-right">
            <span className="rounded bg-zinc-800 px-2 py-0.5 text-xs font-medium capitalize text-zinc-200">
              You: {role}
            </span>
            <span className="flex items-center gap-1 text-xs text-zinc-500">
              <Users className="h-3.5 w-3.5" />
              {memberCount === undefined ? "…" : `${memberCount} member${memberCount === 1 ? "" : "s"}`}
            </span>
          </div>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Link
          href="/dashboard/org/members"
          className="flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/40 p-4 transition-colors hover:border-zinc-700 hover:bg-zinc-900"
        >
          <span className="flex h-9 w-9 items-center justify-center rounded-md border border-zinc-700 bg-zinc-900 text-emerald-400">
            <Users className="h-4 w-4" />
          </span>
          <span>
            <span className="block text-sm font-medium text-zinc-100">Manage members</span>
            <span className="block text-xs text-zinc-500">Roles, removal, and departures</span>
          </span>
        </Link>

        {canManage && (
          <Link
            href="/dashboard/org/invitations"
            className="flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/40 p-4 transition-colors hover:border-zinc-700 hover:bg-zinc-900"
          >
            <span className="flex h-9 w-9 items-center justify-center rounded-md border border-zinc-700 bg-zinc-900 text-emerald-400">
              <UserPlus className="h-4 w-4" />
            </span>
            <span>
              <span className="block text-sm font-medium text-zinc-100">Invite people</span>
              <span className="block text-xs text-zinc-500">Email invites and join links</span>
            </span>
          </Link>
        )}

        {canManage && (
          <Link
            href="/dashboard/org/settings"
            className="flex items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-900/40 p-4 transition-colors hover:border-zinc-700 hover:bg-zinc-900"
          >
            <span className="flex h-9 w-9 items-center justify-center rounded-md border border-zinc-700 bg-zinc-900 text-emerald-400">
              <Settings className="h-4 w-4" />
            </span>
            <span>
              <span className="block text-sm font-medium text-zinc-100">Organization settings</span>
              <span className="block text-xs text-zinc-500">Profile, type, and slug</span>
            </span>
          </Link>
        )}
      </div>
    </div>
  );
}
