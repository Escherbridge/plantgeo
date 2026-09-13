"use client";

import { useMemo, useState } from "react";
import { buildDraftInquiryText, ROUTE_MEANING_LABELS } from "./land-context-utils";
import type { DraftInquiryInput } from "./types";

interface DraftInquiryProps {
  /** Everything except the free-text idea, which the user types here. */
  input: Omit<DraftInquiryInput, "userIdea">;
}

/**
 * Draft inquiry action. Generating and copying this draft is pure client-side string assembly
 * (`buildDraftInquiryText`) plus `navigator.clipboard.writeText` -- there is no fetch call, no
 * mutation, no submission anywhere in this component. Opening the recipient's official form is a
 * separate, user-driven link click; this component never auto-navigates or auto-submits.
 */
export function DraftInquiry({ input }: DraftInquiryProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [userIdea, setUserIdea] = useState("");
  const [draftText, setDraftText] = useState("");
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "failed">("idle");

  const recipientLabel = useMemo(
    () => `${input.recipient.name}${input.recipient.role ? ` (${input.recipient.role})` : ""}`,
    [input.recipient],
  );

  function handleOpen() {
    setIsOpen(true);
    setDraftText(buildDraftInquiryText({ ...input, userIdea }));
    setCopyStatus("idle");
  }

  function handleRegenerate(nextUserIdea: string) {
    setUserIdea(nextUserIdea);
    setDraftText(buildDraftInquiryText({ ...input, userIdea: nextUserIdea }));
    setCopyStatus("idle");
  }

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(draftText);
      setCopyStatus("copied");
    } catch {
      setCopyStatus("failed");
    }
  }

  if (!isOpen) {
    return (
      <button
        type="button"
        onClick={handleOpen}
        className="rounded border border-sky-700 bg-sky-950/40 px-3 py-1.5 text-xs font-medium text-sky-300 hover:bg-sky-950/70"
      >
        Draft inquiry
      </button>
    );
  }

  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-900 p-3">
      <div className="text-sm font-semibold text-zinc-100">Draft inquiry</div>
      <div className="mt-1 text-xs text-zinc-400">
        Recipient: {recipientLabel} -- {ROUTE_MEANING_LABELS[input.routeMeaning]}
      </div>
      <div className="mt-1 text-xs text-zinc-500">
        Why this recipient: {input.recipientRationale}
      </div>

      <label className="mt-2 block text-xs text-zinc-400" htmlFor="land-context-draft-idea">
        Your idea (free text)
      </label>
      <textarea
        id="land-context-draft-idea"
        value={userIdea}
        onChange={(event) => handleRegenerate(event.target.value)}
        rows={2}
        className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 p-2 text-xs text-zinc-200"
        placeholder="Describe what you're looking into..."
      />

      <label className="mt-3 block text-xs text-zinc-400" htmlFor="land-context-draft-text">
        Draft (editable, review before sending)
      </label>
      <textarea
        id="land-context-draft-text"
        value={draftText}
        onChange={(event) => setDraftText(event.target.value)}
        rows={10}
        className="mt-1 w-full rounded border border-zinc-700 bg-zinc-950 p-2 font-mono text-xs text-zinc-200"
      />

      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={handleCopy}
          className="rounded border border-emerald-700 bg-emerald-950/40 px-3 py-1.5 text-xs font-medium text-emerald-300 hover:bg-emerald-950/70"
        >
          Copy draft
        </button>
        <button
          type="button"
          onClick={() => setIsOpen(false)}
          className="rounded border border-zinc-700 px-3 py-1.5 text-xs font-medium text-zinc-400 hover:text-zinc-200"
        >
          Close
        </button>
        {copyStatus === "copied" ? <span className="text-xs text-emerald-400">Copied</span> : null}
        {copyStatus === "failed" ? <span className="text-xs text-red-400">Copy failed -- select and copy manually</span> : null}
      </div>

      <div className="mt-2 text-[11px] text-zinc-500">
        This is a draft only. Copying it sends nothing -- no email, form submission or automatic contact.
      </div>
    </div>
  );
}
