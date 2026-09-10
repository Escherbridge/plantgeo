'use client';

import { useState } from 'react';

/** Shares only the text explicitly selected by the user; never publishes a conversation URL. */
export function CopyShareText({ text, title = 'PlantGeo analysis' }: { text: string; title?: string }) {
  const [status, setStatus] = useState('');
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setStatus('Copied.');
    } catch {
      setStatus('Copy is unavailable in this browser. Use the report export instead.');
    }
  };
  const share = async () => {
    if (!navigator.share) {
      try {
        await navigator.clipboard.writeText(text);
        setStatus('Text copied for sharing. Send it only to people you want to read it.');
      } catch {
        setStatus('Sharing is unavailable in this browser. Use the report export instead.');
      }
      return;
    }
    try {
      await navigator.share({ title, text });
      setStatus('Shared.');
    } catch (error) {
      setStatus(error instanceof Error && error.name === 'AbortError' ? 'Sharing canceled.' : 'Sharing did not complete.');
    }
  };
  return <div className="flex max-w-full flex-wrap items-center gap-2 text-xs">
    <button type="button" onClick={() => void copy()} disabled={!text} className="rounded border px-2.5 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-800">Copy text</button>
    <button type="button" onClick={() => void share()} disabled={!text} className="rounded border px-2.5 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-800">Share text</button>
    <span role="status" className="basis-full text-gray-500">{status}</span>
  </div>;
}
