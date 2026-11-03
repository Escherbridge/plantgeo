import { sha256 } from "@noble/hashes/sha2.js";
import type { Region } from "./region";

export const REGION_IDENTITY_VERSION = "plantgeo-region-v1";
export const EXPECTED_REGION_HEADER = "X-PlantGeo-Expected-Region-Definition";
export const SERVED_REGION_HEADER = "X-PlantGeo-Region-Definition";
const encoder = new TextEncoder();
export function bytesHex(bytes: Uint8Array): string {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** Versioned typed-token encoding; see AGENTS.md and the shared Python golden vectors. */
export function canonicalToken(value: unknown): Uint8Array {
  function token(item: unknown): string {
    if (item === null) return "n";
    if (typeof item === "boolean") return item ? "t" : "f";
    if (typeof item === "number") {
      if (!Number.isFinite(item) || (Number.isInteger(item) && !Number.isSafeInteger(item))) {
        throw new Error("identity_number_invalid");
      }
      const bytes = new Uint8Array(8);
      new DataView(bytes.buffer).setFloat64(0, item === 0 ? 0 : item, false);
      return `d${bytesHex(bytes)}`;
    }
    if (typeof item === "string") {
      for (const character of item) {
        const point = character.codePointAt(0)!;
        if (point >= 0xd800 && point <= 0xdfff) throw new Error("identity_unicode_invalid");
      }
      return `s${encoder.encode(item).length}:${item}`;
    }
    if (Array.isArray(item)) return `a${item.length}:${item.map(token).join("")}`;
    if (typeof item === "object" && item !== null) {
      if (Object.getPrototypeOf(item) !== Object.prototype && Object.getPrototypeOf(item) !== null) {
        throw new Error("identity_value_invalid");
      }
      const record = item as Record<string, unknown>;
      const keys = Object.keys(record).sort((a, b) => {
        const left = encoder.encode(a), right = encoder.encode(b);
        for (let i = 0; i < Math.min(left.length, right.length); i++) {
          if (left[i] !== right[i]) return left[i] - right[i];
        }
        return left.length - right.length;
      });
      return `m${keys.length}:${keys.map((key) => token(key) + token(record[key])).join("")}`;
    }
    throw new Error("identity_value_invalid");
  }
  return encoder.encode(token(value));
}

export function identityDigest(domain: string, value: unknown): string {
  return bytesHex(sha256(canonicalToken([REGION_IDENTITY_VERSION, domain, value])));
}

export function geographyDigest(region: Region): string {
  return identityDigest("geography", {
    envelope: region.envelope, default_camera_envelope: region.defaultCameraEnvelope,
    sub_envelopes: region.subEnvelopes, crs: region.crs, timezone: region.timezone,
    iso_country_codes: [...region.isoCountryCodes].sort(), admin_codes: [...region.adminCodes].sort(),
  });
}

export function definitionDigest(region: Region): string {
  const digest = identityDigest("definition", {
    geography_sha256: geographyDigest(region), slug: region.slug,
    lattice_pitch_degrees: region.latticePitchDegrees, lattice_origin_rule: region.latticeOriginRule,
    platform_layers: [...region.platformLayers].sort(),
    enabled_layers: [...region.enabledLayers].sort((a, b) => a.layerSlug < b.layerSlug ? -1 : a.layerSlug > b.layerSlug ? 1 : 0)
      .map((binding) => ({ layer_slug: binding.layerSlug, source_slug: binding.sourceSlug, coverage: binding.coverage })),
    analysis_lattices: Object.fromEntries(Object.entries(region.analysisLattices ?? {}).map(([key, lattice]) => [key, {
      pitch_degrees: lattice.pitchDegrees, origin_rule: lattice.originRule,
      envelope: lattice.envelope, cell_key_prefix: lattice.cellKeyPrefix,
    }])),
  });
  return digest;
}

export function isCustomRegion(region: Region): boolean {
  return region.slug !== "pnw" && region.slug !== "kenya-highlands";
}
