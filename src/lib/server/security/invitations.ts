import { createHash } from "crypto";

// Pure invitation / join-link policy. No database access lives here so the
// state machine stays unit-testable; the router owns every query.

export type InvitationState = "pending" | "accepted" | "revoked" | "expired";

export type JoinLinkState = "active" | "revoked" | "expired" | "exhausted";

export type JoinLinkRejection =
  | "revoked"
  | "expired"
  | "exhausted"
  | "domain_mismatch"
  | "missing_email"
  | "unverified_email";

export interface InvitationRecord {
  expiresAt: Date | string | null;
  acceptedAt?: Date | string | null;
  revokedAt?: Date | string | null;
}

export interface JoinLinkRecord {
  expiresAt?: Date | string | null;
  revokedAt?: Date | string | null;
  maxUses?: number | null;
  useCount?: number | null;
  allowedEmailDomain?: string | null;
}

export type JoinLinkDecision =
  | { allowed: true }
  | { allowed: false; reason: JoinLinkRejection };

/**
 * The redeeming identity as read from the database — never from a session
 * claim. `emailVerified` is `users.email_verified IS NOT NULL`.
 */
export interface RedeemingIdentity {
  email: string | null;
  emailVerified: boolean;
}

function toTime(value: Date | string | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const time = value instanceof Date ? value.getTime() : Date.parse(value);
  return Number.isFinite(time) ? time : null;
}

/** Lowercased, trimmed email; the only form ever written to the database. */
export function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

/** Domain part of an email address, normalized, or null when malformed. */
export function emailDomain(email: string): string | null {
  const normalized = normalizeEmail(email);
  const at = normalized.lastIndexOf("@");
  if (at <= 0 || at === normalized.length - 1) return null;
  return normalized.slice(at + 1);
}

/** Accepts `acme.com` or `@acme.com`; returns null when unusable. */
export function normalizeEmailDomain(domain: string): string | null {
  const normalized = domain.trim().toLowerCase().replace(/^@/, "");
  if (!/^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/.test(normalized)) {
    return null;
  }
  return normalized;
}

/**
 * Case-insensitive equality between the invited address and the accepting
 * account's stored address. Never call this with a session/JWT claim.
 */
export function emailMatchesInvitation(
  invitedEmail: string | null | undefined,
  accountEmail: string | null | undefined
): boolean {
  if (!invitedEmail || !accountEmail) return false;
  return normalizeEmail(invitedEmail) === normalizeEmail(accountEmail);
}

/**
 * Terminal states win over time-based ones: an invitation that was accepted
 * stays `accepted` after its expiry, and revocation beats a pending expiry.
 */
export function invitationState(
  invitation: InvitationRecord,
  now: Date = new Date()
): InvitationState {
  if (toTime(invitation.acceptedAt) !== null) return "accepted";
  if (toTime(invitation.revokedAt) !== null) return "revoked";
  const expiresAt = toTime(invitation.expiresAt);
  if (expiresAt === null || expiresAt <= now.getTime()) return "expired";
  return "pending";
}

/** Only a pending invitation may be accepted. */
export function isInvitationRedeemable(
  invitation: InvitationRecord,
  now: Date = new Date()
): boolean {
  return invitationState(invitation, now) === "pending";
}

/** Legal edges of the invitation state machine. */
export function canTransitionInvitation(
  from: InvitationState,
  to: Exclude<InvitationState, "pending">
): boolean {
  if (from !== "pending") return false;
  return to === "accepted" || to === "revoked" || to === "expired";
}

/** Remaining redemptions, or null when the link is unlimited. */
export function remainingJoinLinkUses(link: JoinLinkRecord): number | null {
  if (link.maxUses === null || link.maxUses === undefined) return null;
  return Math.max(0, link.maxUses - (link.useCount ?? 0));
}

export function joinLinkState(
  link: JoinLinkRecord,
  now: Date = new Date()
): JoinLinkState {
  if (toTime(link.revokedAt) !== null) return "revoked";
  const expiresAt = toTime(link.expiresAt);
  if (expiresAt !== null && expiresAt <= now.getTime()) return "expired";
  const remaining = remainingJoinLinkUses(link);
  if (remaining !== null && remaining <= 0) return "exhausted";
  return "active";
}

/**
 * Full redemption policy for a join link: revocation, expiry, use budget and
 * the optional email-domain allowlist. The router still performs the atomic
 * `use_count` claim, this only decides admissibility.
 *
 * A domain allowlist is admission control, so the address it is checked against
 * must be one the account actually proved it controls: an unverified address is
 * rejected outright. Links without an allowlist admit anyone holding the code,
 * so the address authorizes nothing there and verification is not required.
 */
export function canRedeemJoinLink(
  link: JoinLinkRecord,
  now: Date = new Date(),
  identity?: RedeemingIdentity | null
): JoinLinkDecision {
  const state = joinLinkState(link, now);
  if (state !== "active") return { allowed: false, reason: state };

  const allowedDomain = link.allowedEmailDomain
    ? normalizeEmailDomain(link.allowedEmailDomain)
    : null;
  if (allowedDomain) {
    if (!identity?.email) return { allowed: false, reason: "missing_email" };
    if (!identity.emailVerified) {
      return { allowed: false, reason: "unverified_email" };
    }
    const domain = emailDomain(identity.email);
    if (domain !== allowedDomain) {
      return { allowed: false, reason: "domain_mismatch" };
    }
  }
  return { allowed: true };
}

/** Shown whenever an unverified address is used as an authorization input. */
export const EMAIL_VERIFICATION_REQUIRED_MESSAGE =
  "Verify your email address before joining an organization";

/** Human-readable reason surfaced to the UI without leaking org details. */
export function joinLinkRejectionMessage(reason: JoinLinkRejection): string {
  switch (reason) {
    case "revoked":
      return "This join link has been revoked";
    case "expired":
      return "This join link has expired";
    case "exhausted":
      return "This join link has reached its maximum number of uses";
    case "domain_mismatch":
      return "This join link is restricted to a different email domain";
    case "missing_email":
      return "An email address is required to use this join link";
    case "unverified_email":
      return EMAIL_VERIFICATION_REQUIRED_MESSAGE;
  }
}

/** Human-readable reason for a non-pending invitation. */
export function invitationRejectionMessage(state: InvitationState): string {
  switch (state) {
    case "accepted":
      return "This invitation has already been used";
    case "revoked":
      return "This invitation has been revoked";
    case "expired":
      return "This invitation has expired";
    case "pending":
      return "This invitation is still pending";
  }
}

/** Invitations a single member may create per minute. */
export const INVITATIONS_PER_MINUTE = 10;

/** Token/code redemption attempts allowed per client address per minute. */
export const REDEMPTIONS_PER_MINUTE = 20;

/** Rate-limit identity for authenticated invitation creation. */
export function invitationRateLimitKey(userId: string): string {
  const digest = createHash("sha256").update(userId).digest("hex");
  return `org:invite:${digest}`;
}
