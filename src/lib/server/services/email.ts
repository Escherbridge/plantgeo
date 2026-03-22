/**
 * Email notification service.
 * Supports Resend and SendGrid via EMAIL_PROVIDER env var.
 * Gracefully degrades (log only) if no provider is configured.
 */

export interface AlertRecord {
  id: string;
  userId: string;
  alertType: string;
  severity: string;
  title: string;
  body: string | null;
  metadata: unknown;
  isRead: boolean | null;
  createdAt: Date | null;
}

const SEVERITY_COLORS: Record<string, string> = {
  critical: "#dc2626",
  warning: "#f59e0b",
  info: "#3b82f6",
};

const SEVERITY_LABELS: Record<string, string> = {
  critical: "CRITICAL",
  warning: "WARNING",
  info: "INFO",
};

function buildAlertHtml(alert: AlertRecord, appUrl: string): string {
  const color = SEVERITY_COLORS[alert.severity] ?? "#6b7280";
  const label = SEVERITY_LABELS[alert.severity] ?? alert.severity.toUpperCase();
  const viewUrl = `${appUrl}/?alert=${alert.id}`;
  const body = alert.body ?? "";
  const createdAt = alert.createdAt
    ? new Date(alert.createdAt).toLocaleString("en-US", { timeZoneName: "short" })
    : "";

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${alert.title}</title>
</head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="padding:32px 0;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
          <!-- Header -->
          <tr>
            <td style="background:${color};padding:16px 24px;">
              <span style="color:#ffffff;font-size:12px;font-weight:700;letter-spacing:1px;">${label} ALERT</span>
            </td>
          </tr>
          <!-- Body -->
          <tr>
            <td style="padding:24px;">
              <h2 style="margin:0 0 12px;color:#111827;font-size:18px;font-weight:600;">${escapeHtml(alert.title)}</h2>
              ${body ? `<p style="margin:0 0 16px;color:#374151;font-size:14px;line-height:1.6;">${escapeHtml(body)}</p>` : ""}
              ${createdAt ? `<p style="margin:0 0 24px;color:#9ca3af;font-size:12px;">${createdAt}</p>` : ""}
              <a href="${viewUrl}" style="display:inline-block;background:${color};color:#ffffff;text-decoration:none;padding:10px 20px;border-radius:6px;font-size:14px;font-weight:500;">View on PlantGeo Map</a>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="background:#f3f4f6;padding:16px 24px;border-top:1px solid #e5e7eb;">
              <p style="margin:0;color:#9ca3af;font-size:11px;">You are receiving this because you subscribed to environmental alerts on PlantGeo. To manage your preferences, visit your account settings.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>`;
}

function buildDigestHtml(alerts: AlertRecord[], appUrl: string): string {
  const rows = alerts
    .map((a) => {
      const color = SEVERITY_COLORS[a.severity] ?? "#6b7280";
      const label = SEVERITY_LABELS[a.severity] ?? a.severity.toUpperCase();
      const ts = a.createdAt
        ? new Date(a.createdAt).toLocaleString("en-US", { timeZoneName: "short" })
        : "";
      return `<tr>
        <td style="padding:12px 16px;border-bottom:1px solid #e5e7eb;">
          <span style="display:inline-block;background:${color};color:#fff;font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;letter-spacing:0.5px;margin-right:8px;">${label}</span>
          <strong style="color:#111827;font-size:13px;">${escapeHtml(a.title)}</strong>
          ${ts ? `<br/><span style="color:#9ca3af;font-size:11px;">${ts}</span>` : ""}
        </td>
      </tr>`;
    })
    .join("");

  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8" /><title>PlantGeo Daily Alert Digest</title></head>
<body style="margin:0;padding:0;background:#f9fafb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="padding:32px 0;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
          <tr>
            <td style="background:#1d4ed8;padding:16px 24px;">
              <span style="color:#ffffff;font-size:14px;font-weight:700;">PlantGeo Daily Alert Digest</span>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 24px 0;">
              <p style="margin:0;color:#374151;font-size:14px;">You have <strong>${alerts.length}</strong> environmental alert${alerts.length !== 1 ? "s" : ""} since your last digest.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 0 0;">
              <table width="100%" cellpadding="0" cellspacing="0">${rows}</table>
            </td>
          </tr>
          <tr>
            <td style="padding:16px 24px;">
              <a href="${appUrl}" style="display:inline-block;background:#1d4ed8;color:#ffffff;text-decoration:none;padding:10px 20px;border-radius:6px;font-size:14px;font-weight:500;">View All on PlantGeo</a>
            </td>
          </tr>
          <tr>
            <td style="background:#f3f4f6;padding:16px 24px;border-top:1px solid #e5e7eb;">
              <p style="margin:0;color:#9ca3af;font-size:11px;">Manage your alert preferences in account settings.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>`;
}

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function sendViaResend(to: string, subject: string, html: string): Promise<void> {
  const apiKey = process.env.RESEND_API_KEY;
  const from = process.env.EMAIL_FROM ?? "PlantGeo Alerts <alerts@plantgeo.io>";

  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ from, to, subject, html }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Resend API error ${res.status}: ${text}`);
  }
}

async function sendViaSendGrid(to: string, subject: string, html: string): Promise<void> {
  const apiKey = process.env.SENDGRID_API_KEY;
  const from = process.env.EMAIL_FROM ?? "alerts@plantgeo.io";

  const res = await fetch("https://api.sendgrid.com/v3/mail/send", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      personalizations: [{ to: [{ email: to }] }],
      from: { email: from },
      subject,
      content: [{ type: "text/html", value: html }],
    }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`SendGrid API error ${res.status}: ${text}`);
  }
}

async function sendEmail(to: string, subject: string, html: string): Promise<void> {
  const provider = process.env.EMAIL_PROVIDER;

  if (provider === "resend") {
    await sendViaResend(to, subject, html);
  } else if (provider === "sendgrid") {
    await sendViaSendGrid(to, subject, html);
  } else {
    // Graceful degradation — no provider configured
    console.log(`[email] No EMAIL_PROVIDER configured. Skipping email to ${to}: ${subject}`);
  }
}

/**
 * Send an immediate alert email for a single alert event.
 */
export async function sendAlertEmail(to: string, alert: AlertRecord): Promise<void> {
  const appUrl = process.env.NEXT_PUBLIC_APP_URL ?? "https://plantgeo.io";
  const html = buildAlertHtml(alert, appUrl);
  const subject = `[PlantGeo Alert] ${alert.title}`;
  await sendEmail(to, subject, html);
}

/**
 * Send a daily digest email for a list of unread alerts.
 */
export async function sendDigestEmail(to: string, alerts: AlertRecord[]): Promise<void> {
  if (alerts.length === 0) return;
  const appUrl = process.env.NEXT_PUBLIC_APP_URL ?? "https://plantgeo.io";
  const html = buildDigestHtml(alerts, appUrl);
  const subject = `PlantGeo Daily Alert Digest — ${alerts.length} alert${alerts.length !== 1 ? "s" : ""}`;
  await sendEmail(to, subject, html);
}
