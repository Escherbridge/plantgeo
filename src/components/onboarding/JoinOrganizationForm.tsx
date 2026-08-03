"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import { ArrowLeft, Loader2 } from "lucide-react";
import { trpc } from "@/lib/trpc/client";
import { toast } from "@/components/ui/toast";
import { cn } from "@/lib/utils";
import { InvitePreviewCard, type InvitePreviewState } from "./InvitePreviewCard";

const inputClass =
  "rounded-md bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500";

type Mode = "invite" | "code";

/** Pulls the trailing path segment out of a pasted URL, or returns the raw input. */
function extractIdentifier(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "";
  try {
    const url = new URL(trimmed);
    const segments = url.pathname.split("/").filter(Boolean);
    return segments[segments.length - 1] ?? trimmed;
  } catch {
    const segments = trimmed.split("/").filter(Boolean);
    return segments[segments.length - 1] ?? trimmed;
  }
}

interface JoinOrganizationFormProps {
  onCancel: () => void;
}

/** Preview-then-accept flow for both invitation links and join codes. */
export function JoinOrganizationForm({ onCancel }: JoinOrganizationFormProps) {
  const router = useRouter();
  const { update: updateSession } = useSession();
  const utils = trpc.useUtils();

  const [mode, setMode] = useState<Mode>("invite");
  const [rawInput, setRawInput] = useState("");
  const [submittedId, setSubmittedId] = useState<string | null>(null);

  const identifier = submittedId ?? "";

  const invitationQuery = trpc.teams.previewInvitation.useQuery(
    { token: identifier },
    { enabled: mode === "invite" && identifier.length > 0, retry: false }
  );
  const joinLinkQuery = trpc.teams.previewJoinLink.useQuery(
    { code: identifier },
    { enabled: mode === "code" && identifier.length > 0, retry: false }
  );

  const acceptInvitationMutation = trpc.teams.acceptInvitation.useMutation();
  const redeemJoinLinkMutation = trpc.teams.redeemJoinLink.useMutation();
  const setActiveMutation = trpc.teams.setActiveTeam.useMutation();

  const activeQuery = mode === "invite" ? invitationQuery : joinLinkQuery;

  function handlePreview(e: React.FormEvent) {
    e.preventDefault();
    const id = extractIdentifier(rawInput);
    if (!id) return;
    setSubmittedId(id);
  }

  function switchMode(next: Mode) {
    setMode(next);
    setSubmittedId(null);
    setRawInput("");
  }

  let previewState: InvitePreviewState = { status: "loading" };
  if (submittedId) {
    if (activeQuery.isLoading) {
      previewState = { status: "loading" };
    } else if (activeQuery.isError) {
      previewState = { status: "error", message: activeQuery.error.message };
    } else if (activeQuery.data && !activeQuery.data.valid) {
      previewState = {
        status: "invalid",
        message:
          mode === "invite"
            ? "This invitation is no longer valid. It may have expired or already been used."
            : "This join code is no longer valid. It may have expired, been revoked, or reached its use limit.",
      };
    } else if (activeQuery.data?.valid) {
      previewState = {
        status: "valid",
        organizationName: activeQuery.data.orgName,
        role: activeQuery.data.role,
        email: mode === "invite" ? invitationQuery.data?.email : undefined,
      };
    }
  }

  async function handleAccept() {
    try {
      const result =
        mode === "invite"
          ? await acceptInvitationMutation.mutateAsync({ token: identifier })
          : await redeemJoinLinkMutation.mutateAsync({ code: identifier });
      await setActiveMutation.mutateAsync({ teamId: result.teamId });
      await updateSession({ activeTeamId: result.teamId });
      await utils.teams.listMyTeams.invalidate();
      toast.success("You're in", { description: "Welcome to the organization." });
      router.push("/dashboard/org");
    } catch {
      // Surfaced inline via the mutation's error state below.
    }
  }

  const accepting =
    acceptInvitationMutation.isPending || redeemJoinLinkMutation.isPending || setActiveMutation.isPending;
  const acceptError =
    acceptInvitationMutation.error?.message ??
    redeemJoinLinkMutation.error?.message ??
    setActiveMutation.error?.message;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h2 className="text-xl text-zinc-100 [font-family:var(--font-onboarding-display)]">
          Join an organization
        </h2>
        <p className="mt-1 text-xs text-zinc-500">Paste what you were given below.</p>
      </div>

      <div className="inline-flex w-fit gap-1 rounded-md border border-zinc-800 bg-zinc-950/60 p-1">
        {(["invite", "code"] as Mode[]).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => switchMode(m)}
            className={cn(
              "rounded px-3 py-1.5 text-xs font-medium transition-colors",
              mode === m ? "bg-emerald-600 text-white" : "text-zinc-400 hover:text-zinc-200"
            )}
          >
            {m === "invite" ? "Invite link" : "Join code"}
          </button>
        ))}
      </div>

      {!submittedId && (
        <form onSubmit={handlePreview} className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-sm text-zinc-300" htmlFor="join-input">
              {mode === "invite" ? "Invitation link or token" : "Join code or link"}
            </label>
            <input
              id="join-input"
              type="text"
              autoFocus
              value={rawInput}
              onChange={(e) => setRawInput(e.target.value)}
              placeholder={mode === "invite" ? "https://plantgeo.app/invite/…" : "https://plantgeo.app/join/…"}
              className={inputClass}
            />
          </div>
          <button
            type="submit"
            disabled={!rawInput.trim()}
            className="self-start rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-40"
          >
            Preview
          </button>
        </form>
      )}

      {submittedId && (
        <div className="flex flex-col gap-4">
          <InvitePreviewCard state={previewState}>
            {previewState.status === "valid" && (
              <div className="flex flex-col gap-2">
                {acceptError && <p className="text-sm text-red-400">{acceptError}</p>}
                <button
                  type="button"
                  disabled={accepting}
                  onClick={handleAccept}
                  className="flex items-center justify-center gap-2 rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-40"
                >
                  {accepting && <Loader2 className="h-4 w-4 animate-spin" />}
                  {accepting ? "Joining…" : "Accept & continue"}
                </button>
              </div>
            )}
          </InvitePreviewCard>
          <button
            type="button"
            onClick={() => setSubmittedId(null)}
            className="self-start text-xs text-zinc-500 transition-colors hover:text-zinc-300"
          >
            Try a different link or code
          </button>
        </div>
      )}

      <button
        type="button"
        onClick={onCancel}
        className="flex items-center gap-1 self-start text-sm text-zinc-400 transition-colors hover:text-zinc-200"
      >
        <ArrowLeft className="h-4 w-4" />
        Back
      </button>
    </div>
  );
}
