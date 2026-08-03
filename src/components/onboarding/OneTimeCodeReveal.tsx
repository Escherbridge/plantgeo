"use client";

import { useState } from "react";
import { AlertTriangle, Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";

interface OneTimeCodeRevealProps {
  code: string;
  joinUrl: string;
  onDismiss: () => void;
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard permissions can be denied; the value is still selectable in place.
    }
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      className={cn(
        "flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium transition-colors",
        copied
          ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-400"
          : "border-zinc-700 bg-zinc-800 text-zinc-300 hover:bg-zinc-700"
      )}
    >
      {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      {copied ? "Copied" : label}
    </button>
  );
}

/**
 * Shows a freshly created join code exactly once. Callers must not persist the
 * raw code themselves — after `onDismiss` it is gone from the client for good.
 */
export function OneTimeCodeReveal({ code, joinUrl, onDismiss }: OneTimeCodeRevealProps) {
  return (
    <div className="flex flex-col gap-4 rounded-lg border border-amber-500/30 bg-amber-500/6 p-5">
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
        <p className="text-xs leading-relaxed text-amber-200/90">
          This code will not be shown again. Copy it now and store it somewhere your team can
          retrieve it.
        </p>
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">Join code</span>
        <div className="flex items-center justify-between gap-2 rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2.5">
          <code className="select-all font-mono text-lg tracking-[0.15em] text-zinc-100">{code}</code>
          <CopyButton value={code} label="Copy code" />
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-[10px] uppercase tracking-[0.2em] text-zinc-500">Join link</span>
        <div className="flex items-center justify-between gap-2 rounded-md border border-zinc-800 bg-zinc-950 px-3 py-2.5">
          <span className="truncate font-mono text-xs text-zinc-400">{joinUrl}</span>
          <CopyButton value={joinUrl} label="Copy link" />
        </div>
      </div>

      <button
        type="button"
        onClick={onDismiss}
        className="self-end rounded-md bg-zinc-800 px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:bg-zinc-700"
      >
        I&rsquo;ve saved it
      </button>
    </div>
  );
}
