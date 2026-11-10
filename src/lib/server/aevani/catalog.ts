import { z } from "zod";
import {
  fetchBoundedJson,
} from "@/lib/server/http/bounded-upstream";
import type { CatalogProductCard, CatalogRead } from "@/lib/plans/catalog-view";

/** Phase 1 default. No new environment variable is required. */
const DEFAULT_AEVANI_ORIGIN = "https://aevani.com";
const CATALOG_PATH = "/api/catalog/products";

const CATALOG_TIMEOUT_MS = 4_000;
const CATALOG_MAX_BYTES = 1_000_000;
const CATALOG_REVALIDATE_SECONDS = 60;
const MAX_CATALOG_PAGES = 3;
const MAX_PRODUCTS = 200;

const priceSchema = z
  .object({
    amountMinor: z.number().int().nonnegative(),
    currency: z.string().regex(/^[A-Z]{3}$/),
  })
  .strip();

const productSchema = z
  .object({
    id: z.string().trim().min(1).max(80),
    canonicalUrl: z.string().trim().max(500).nullable().optional(),
    title: z.string().trim().min(1).max(300),
    summary: z.string().trim().max(2_000).nullable().optional(),
    price: priceSchema.nullable().optional(),
    availability: z.string().trim().max(40).nullable().optional(),
    category: z.string().trim().max(120).nullable().optional(),
    images: z
      .array(
        z
          .object({
            url: z.string().trim().max(500),
            alt: z.string().trim().max(300).nullable().optional(),
          })
          .strip()
      )
      .max(8)
      .nullable()
      .optional(),
  })
  .strip();

const pageSchema = z
  .object({
    contractVersion: z.string().optional(),
    products: z.array(z.unknown()).optional(),
    nextCursor: z.string().trim().min(1).max(200).nullable().optional(),
  })
  .strip();

/**
 * Uses `AEVANI_CATALOG_URL` only when it is already set to an http(s) URL.
 * A missing or unusable value stays on the public default.
 */
export function resolveAevaniCatalogUrl(override = process.env.AEVANI_CATALOG_URL): URL {
  const configured = override?.trim();
  if (configured) {
    try {
      const url = new URL(configured);
      if (
        (url.protocol === "https:" || url.protocol === "http:") &&
        !url.username &&
        !url.password
      ) {
        return url;
      }
    } catch {
      // Fall through to the built-in origin.
    }
  }
  return new URL(CATALOG_PATH, DEFAULT_AEVANI_ORIGIN);
}

function httpsUrl(value: string | null | undefined): URL | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password) return null;
    return url;
  } catch {
    return null;
  }
}

/** Product pages may be linked. Cart, checkout, and affiliate click-throughs may not. */
function productPageUrl(value: string | null | undefined): string | null {
  const url = httpsUrl(value);
  if (!url) return null;
  const path = url.pathname.toLowerCase();
  const blocked =
    path === "/cart" ||
    path.startsWith("/cart/") ||
    path === "/checkout" ||
    path.startsWith("/checkout/") ||
    path === "/aff" ||
    path.startsWith("/aff/");
  return blocked ? null : url.toString();
}

function availabilityLabel(availability: string | null | undefined): {
  comingSoon: boolean;
  label: string;
} {
  const normalized = availability?.trim().toLowerCase() ?? "";
  if (normalized === "" || normalized === "unavailable" || normalized === "coming_soon" || normalized === "coming soon") {
    return { comingSoon: true, label: "Coming soon" };
  }
  if (!/^[a-z0-9][a-z0-9 _-]{0,40}$/i.test(availability ?? "")) {
    return { comingSoon: true, label: "Coming soon" };
  }
  return { comingSoon: false, label: availability!.trim() };
}

function priceLabel(price: { amountMinor: number; currency: string } | null | undefined): string | null {
  if (!price) return null;
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: price.currency,
    }).format(price.amountMinor / 100);
  } catch {
    return null;
  }
}

function toCard(raw: unknown): CatalogProductCard | null {
  const parsed = productSchema.safeParse(raw);
  if (!parsed.success) return null;
  const product = parsed.data;
  const availability = availabilityLabel(product.availability);
  const image = product.images?.map((entry) => ({
    url: httpsUrl(entry.url)?.toString() ?? null,
    alt: entry.alt ?? null,
  })).find((entry) => entry.url);
  return {
    id: product.id,
    title: product.title,
    summary: product.summary?.trim() ? product.summary.trim() : null,
    category: product.category?.trim() ? product.category.trim() : null,
    canonicalUrl: productPageUrl(product.canonicalUrl),
    imageUrl: image?.url ?? null,
    imageAlt: image?.alt ?? null,
    comingSoon: availability.comingSoon,
    availabilityLabel: availability.label,
    priceLabel: priceLabel(product.price),
  };
}

/**
 * Server-side read of the public Aevani catalog. A 404, a timeout, a bad
 * payload, or an empty list all become the same empty state so the plan page
 * still renders the map.
 */
export async function readAevaniCatalog(): Promise<CatalogRead> {
  const products: CatalogProductCard[] = [];
  const seenIds = new Set<string>();
  const seenCursors = new Set<string>();
  let cursor: string | null = null;

  try {
    for (let page = 0; page < MAX_CATALOG_PAGES && products.length < MAX_PRODUCTS; page += 1) {
      if (cursor) {
        if (seenCursors.has(cursor)) break;
        seenCursors.add(cursor);
      }
      const url = resolveAevaniCatalogUrl();
      if (cursor) url.searchParams.set("cursor", cursor);
      const payload = await fetchBoundedJson(
        url,
        { method: "GET" },
        {
          maxBytes: CATALOG_MAX_BYTES,
          timeoutMs: CATALOG_TIMEOUT_MS,
          revalidateSeconds: CATALOG_REVALIDATE_SECONDS,
        }
      );
      const parsed = pageSchema.safeParse(payload);
      if (!parsed.success) break;
      if (parsed.data.contractVersion !== undefined && parsed.data.contractVersion !== "v1") break;
      for (const raw of parsed.data.products ?? []) {
        const card = toCard(raw);
        if (!card || seenIds.has(card.id)) continue;
        seenIds.add(card.id);
        products.push(card);
        if (products.length >= MAX_PRODUCTS) break;
      }
      cursor = parsed.data.nextCursor ?? null;
      if (!cursor) break;
    }
  } catch {
    if (products.length === 0) return { status: "empty" };
  }

  return products.length > 0 ? { status: "ready", products } : { status: "empty" };
}
