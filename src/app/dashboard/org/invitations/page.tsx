"use client";

import { useState } from "react";
import { Check, Copy, Link2, Loader2, Mail, RefreshCw, Send, Trash2, UserPlus, X } from "lucide-react";
import { trpc } from "@/lib/trpc/client";
import { toast } from "@/components/ui/toast";
import { OneTimeCodeReveal } from "@/components/onboarding/OneTimeCodeReveal";
import { useActiveOrganization } from "../useActiveOrganization";

/** Roles an invitation or join link may grant; owner is never delegable this way. */
type DelegableRole = "member" | "viewer";

const inputClass =
  "rounded-md bg-zinc-800 border border-zinc-700 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:ring-1 focus:ring-emerald-500";

function formatDate(value: string | Date | null | undefined): string {
  if (!value) return "—";
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString();
}

function toDelegableRole(role: string | null | undefined): DelegableRole {
  return role === "member" ? "member" : "viewer";
}

function CopyLinkButton({ value, label = "Copy link" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1800);
        } catch {
          // Clipboard permission denied — nothing more we can do here.
        }
      }}
      className="flex items-center gap-1 rounded px-2 py-1 text-xs text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-200"
    >
      {copied ? <Check className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
      {copied ? "Copied" : label}
    </button>
  );
}

