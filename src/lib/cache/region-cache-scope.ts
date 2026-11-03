import { definitionDigest } from "@/lib/region/identity";
import { getRegion } from "@/lib/region/region";

/** Browser-visible origin only; upstream service addresses never belong in public storage. */
export function regionCacheScope(): string {
  const origin = typeof window === "undefined" ? "server" : window.location.origin;
  return `${origin}/api/trpc|${definitionDigest(getRegion())}`;
}

export function scopedQueryKey(queryHash: string): string {
  return `plantgeo-scope:${JSON.stringify(regionCacheScope())}:${queryHash}`;
}

export function keyBelongsToCurrentScope(key: string, scope = regionCacheScope()): boolean {
  return key.startsWith(`plantgeo-scope:${JSON.stringify(scope)}:`);
}

export function unscopedQueryHash(key: string): string {
  if (!key.startsWith("plantgeo-scope:")) return key;
  const start = "plantgeo-scope:".length;
  let end = start + 1;
  while (end < key.length) {
    if (key[end] === "\\") { end += 2; continue; }
    if (key[end] === '"') return key.slice(end + 2);
    end++;
  }
  return key;
}
