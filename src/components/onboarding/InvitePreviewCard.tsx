"use client";

import { AlertTriangle, Loader2, ShieldCheck, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

export type OrganizationRole = "owner" | "member" | "viewer";

export type InvitePreviewState =
  | { status: "loading" }
  | { status: "invalid"; message: string }
  | { status: "error"; message: string }
  | { status: "valid"; organizationName: string; role: OrganizationRole; email?: string };

const ROLE_LABEL: Record<OrganizationRole, string> = {
  owner: "Owner",
  member: "Member",
  viewer: "Viewer",
};

const ROLE_DESCRIPTION: Record<OrganizationRole, string> = {
  owner: "Full control, including billing and membership",
  member: "Can edit and manage most organization data",
  viewer: "Read-only access to organization data",
};

function initialsFor(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "?";
  return trimmed
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase())
    .join("");
}

interface InvitePreviewCardProps {
  state: InvitePreviewState;
  children?: React.ReactNode;
}

/** Renders the loading/invalid/error/valid states shared by invite and join-code landings. */
export function InvitePreviewCard({ state, children }: InvitePreviewCardProps) {
  if (state.status === "loading") {
    return (
      <div className="flex flex-col items-center gap-3 rounded-lg border border-zinc-800 bg-zinc-950/40 px-6 py-10 text-center">
        <Loader2 className="h-5 w-5 animate-spin text-emerald-500" />
        <p className="text-sm text-zinc-400">Checking this link…</p>
      </div>
    );
  }

  if (state.status === "invalid" || state.status === "error") {
    const Icon = state.status === "invalid" ? XCircle : AlertTriangle;
    return (
      <div className="flex flex-col items-center gap-3 rounded-lg border border-red-900/40 bg-red-950/20 px-6 py-10 text-center">
        <Icon className="h-6 w-6 text-red-400" />
        <p className="text-sm text-red-300">{state.message}</p>
      </div>
    );
  }

  const { organizationName, role, email } = state;

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-emerald-500/20 bg-emerald-500/4 p-5">
      <div className="flex items-center gap-3">
        <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-emerald-500/30 bg-emerald-500/10 text-sm font-semibold text-emerald-300 [font-family:var(--font-onboarding-display,inherit)]">
          {initialsFor(organizationName)}
        </div>
        <div className="min-w-0">
          <p className="truncate text-base font-medium text-zinc-50">{organizationName}</p>
          {email && <p className="truncate text-xs text-zinc-500">Reserved for {email}</p>}
        </div>
      </div>

      <div
        className={cn(
          "flex items-center gap-2 rounded-md border border-zinc-800 bg-zinc-950/60 px-3 py-2 text-xs",
          "text-zinc-400"
        )}
      >
        <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
        <span>
          Joining as <span className="font-medium text-zinc-200">{ROLE_LABEL[role]}</span> &mdash;{" "}
          {ROLE_DESCRIPTION[role]}
        </span>
      </div>

      {children}
    </div>
  );
}