/** Email invitations and shareable join links for the active organization. */
export default function OrganizationInvitationsPage() {
  const { organization, role, isLoading: orgLoading } = useActiveOrganization();
  const utils = trpc.useUtils();
  const teamId = organization?.id ?? "";

  const invitationsQuery = trpc.teams.listInvitations.useQuery({ teamId }, { enabled: Boolean(teamId) });
  const joinLinksQuery = trpc.teams.listJoinLinks.useQuery({ teamId }, { enabled: Boolean(teamId) });

  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<DelegableRole>("viewer");
  const [joinLinkRole, setJoinLinkRole] = useState<DelegableRole>("viewer");
  const [revealedCode, setRevealedCode] = useState<{ code: string; joinUrl: string } | null>(null);
  // createInvitation returns the accept link only at issue time — that's the
  // only moment we can offer a "copy link" affordance for it.
  const [lastSentInvite, setLastSentInvite] = useState<{ email: string; acceptUrl: string } | null>(null);

  const createInvitationMutation = trpc.teams.createInvitation.useMutation({
    onSuccess: (result) => {
      // `acceptUrl` is empty unless the call asked for it; storing the empty string would
      // render a copy button over nothing.
      setLastSentInvite(result.acceptUrl ? { email: result.email, acceptUrl: result.acceptUrl } : null);
      utils.teams.listInvitations.invalidate({ teamId });
    },
  });
  const revokeInvitationMutation = trpc.teams.revokeInvitation.useMutation({
    onSuccess: () => utils.teams.listInvitations.invalidate({ teamId }),
  });
  const createJoinLinkMutation = trpc.teams.createJoinLink.useMutation({
    onSuccess: (result) => {
      setRevealedCode({ code: result.code, joinUrl: result.joinUrl });
      utils.teams.listJoinLinks.invalidate({ teamId });
    },
  });
  const rotateJoinLinkMutation = trpc.teams.rotateJoinLink.useMutation({
    onSuccess: (result) => {
      setRevealedCode({ code: result.code, joinUrl: result.joinUrl });
      utils.teams.listJoinLinks.invalidate({ teamId });
    },
  });
  const revokeJoinLinkMutation = trpc.teams.revokeJoinLink.useMutation({
    onSuccess: () => utils.teams.listJoinLinks.invalidate({ teamId }),
  });

  if (orgLoading) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-center">
        <Loader2 className="h-5 w-5 animate-spin text-emerald-500" />
        <p className="text-sm text-zinc-400">Loading…</p>
      </div>
    );
  }

  const canInviteMember = role === "owner";
  const availableRoles: DelegableRole[] = canInviteMember ? ["member", "viewer"] : ["viewer"];

  async function handleInvite(e: React.FormEvent) {
    e.preventDefault();
    if (!inviteEmail.trim() || !teamId) return;
    try {
      await createInvitationMutation.mutateAsync({ teamId, email: inviteEmail.trim(), teamRole: inviteRole, returnLink: true });
      setInviteEmail("");
      toast.success("Invitation sent");
    } catch (err) {
      toast.error("Couldn't send invitation", { description: (err as Error).message });
    }
  }

  /** Re-issuing an invite to the same email replaces the prior one and returns a fresh link. */
  async function handleResend(email: string, teamRole: string | null) {
    if (!teamId) return;
    try {
      await createInvitationMutation.mutateAsync({ teamId, email, teamRole: toDelegableRole(teamRole), returnLink: true });
      toast.success("Invitation resent");
    } catch (err) {
      toast.error("Couldn't resend invitation", { description: (err as Error).message });
    }
  }

  async function handleCreateJoinLink() {
    if (!teamId) return;
    try {
      await createJoinLinkMutation.mutateAsync({ teamId, teamRole: joinLinkRole });
    } catch (err) {
      toast.error("Couldn't create join link", { description: (err as Error).message });
    }
  }

  return (
    <div className="flex flex-col gap-8">
      {revealedCode && (
        <OneTimeCodeReveal
          code={revealedCode.code}
          joinUrl={revealedCode.joinUrl}
          onDismiss={() => setRevealedCode(null)}
        />
      )}

      <section className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <Mail className="h-4 w-4 text-emerald-400" />
          <h2 className="text-lg text-zinc-100 [font-family:var(--font-onboarding-display,inherit)]">
            Invite by email
          </h2>
        </div>

        <form onSubmit={handleInvite} className="flex flex-wrap items-center gap-2">
          <input
            type="email"
            required
            placeholder="teammate@example.org"
            value={inviteEmail}
            onChange={(e) => setInviteEmail(e.target.value)}
            className={`${inputClass} min-w-56 flex-1`}
          />
          <select
            value={inviteRole}
            onChange={(e) => setInviteRole(e.target.value as DelegableRole)}
            className="rounded-md border border-zinc-700 bg-zinc-800 px-2 py-2 text-sm capitalize text-zinc-100"
          >
            {availableRoles.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          <button
            type="submit"
            disabled={createInvitationMutation.isPending || !inviteEmail.trim()}
            className="flex items-center gap-1.5 rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-40"
          >
            {createInvitationMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <UserPlus className="h-3.5 w-3.5" />
            )}
            Send invite
          </button>
        </form>

        {lastSentInvite && (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-emerald-500/20 bg-emerald-500/4 px-3 py-2">
            <p className="text-xs text-zinc-400">
              Sent to <span className="text-zinc-200">{lastSentInvite.email}</span>
            </p>
            <div className="flex items-center gap-2">
              <CopyLinkButton value={lastSentInvite.acceptUrl} />
              <button
                type="button"
                onClick={() => setLastSentInvite(null)}
                className="rounded p-1 text-zinc-500 transition-colors hover:text-zinc-300"
                aria-label="Dismiss"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        )}

        <div className="rounded-lg border border-zinc-800">
          {invitationsQuery.isLoading ? (
            <p className="px-4 py-6 text-center text-sm text-zinc-500">Loading invitations…</p>
          ) : invitationsQuery.isError ? (
            <p className="px-4 py-6 text-center text-sm text-red-400">{invitationsQuery.error.message}</p>
          ) : (invitationsQuery.data?.length ?? 0) === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-zinc-500">No pending invitations.</p>
          ) : (
            <ul className="divide-y divide-zinc-900">
              {invitationsQuery.data?.map((invitation) => (
                <li
                  key={invitation.id}
                  className="flex flex-wrap items-center justify-between gap-2 bg-zinc-900/20 px-4 py-2.5"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm text-zinc-200">{invitation.email}</p>
                    <p className="text-xs text-zinc-500">
                      <span className="capitalize">{invitation.teamRole}</span> &middot; expires{" "}
                      {formatDate(invitation.expiresAt)}
                    </p>
                  </div>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => handleResend(invitation.email, invitation.teamRole)}
                      disabled={createInvitationMutation.isPending}
                      className="flex items-center gap-1 rounded px-2 py-1 text-xs text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-200"
                      title="Resend and get a fresh link"
                    >
                      <Send className="h-3 w-3" />
                      Resend
                    </button>
                    <button
                      type="button"
                      onClick={() => revokeInvitationMutation.mutate({ invitationId: invitation.id })}
                      disabled={revokeInvitationMutation.isPending}
                      className="rounded p-1.5 text-zinc-500 transition-colors hover:bg-red-500/10 hover:text-red-400"
                      title="Revoke invitation"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <Link2 className="h-4 w-4 text-emerald-400" />
          <h2 className="text-lg text-zinc-100 [font-family:var(--font-onboarding-display,inherit)]">
            Join links
          </h2>
        </div>
        <p className="text-xs text-zinc-500">
          Anyone with a join code can join at the role you set &mdash; share it only with people you trust.
        </p>

        <div className="flex flex-wrap items-center gap-2">
          <select
            value={joinLinkRole}
            onChange={(e) => setJoinLinkRole(e.target.value as DelegableRole)}
            className="rounded-md border border-zinc-700 bg-zinc-800 px-2 py-2 text-sm capitalize text-zinc-100"
          >
            {availableRoles.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={handleCreateJoinLink}
            disabled={createJoinLinkMutation.isPending}
            className="flex items-center gap-1.5 rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:opacity-40"
          >
            {createJoinLinkMutation.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            New join link
          </button>
        </div>

        <div className="rounded-lg border border-zinc-800">
          {joinLinksQuery.isLoading ? (
            <p className="px-4 py-6 text-center text-sm text-zinc-500">Loading join links…</p>
          ) : joinLinksQuery.isError ? (
            <p className="px-4 py-6 text-center text-sm text-red-400">{joinLinksQuery.error.message}</p>
          ) : (joinLinksQuery.data?.length ?? 0) === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-zinc-500">No join links yet.</p>
          ) : (
            <ul className="divide-y divide-zinc-900">
              {joinLinksQuery.data?.map((link) => (
                <li
                  key={link.id}
                  className="flex flex-wrap items-center justify-between gap-2 bg-zinc-900/20 px-4 py-2.5"
                >
                  <div className="min-w-0">
                    <p className="text-sm capitalize text-zinc-200">{link.teamRole} access</p>
                    <p className="text-xs text-zinc-500">
                      {link.allowedEmailDomain && <>@{link.allowedEmailDomain} only &middot; </>}
                      {typeof link.maxUses === "number" && (
                        <>
                          {link.useCount}/{link.maxUses} used &middot;{" "}
                        </>
                      )}
                      {link.expiresAt ? `expires ${formatDate(link.expiresAt)}` : "no expiration"}
                      {link.revokedAt && <span className="text-red-400"> &middot; revoked</span>}
                    </p>
                  </div>
                  {!link.revokedAt && (
                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() => rotateJoinLinkMutation.mutate({ linkId: link.id })}
                        disabled={rotateJoinLinkMutation.isPending}
                        className="flex items-center gap-1 rounded px-2 py-1 text-xs text-zinc-400 transition-colors hover:bg-zinc-800 hover:text-zinc-200"
                        title="Rotate code"
                      >
                        <RefreshCw className="h-3.5 w-3.5" />
                        Rotate
                      </button>
                      <button
                        type="button"
                        onClick={() => revokeJoinLinkMutation.mutate({ linkId: link.id })}
                        disabled={revokeJoinLinkMutation.isPending}
                        className="rounded p-1.5 text-zinc-500 transition-colors hover:bg-red-500/10 hover:text-red-400"
                        title="Revoke join link"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    </div>
  );
}
