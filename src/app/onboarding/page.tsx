"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Building2, Loader2, UserPlus } from "lucide-react";
import { trpc } from "@/lib/trpc/client";
import { CreateOrganizationForm } from "@/components/onboarding/CreateOrganizationForm";
import { JoinOrganizationForm } from "@/components/onboarding/JoinOrganizationForm";

type Screen = "choose" | "create" | "join";

function LoadingNotice({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center gap-3 py-6 text-center">
      <Loader2 className="h-5 w-5 animate-spin text-emerald-500" />
      <p className="text-sm text-zinc-400">{message}</p>
    </div>
  );
}

/** Post-registration fork: create an organization, join one, or skip for now. */
export default function OnboardingPage() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col items-center gap-2 border-b border-zinc-800 pb-5 text-center">
        <Link
          href="/dashboard"
          className="rounded-md border border-emerald-500/50 px-4 py-2 text-sm font-medium text-emerald-300 transition-colors hover:bg-emerald-500/10"
        >
          Continue individually
        </Link>
        <p className="text-xs text-zinc-400">Organization setup is optional. You can return to it later.</p>
      </div>
      <Suspense fallback={<LoadingNotice message="Loading…" />}>
        <OnboardingView />
      </Suspense>
    </div>
  );
}

/** Resolve organization setup intent; see components/onboarding/AGENTS.md. */
function OnboardingView() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const creatingAdditionalOrganization = searchParams.get("create") === "1";
  const [screen, setScreen] = useState<Screen>(
    creatingAdditionalOrganization ? "create" : "choose"
  );
  const { data: myTeams, isLoading, isError, error, refetch } = trpc.teams.listMyTeams.useQuery();
  const belongsToOrganization = Boolean(myTeams && myTeams.length > 0);

  useEffect(() => {
    if (belongsToOrganization && !creatingAdditionalOrganization) {
      router.replace("/dashboard/org");
    }
  }, [belongsToOrganization, creatingAdditionalOrganization, router]);

  if (isLoading) {
    return <LoadingNotice message="Checking your organizations…" />;
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center gap-3 py-6 text-center">
        <p className="text-sm text-red-400">{error.message}</p>
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

  if (belongsToOrganization && !creatingAdditionalOrganization) {
    // Redirect is in flight; avoid flashing the chooser.
    return <LoadingNotice message="Taking you to your organization…" />;
  }

  if (screen === "create") {
    return <CreateOrganizationForm onCancel={() => setScreen("choose")} />;
  }

  if (screen === "join") {
    return <JoinOrganizationForm onCancel={() => setScreen("choose")} />;
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="text-center">
        <h2 className="text-xl text-zinc-100 [font-family:var(--font-onboarding-display)]">
          Set up your workspace
        </h2>
        <p className="mt-1 text-xs text-zinc-500">
          Organizations group members, layers, and permissions on PlantGeo.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <button
          type="button"
          onClick={() => setScreen("create")}
          className="group flex flex-col items-start gap-3 rounded-lg border border-zinc-800 bg-zinc-950/40 p-5 text-left transition-all hover:border-emerald-500/40 hover:bg-emerald-500/4"
        >
          <span className="flex h-9 w-9 items-center justify-center rounded-md border border-zinc-700 bg-zinc-900 text-emerald-400 transition-colors group-hover:border-emerald-500/40">
            <Building2 className="h-4 w-4" />
          </span>
          <span>
            <span className="block text-sm font-medium text-zinc-100">Create an organization</span>
            <span className="mt-0.5 block text-xs leading-snug text-zinc-500">
              Start fresh and invite your team afterward.
            </span>
          </span>
        </button>

        <button
          type="button"
          onClick={() => setScreen("join")}
          className="group flex flex-col items-start gap-3 rounded-lg border border-zinc-800 bg-zinc-950/40 p-5 text-left transition-all hover:border-emerald-500/40 hover:bg-emerald-500/4"
        >
          <span className="flex h-9 w-9 items-center justify-center rounded-md border border-zinc-700 bg-zinc-900 text-emerald-400 transition-colors group-hover:border-emerald-500/40">
            <UserPlus className="h-4 w-4" />
          </span>
          <span>
            <span className="block text-sm font-medium text-zinc-100">Join an organization</span>
            <span className="mt-0.5 block text-xs leading-snug text-zinc-500">
              Use an invite link or a join code from your team.
            </span>
          </span>
        </button>
      </div>

    </div>
  );
}
