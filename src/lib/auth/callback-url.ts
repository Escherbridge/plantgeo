/** Post-authentication destination validation, shared by every auth surface. */

export const DEFAULT_CALLBACK_URL = "/dashboard";

/**
 * `callbackUrl` arrives from the query string, so it is attacker-controlled.
 * Only same-origin relative paths are accepted: an absolute or scheme-relative
 * value would let `/login?callbackUrl=https://evil.example/dashboard`
 * authenticate on the real domain and then hand the signed-in visitor to a
 * clone. NextAuth already validates the OAuth path; the credentials path,
 * which navigates on its own, does not get that for free.
 *
 * A leading backslash is rejected too — URL parsers fold `\` into `/`, so
 * `/\evil.example` resolves the same way `//evil.example` does.
 */
export function safeCallbackUrl(candidate: string | null | undefined): string {
  if (typeof candidate !== "string" || candidate.length === 0) {
    return DEFAULT_CALLBACK_URL;
  }
  if (!candidate.startsWith("/")) return DEFAULT_CALLBACK_URL;
  if (candidate.startsWith("//") || candidate.startsWith("/\\")) {
    return DEFAULT_CALLBACK_URL;
  }
  return candidate;
}
