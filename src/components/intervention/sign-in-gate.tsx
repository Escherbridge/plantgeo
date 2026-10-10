"use client";

/**
 * The one sign-in destination the social controls point at.
 *
 * It matches `/feed`'s `SignedOutGate` link (`/login?callbackUrl=%2Ffeed`) in
 * shape: a prompt, never a silent failure and never a raw UNAUTHORIZED, because
 * every `trpc.interventionSocial.*` procedure is signed-in-only.
 */
export const SOCIAL_SIGN_IN_HREF = "/login?callbackUrl=%2Ffeed";

/** The inline, in-card equivalent of `/feed`'s full-page signed-out notice. */
export function SocialSignInPrompt({
  message,
  testId,
}: {
  message: string;
  testId: string;
}) {
  return (
    <p
      data-testid={testId}
      role="status"
      className="rounded border border-[hsl(var(--border))] px-3 py-2 text-xs"
    >
      {message}{" "}
      {/* touch-target-exempt: inline prose link inside running text, not a flex item --
          min-height has no effect on a non-replaced inline box, so there is nothing to grow. */}
      <a href={SOCIAL_SIGN_IN_HREF} className="underline">
        Log in
      </a>
    </p>
  );
}
