"use client";

import Link from "next/link";
import { useEffect, useState, useSyncExternalStore } from "react";
import { editorialFocusRing, editorialFocusRingInverse } from "@/components/ui/editorial";
import {
  guestPlanEditPath,
  guestPlanEditUrl,
  rememberGuestPlan,
} from "@/lib/plans/guest-plan-links";
import { cn } from "@/lib/utils";

function subscribeOrigin(): () => void {
  return () => {};
}

function originSnapshot(): string {
  return window.location.origin;
}

function originServerSnapshot(): string {
  return "";
}

export function GuestLinkPanel({
  publicId,
  guestToken,
  title,
  plain = false,
}: {
  publicId: string;
  guestToken: string;
  title: string;
  /** Document column, rather than the full-width band above the map. */
  plain?: boolean;
}) {
  const origin = useSyncExternalStore(subscribeOrigin, originSnapshot, originServerSnapshot);
  const path = guestPlanEditPath({ publicId, token: guestToken });
  const fullLink = origin ? guestPlanEditUrl(origin, { publicId, token: guestToken }) : path;
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState<string | null>(null);

  useEffect(() => {
    try {
      rememberGuestPlan(window.localStorage, { publicId, token: guestToken, title });
    } catch {
      // The link is on the page even when this browser cannot store it.
    }
  }, [publicId, guestToken, title]);

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(fullLink);
      setCopied(true);
      setCopyError(null);
    } catch {
      setCopied(false);
      setCopyError("The link could not be copied. Select the link above.");
    }
  }

  return (
    <div
      className={cn(
        "flex flex-col gap-snug",
        plain
          ? "max-w-measure"
          : "shrink-0 border-b border-rule bg-paper px-comfortable py-comfortable"
      )}
    >
      <h2 className="font-editorial-display text-subheading text-ink">Keep this link.</h2>
      <p className="max-w-measure font-editorial-text text-body text-ink">
        It&apos;s the only way to edit this plan without an account. Anyone with it can edit, so
        share it carefully.
      </p>
      <p className="max-w-measure font-editorial-text text-caption break-all text-ink">
        <a
          href={path}
          className={cn(
            "underline decoration-accent decoration-2 underline-offset-4",
            editorialFocusRing
          )}
        >
          {fullLink}
        </a>
      </p>
      <div className="flex flex-wrap gap-snug">
        <button
          type="button"
          onClick={() => void copyLink()}
          className={cn(
            "bg-ink px-comfortable py-snug font-editorial-label text-label text-paper uppercase hover:bg-accent hover:text-accent-ink",
            editorialFocusRingInverse
          )}
        >
          Copy edit link
        </button>
        <Link
          href={`/register?callbackUrl=${encodeURIComponent(`/plan/${publicId}`)}`}
          className={cn(
            "inline-flex items-center border border-rule bg-paper px-comfortable py-snug font-editorial-label text-label text-ink uppercase hover:border-accent hover:text-accent",
            editorialFocusRing
          )}
        >
          Create an account to keep this plan
        </Link>
      </div>
      {copied ? (
        <p role="status" className="font-editorial-text text-body text-ink">
          Link copied.
        </p>
      ) : null}
      {copyError ? (
        <p role="status" className="font-editorial-text text-caption text-ink">
          {copyError}
        </p>
      ) : null}
    </div>
  );
}
