import { getRegion } from "@/lib/region/region";
import { definitionDigest, isCustomRegion, EXPECTED_REGION_HEADER, SERVED_REGION_HEADER } from "@/lib/region/identity";

/** Bind the browser's actual response to its compiled definition before tRPC decodes it. */
export function withRegionIdentity(fetcher: typeof fetch): typeof fetch {
  return async (url, options) => {
    const region = getRegion();
    const expected = definitionDigest(region);
    const headers = new Headers(options?.headers);
    headers.set(EXPECTED_REGION_HEADER, expected);
    const response = await fetcher(url, { ...options, headers });
    const served = response.headers.get(SERVED_REGION_HEADER);
    if ((served !== null && served !== expected) || (served === null && isCustomRegion(region)) ||
        expected !== definitionDigest(getRegion())) {
      try { void response.body?.cancel().catch(() => {}); } catch { /* Keep the identity refusal. */ }
      throw new Error("region_identity_mismatch");
    }
    return response;
  };
}
