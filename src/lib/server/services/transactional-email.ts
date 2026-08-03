/**
 * Transactional account emails: verification, password reset, organization invites.
 * Delivery reuses the provider plumbing in ./email; when no EMAIL_PROVIDER is
 * configured the action URL is logged so local development stays usable.
 */

import { sendEmail } from "@/lib/server/services/email";

const BRAND_COLOR = "#1d4ed8";

/** Absolute origin for links in outbound mail, without a trailing slash. */
export function appUrl(): string {
  const configured = process.env.NEXT_PUBLIC_APP_URL?.trim();
  const base = configured && configured.length > 0 ? configured : "https://plantgeo.io";
  return base.replace(/\/+$/, "");
}

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Shared single-action email shell, matching the alert templates in ./email. */
function buildActionHtml(params: {
  title: string;
  heading: string;
  intro: string;
  actionLabel: string;
  actionUrl: string;
  footnote: string;
}): string {
  const safeUrl = escapeHtml(params.actionUrl);

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${escapeHtml(params.title)}</title>
</head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="padding:32px 0;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
          <!-- Header -->
          <tr>
            <td style="background:${BRAND_COLOR};padding:16px 24px;">
              <span style="color:#ffffff;font-size:14px;font-weight:700;">PlantGeo</span>
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding:24px;">
              <h2 style="margin:0 0 12px;color:#111827;font-size:18px;font-weight:600;">${escapeHtml(params.heading)}</h2>
              <p style="margin:0 0 24px;color:#374151;font-size:14px;line-height:1.6;">${escapeHtml(params.intro)}</p>
              <a href="${safeUrl}" style="display:inline-block;background:${BRAND_COLOR};color:#ffffff;text-decoration:none;padding:10px 20px;border-radius:6px;font-size:14px;font-weight:500;">${escapeHtml(params.actionLabel)}</a>
              <p style="margin:24px 0 0;color:#6b7280;font-size:12px;line-height:1.6;">If the button does not work, paste this link into your browser:<br /><span style="color:#374151;word-break:break-all;">${safeUrl}</span></p>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="background:#f3f4f6;padding:16px 24px;border-top:1px solid #e5e7eb;">
              <p style="margin:0;color:#9ca3af;font-size:11px;">${escapeHtml(params.footnote)}</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>`;
}

/**
 * Without a provider ./email logs only the subject, which hides the one value a
 * developer needs. Log the action URL too so local flows can be completed.
 *
 * These URLs carry live single-use credentials, so this is a development-only
 * affordance: a production log drain must never become a token source. A
 * missing provider is a supported production state, so EMAIL_PROVIDER alone is
 * not a sufficient guard.
 */
function logActionUrl(kind: string, to: string, actionUrl: string): void {
  if (process.env.NODE_ENV === "production") return;
  if (process.env.EMAIL_PROVIDER) return;
  console.log(`[email] ${kind} link for ${to}: ${actionUrl}`);
}

export async function sendEmailVerification(
  to: string,
  verifyUrl: string
): Promise<void> {
  logActionUrl("email-verification", to, verifyUrl);
  const html = buildActionHtml({
    title: "Verify your PlantGeo email",
    heading: "Confirm your email address",
    intro:
      "Confirm this address to finish setting up your PlantGeo account. This link expires in 24 hours.",
    actionLabel: "Verify email",
    actionUrl: verifyUrl,
    footnote:
      "You received this because someone signed up for PlantGeo with this address. If that was not you, you can ignore this email.",
  });
  await sendEmail(to, "Verify your PlantGeo email address", html);
}

export async function sendPasswordReset(
  to: string,
  resetUrl: string
): Promise<void> {
  logActionUrl("password-reset", to, resetUrl);
  const html = buildActionHtml({
    title: "Reset your PlantGeo password",
    heading: "Reset your password",
    intro:
      "Choose a new password for your PlantGeo account. This link expires in 1 hour and can be used once.",
    actionLabel: "Reset password",
    actionUrl: resetUrl,
    footnote:
      "If you did not request a password reset, ignore this email; your password will stay unchanged.",
  });
  await sendEmail(to, "Reset your PlantGeo password", html);
}

/**
 * Sent when someone submits the registration form for an address that already
 * has an account. The registration response is identical either way, so this
 * email is the only place the collision is disclosed — and only to the address
 * owner, who is the one party entitled to know.
 */
export async function sendExistingAccountNotice(to: string): Promise<void> {
  const signInUrl = `${appUrl()}/login`;
  const html = buildActionHtml({
    title: "You already have a PlantGeo account",
    heading: "You already have an account",
    intro:
      "Someone tried to create a PlantGeo account with this email address, but one already exists. Sign in with your existing password, or reset it if you no longer remember it.",
    actionLabel: "Sign in",
    actionUrl: signInUrl,
    footnote: `If that was not you, no action is needed and nothing has changed. To reset your password, visit ${appUrl()}/forgot-password`,
  });
  await sendEmail(to, "You already have a PlantGeo account", html);
}

export async function sendOrgInvitation(
  to: string,
  params: {
    orgName: string;
    inviterName: string | null;
    acceptUrl: string;
    role: string;
  }
): Promise<void> {
  logActionUrl("organization-invitation", to, params.acceptUrl);
  const inviter = params.inviterName?.trim();
  const intro = inviter
    ? `${inviter} invited you to join ${params.orgName} on PlantGeo as a ${params.role}. This invitation expires in 7 days.`
    : `You have been invited to join ${params.orgName} on PlantGeo as a ${params.role}. This invitation expires in 7 days.`;

  const html = buildActionHtml({
    title: `Join ${params.orgName} on PlantGeo`,
    heading: `Join ${params.orgName}`,
    intro,
    actionLabel: "Accept invitation",
    actionUrl: params.acceptUrl,
    footnote:
      "If you do not recognize this organization, you can safely ignore this email.",
  });
  await sendEmail(to, `You are invited to join ${params.orgName} on PlantGeo`, html);
}
