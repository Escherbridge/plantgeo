import { CopyInviteLink } from "@/app/dashboard/affiliate/CopyInviteLink";
import { AFFILIATE_DASHBOARD_BODY, AFFILIATE_DASHBOARD_HEADING } from "@/lib/affiliate/invite-copy";

/** The affiliate's link and the count of plans started from it. No rate or payout. */
export function AffiliateInvitePanel({
  inviteUrl,
  plansStarted,
}: {
  inviteUrl: string;
  plansStarted: number;
}) {
  return (
    <section className="flex flex-col gap-4" aria-labelledby="affiliate-invite-heading">
      <h1 id="affiliate-invite-heading" className="text-xl font-semibold text-zinc-50">
        {AFFILIATE_DASHBOARD_HEADING}
      </h1>
      <p className="max-w-prose text-sm text-zinc-300">{AFFILIATE_DASHBOARD_BODY}</p>
      <p>
        <a href={inviteUrl} className="break-all text-sm text-emerald-400 underline">
          {inviteUrl}
        </a>
      </p>
      <CopyInviteLink url={inviteUrl} />
      <p className="text-sm text-zinc-300">
        Plans started <span className="font-semibold text-zinc-50">{plansStarted}</span>
      </p>
    </section>
  );
}
