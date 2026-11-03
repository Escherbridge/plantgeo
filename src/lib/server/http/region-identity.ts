import { getRegion } from "@/lib/region/region";
import { definitionDigest, isCustomRegion, EXPECTED_REGION_HEADER, SERVED_REGION_HEADER } from "@/lib/region/identity";
import { fetchBoundedJson, providerUrl, UpstreamPayloadError } from "./bounded-upstream";


export function serverRegionScope(): string {
  return `${providerUrl("AGRI_PARQUET_SERVICE_URL", "http://localhost:8000").href}|${definitionDigest(getRegion())}`;
}

/** Check identity on the actual bounded response, independently of any earlier census. */
export function fetchRegionJson(
  url: string | URL, init: RequestInit, options: Parameters<typeof fetchBoundedJson>[2]
): Promise<unknown> {
  const region = getRegion();
  const expected = definitionDigest(region);
  const headers = new Headers(init.headers);
  headers.set(EXPECTED_REGION_HEADER, expected);
  return fetchBoundedJson(url, { ...init, headers }, options, (responseHeaders) => {
    const served = responseHeaders.get(SERVED_REGION_HEADER);
    if ((served !== null && served !== expected) || (served === null && isCustomRegion(region)) ||
        definitionDigest(getRegion()) !== expected) {
      throw new UpstreamPayloadError("region_identity_mismatch");
    }
  });
}
