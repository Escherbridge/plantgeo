export type AffiliateApplicationStatus = "pending" | "approved" | "declined";

/** One row on the admin review list. No review time, rate, or payout. */
export type AffiliateApplicationListItem = {
  id: string;
  name: string;
  email: string;
  organization: string | null;
  role: string | null;
  website: string | null;
  message: string | null;
  status: AffiliateApplicationStatus;
  code: string | null;
};

export function affiliateApplicationStatus(
  value: string
): AffiliateApplicationStatus | null {
  if (value === "pending" || value === "approved" || value === "declined") return value;
  return null;
}
