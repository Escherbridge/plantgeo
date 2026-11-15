"use client";

import { useState } from "react";
import { AFFILIATE_COPY_LINK_LABEL, AFFILIATE_COPY_LINK_STATUS } from "@/lib/affiliate/invite-copy";
import { TOUCH_TARGET } from "@/components/ui/touch-target";

/** Copies the invite URL. The URL stays visible if the clipboard is refused. */
export function CopyInviteLink({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);

  return (
    <div className="flex flex-col items-start gap-2">
      <button
        type="button"
        className={`rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 ${TOUCH_TARGET}`}
        onClick={() => {
          const write = navigator.clipboard?.writeText(url);
          if (!write) {
            setCopied(false);
            return;
          }
          void write.then(
            () => setCopied(true),
            () => setCopied(false)
          );
        }}
      >
        {AFFILIATE_COPY_LINK_LABEL}
      </button>
      <p role="status" className="text-sm text-zinc-300">
        {copied ? AFFILIATE_COPY_LINK_STATUS : ""}
      </p>
    </div>
  );
}
