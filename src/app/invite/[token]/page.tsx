"use client";

import { useParams, useRouter, useSearchParams } from "next/navigation";
import { signOut, useSession } from "next-auth/react";
import Link from "next/link";
import { AlertTriangle, Loader2 } from "lucide-react";
import { trpc } from "@/lib/trpc/client";
import { toast } from "@/components/ui/toast";
import { InvitePreviewCard, type InvitePreviewState } from "@/components/onboarding/InvitePreviewCard";

/** Public invitation landing page: preview, then sign-in/accept based on session state. */
export default function InviteTokenPage() {
  const { token } = useParams<{ token: string }>();
  const searchParams = useSearchParams();
  // Set by /api/invitations/[token] when the one-click email link couldn't
  // complete on its own; "expired"/"invalid" short-circuit straight to the
  // invalid state, "email_mismatch" is handled by the live session check below.
  const errorParam = searchParams.get("error");
  const linkAlreadyInvalid = errorParam === "expired" || errorParam === "invalid";
  const router = useRouter();
  const { data: session, status: sessionStatus, update: updateSession } = useSession();
  const utils = trpc.useUtils();

  const previewQuery = trpc.teams.previewInvitation.useQuery(
    { token },
    { enabled: Boolean(token) && !linkAlreadyInvalid, retry: false }
  );
  const acceptMutation = trpc.teams.acceptInvitation.useMutation();
  const setActiveMutation = trpc.teams.setActiveTeam.useMutation();

  const callbackPath = `/invite/${token}`;

  let state: InvitePreviewState = { status: "loading" };
  if (linkAlreadyInvalid) {
    state = {
      status: "invalid",
      message: "This invitation is no longer valid. It may have expired or already been used.",
    };
  } else if (previewQuery.isLoading) {
    state = { status: "loading" };
  } else if (previewQuery.isError) {
    state = { status: "error", message: previewQuery.error.message };
  } else if (previewQuery.data && !previewQuery.data.valid) {
    state = {
      status: "invalid",
      message: "This invitation is no longer valid. It may have expired or already been used.",
    };
  } else if (previewQuery.data?.valid) {
    state = {
      status: "valid",
      organizationName: previewQuery.data.orgName,
      role: previewQuery.data.role,
      email: previewQuery.data.email,
    };
  }

  const isAuthenticated = sessionStatus === "authenticated" && Boolean(session?.user);
  const sessionEmail = session?.user?.email?.toLowerCase();
  const invitedEmail = state.status === "valid" ? state.email?.toLowerCase() : undefined;
  const emailMatches = isAuthenticated && (!invitedEmail || sessionEmail === invitedEmail);

  async function handleAccept() {
    try {
      const result = await acceptMutation.mutateAsync({ token });
      await setActiveMutation.mutateAsync({ teamId: result.teamId });
      await updateSession({ activeTeamId: result.teamId });
      await utils.teams.listMyTeams.invalidate();
      toast.success("You're in", { description: "Welcome to the organization." });
      router.push("/dashboard/org");
    } catch {
      // Surfaced inline via mutation error state below.
    }
  }

  const accepting = acceptMutation.isPending || setActiveMutation.isPending;
  const acceptError = acceptMutation.error?.message ?? setActiveMutation.error?.message;

  return (
    <div className="flex flex-col gap-6">
      <div className="text-center">
        <h2 className="text-xl text-zinc-100 [font-family:var(--font-onboarding-display)]">
          You&rsquo;ve been invited
        </h2>
      </div>

      <InvitePreviewCard state={state}>
        {state.status === "valid" && sessionStatus !== "loading" && (
          <div className="flex flex-col gap-3">
            {!isAuthenticated && (
              <>
                <p className="text-xs text-zinc-500">Sign in or create an account to accept.</p>
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

            {isAuthenticated && emailMatches && (
              <>
                {acceptError && <p className="text-sm text-red-400">{acceptError}</p>}
                <button
                  type="button"
                  disabled={accepting}
                  onClick={handleAccept}
                  className="flex items-center justify-center gap-2 rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-40"
                >
                  {accepting && <Loader2 className="h-4 w-4 animate-spin" />}
                  {accepting ? "Joining…" : "Accept invitation"}
                </button>
              </>
            )}

            {isAuthenticated && !emailMatches && (
              <div className="flex flex-col gap-2 rounded-md border border-amber-500/30 bg-amber-500/6 px-3 py-2.5">
                <div className="flex items-start gap-2">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
                  <p className="text-xs leading-relaxed text-amber-200/90">
                    You&rsquo;re signed in as <span className="font-medium">{session?.user?.email}</span>, but
                    this invitation was sent to a different address.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() =>
                    signOut({ callbackUrl: `/login?callbackUrl=${encodeURIComponent(callbackPath)}` })
                  }
                  className="self-start rounded-md bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:bg-zinc-700"
                >
                  Sign out and switch accounts
                </button>
              </div>
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
