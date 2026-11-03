// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import vectors from "../../../services/agri-data-service/src/agri_data_service/foundation/region/identity_vectors.fixture.json";
import { bytesHex, canonicalToken, definitionDigest, geographyDigest, identityDigest } from "@/lib/region/identity";
import { decodeCustomRegionManifest } from "@/lib/region/custom-region-manifest";
import { regionSchema, regionIdentityVerdict } from "@/lib/region/region";

afterEach(() => vi.unstubAllEnvs());
describe("cross-runtime region identity", () => {
  it.each(vectors.vectors)("matches canonical bytes and digest %#", (vector) => {
    expect(bytesHex(canonicalToken(vector.value))).toBe(vector.token_hex);
    expect(identityDigest(vectors.domain, vector.value)).toBe(vector.sha256);
  });
  it("matches the Python manifest digests", () => {
    const region = regionSchema.parse(decodeCustomRegionManifest(JSON.stringify(vectors.manifest)));
    expect(geographyDigest(region)).toBe(vectors.geography_sha256);
    expect(definitionDigest(region)).toBe(vectors.definition_sha256);
    const before = definitionDigest(region);
    region.displayName = "Only a label";
    expect(definitionDigest(region)).toBe(before);
    region.envelope.west -= 1;
    expect(definitionDigest(region)).not.toBe(before);
  });
  it.each([new Date(), new Map(), Infinity, NaN, "\ud800"])("refuses noncanonical values %#", (value) => {
    expect(() => canonicalToken(value)).toThrow();
  });
  it("requires custom identity and refuses same-slug changed content", () => {
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION", vectors.manifest.slug);
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION_MANIFEST_JSON", JSON.stringify(vectors.manifest));
    expect(regionIdentityVerdict(vectors.manifest.slug).kind).toBe("mismatch");
    expect(regionIdentityVerdict(vectors.manifest.slug, vectors.definition_sha256).kind).toBe("agrees");
    const changed = structuredClone(vectors.manifest);
    changed.envelope.west -= 1;
    vi.stubEnv("NEXT_PUBLIC_PLANTGEO_REGION_MANIFEST_JSON", JSON.stringify(changed));
    expect(regionIdentityVerdict(changed.slug, vectors.definition_sha256).kind).toBe("mismatch");
  });
});
