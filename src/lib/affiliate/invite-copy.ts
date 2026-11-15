import { normalizeRefCode } from "@/lib/plans/ref-code";
import { SITE_ORIGIN } from "@/lib/public-site";

export const AFFILIATE_DASHBOARD_HEADING = "Your invite link";

export const AFFILIATE_DASHBOARD_BODY =
  "Send this link to a landowner, a grower, a school, or a homestead. When they start a plan from it, it is counted on this page. A referral share has not been set, so this page does not show a rate or a payout.";

export const AFFILIATE_COPY_LINK_LABEL = "Copy link";

export const AFFILIATE_COPY_LINK_STATUS = "Link copied.";

/** The recorded name, or the organization when the name is blank. Never a made-up label. */
export function affiliateDisplayName(input: {
  name: string;
  organization: string | null;
}): string | null {
  const name = input.name.trim();
  if (name.length > 0) return name;
  const organization = input.organization?.trim() ?? "";
  if (organization.length > 0) return organization;
  return null;
}

export function affiliateInviteLine(displayName: string): string {
  return `${displayName} invited you to plan a site. They can see that you started a plan, but not what's in it.`;
}

export function inviteLineForAffiliate(input: {
  name: string;
  organization: string | null;
}): string | null {
  const displayName = affiliateDisplayName(input);
  if (!displayName) return null;
  return affiliateInviteLine(displayName);
}

export function affiliateInviteUrl(code: string): string {
  return `${SITE_ORIGIN}/plan/new?ref=${encodeURIComponent(code)}`;
}

function slugify(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** A unique-looking invite code. The database unique index is what makes it unique. */
export function buildAffiliateCode(
  name: string,
  organization: string | null,
  suffix: string
): string {
  const slug = slugify(name) || slugify(organization ?? "") || "affiliate";
  const tail = suffix.toLowerCase().replace(/[^a-z0-9]/g, "") || "code";
  const base = slug.slice(0, 48).replace(/-+$/g, "");
  const candidate = `${base}-${tail}`.slice(0, 64).replace(/-+$/g, "");
  return normalizeRefCode(candidate) ?? normalizeRefCode(`affiliate-${tail}`) ?? "affiliate";
}
