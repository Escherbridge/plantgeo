/**
 * Converts a human-readable display string into a value legal in an HTTP header.
 *
 * HTTP header values are ByteStrings: every code unit must fit in one byte. Node
 * throws `TypeError: Cannot convert argument to a ByteString` while building the
 * Response — before any body is written — so a single typographic character in an
 * attribution constant fails every request on the route. Display strings keep
 * their typography; only the header boundary is sanitised.
 *
 * Promote to `src/lib/server/http/` once a second route needs it.
 */

/**
 * Typographic characters with an unambiguous ASCII spelling that NFKD leaves
 * alone. NFKD already folds the space variants and the ellipsis, so those are
 * deliberately absent.
 */
const ASCII_SPELLINGS: ReadonlyArray<readonly [RegExp, string]> = [
  // hyphen, non-breaking hyphen, figure dash, en dash, em dash, horizontal bar
  [/[‐-―]/g, "-"],
  // left/right single quote, single low and high-reversed quote
  [/[‘’‚‛]/g, "'"],
  // left/right double quote, double low and high-reversed quote
  [/[“”„‟]/g, '"'],
];

/** Returns `value` as printable ASCII, transliterating what it can and dropping the rest. */
export function toAsciiHeaderValue(value: string): string {
  let ascii = value.normalize("NFKD");
  for (const [pattern, replacement] of ASCII_SPELLINGS) {
    ascii = ascii.replace(pattern, replacement);
  }
  return ascii
    .replace(/\p{M}/gu, "") // combining marks left behind by NFKD ("é" -> "e")
    .replace(/[^\x20-\x7E]/g, "") // also strips CR/LF, so header injection cannot survive
    .replace(/ {2,}/g, " ")
    .trim();
}
