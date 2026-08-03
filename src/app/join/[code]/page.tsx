"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useSession } from "next-auth/react";
import Link from "next/link";
import { AlertTriangle, Loader2 } from "lucide-react";
import { trpc } from "@/lib/trpc/client";
import { toast } from "@/components/ui/toast";
import { InvitePreviewCard, type InvitePreviewState } from "@/components/onboarding/InvitePreviewCard";

/** Public join-code landing page: preview, then sign-in/redeem based on session state. */
export default function JoinCodePage() {
  const { code } = useParams<{ code: string }>();
  const searchParams = useSearchParams();
  // Set by /api/join/[code] when the one-click email/share link couldn't
  // complete on its own; "expired"/"invalid" short-circuit to the invalid
  // state, "not_allowed" (domain or missing-email restriction) is surfaced as
  // a banner above an otherwise-valid preview.
  const errorParam = searchParams.get("error");
  const linkAlreadyInvalid = errorParam === "expired" || errorParam === "invalid";
  const router = useRouter();
  const { data: session, status: sessionStatus, update: updateSession } = useSession();
  const utils = trpc.useUtils();

  const previewQuery = trpc.teams.previewJoinLink.useQuery(
    { code },
    { enabled: Boolean(code) && !linkAlreadyInvalid, retry: false }
  );
  const redeemMutation = trpc.teams.redeemJoinLink.useMutation();
  const setActiveMutation = trpc.teams.setActiveTeam.useMutation();

  const callbackPath = `/join/${code}`;

  let state: InvitePreviewState = { status: "loading" };
  if (linkAlreadyInvalid) {
    state = {
      status: "invalid",
      message: "This join code is no longer valid. It may have expired, been revoked, or reached its use limit.",
    };
  } else if (previewQuery.isLoading) {
    state = { status: "loading" };
  } else if (previewQuery.isError) {
    state = { status: "error", message: previewQuery.error.message };
  } else if (previewQuery.data && !previewQuery.data.valid) {
    state = {
      status: "invalid",
      message: "This join code is no longer valid. It may have expired, been revoked, or reached its use limit.",
    };
  } else if (previewQuery.data?.valid) {
    state = {
      status: "valid",
      organizationName: previewQuery.data.orgName,
      role: previewQuery.data.role,
    };
  }

  const isAuthenticated = sessionStatus === "authenticated" && Boolean(session?.user);

  async function handleJoin() {
    try {
      const result = await redeemMutation.mutateAsync({ code });
      await setActiveMutation.mutateAsync({ teamId: result.teamId });
      await updateSession({ activeTeamId: result.teamId });
      await utils.teams.listMyTeams.invalidate();
      toast.success("You're in", { description: "Welcome to the organization." });
      router.push("/dashboard/org");
    } catch {
      // Surfaced inline via mutation error state below.
    }
  }

  const joining = redeemMutation.isPending || setActiveMutation.isPending;
  const joinError = redeemMutation.error?.message ?? setActiveMutation.error?.message;

  return (
    <div className="flex flex-col gap-6">
      <div className="text-center">
        <h2 className="text-xl text-zinc-100 [font-family:var(--font-onboarding-display)]">
          Join with a code
        </h2>
      </div>

      <InvitePreviewCard state={state}>
        {state.status === "valid" && sessionStatus !== "loading" && (
          <div className="flex flex-col gap-3">
            {!isAuthenticated && (
              <>
                <p className="text-xs text-zinc-500">Sign in or create an account to join.</p>
                <div className="flex gap-2">
                  <Link
                    href={`/login?callbackUrl=${encodeURIComponent(callbackPath)}`}
                    className="flex-1 rounded-md bg-emerald-600 px-4 py-2 text-center text-sm font-medium text-white transition-colors hover:bg-emerald-500"
                  >
                    Sign in
                  </Link>
                  <Link
                    href={`/register?callbackUrl=${encodeURIComponent(callbackPath)}`}
                    className="flex-1 rounded-md border border-zinc-700 bg-zinc-800 px-4 py-2 text-center text-sm font-medium text-zinc-200 transition-colors hover:bg-zinc-700"
                  >
                    Create account
                  </Link>
                </div>
              </>
            )}

            {isAuthenticated && errorParam === "not_allowed" && (
              <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/6 px-3 py-2.5">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
                <p className="text-xs leading-relaxed text-amber-200/90">
                  This join link has restrictions your account doesn&rsquo;t meet (such as an email
                  domain requirement). You can still try joining below.
                </p>
              </div>
            )}

            {isAuthenticated && (
              <>
                {joinError && <p className="text-sm text-red-400">{joinError}</p>}
                <button
                  type="button"
                  disabled={joining}
                  onClick={handleJoin}
                  className="flex items-center justify-center gap-2 rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-40"
                >
                  {joining && <Loader2 className="h-4 w-4 animate-spin" />}
                  {joining ? "Joining…" : "Join organization"}
                </button>
              </>
            )}
          </div>
        )}
      </InvitePreviewCard>

      {(state.status === "invalid" || state.status === "error") && (
        <Link
          href="/dashboard"
          className="self-center text-xs text-zinc-500 transition-colors hover:text-zinc-300"
        >
          Go to dashboard
        </Link>
      )}
    </div>
  );
}
