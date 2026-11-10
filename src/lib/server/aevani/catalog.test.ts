import { beforeEach, describe, expect, it, vi } from "vitest";
import { UpstreamHttpError, UpstreamTimeoutError } from "@/lib/server/http/bounded-upstream";

const mocks = vi.hoisted(() => ({ fetchBoundedJson: vi.fn() }));

vi.mock("@/lib/server/http/bounded-upstream", async () => {
  const actual = await vi.importActual<typeof import("@/lib/server/http/bounded-upstream")>(
    "@/lib/server/http/bounded-upstream"
  );
  return { ...actual, fetchBoundedJson: mocks.fetchBoundedJson };
});

import { readAevaniCatalog, resolveAevaniCatalogUrl } from "@/lib/server/aevani/catalog";

const kit = {
  id: "product_12",
  canonicalUrl: "https://aevani.com/products/sample-kit",
  title: "Sample kit",
  summary: "A planning reference",
  price: { amountMinor: 1999, currency: "USD" },
  availability: "unavailable",
  category: "Kits",
  sku: "SECRET-SKU",
  costPrice: 100,
  images: [{ url: "https://aevani.com/images/kit.jpg", alt: "Kit" }],
};

describe("Aevani catalog read", () => {
  beforeEach(() => {
    mocks.fetchBoundedJson.mockReset();
    delete process.env.AEVANI_CATALOG_URL;
  });

  it("reads the default public catalog and keeps a short cache", async () => {
    mocks.fetchBoundedJson.mockResolvedValue({
      contractVersion: "v1",
      products: [kit],
      nextCursor: null,
    });

    const result = await readAevaniCatalog();
    const requested = mocks.fetchBoundedJson.mock.calls[0]?.[0] as URL;
    expect(requested.href).toBe("https://aevani.com/api/catalog/products");
    expect(mocks.fetchBoundedJson.mock.calls[0]?.[2]).toMatchObject({
      timeoutMs: 4_000,
      revalidateSeconds: 60,
    });
    expect(result.status).toBe("ready");
    if (result.status !== "ready") return;
    expect(result.products[0]).toMatchObject({
      id: "product_12",
      title: "Sample kit",
      comingSoon: true,
      availabilityLabel: "Coming soon",
      canonicalUrl: "https://aevani.com/products/sample-kit",
      imageUrl: "https://aevani.com/images/kit.jpg",
    });
    expect(result.products[0]?.priceLabel).toContain("19.99");
    expect(JSON.stringify(result)).not.toContain("SECRET-SKU");
    expect(JSON.stringify(result)).not.toContain("costPrice");
  });

  it("drops cart and checkout links and follows one cursor", async () => {
    mocks.fetchBoundedJson.mockImplementation(async (url: URL) => {
      if (url.searchParams.get("cursor") === "page-2") {
        return {
          contractVersion: "v1",
          products: [
            {
              id: "product_13",
              title: "Second kit",
              canonicalUrl: "https://aevani.com/cart",
              availability: "in_stock",
            },
          ],
          nextCursor: null,
        };
      }
      return {
        contractVersion: "v1",
        products: [kit],
        nextCursor: "page-2",
      };
    });

    const result = await readAevaniCatalog();
    expect(result.status).toBe("ready");
    if (result.status !== "ready") return;
    expect(result.products.map((product) => product.id)).toEqual(["product_12", "product_13"]);
    expect(result.products[1]?.canonicalUrl).toBeNull();
    expect(result.products[1]?.comingSoon).toBe(false);
    expect(result.products[1]?.priceLabel).toBeNull();
  });

  it("returns an empty catalog on 404, timeout, and a bad payload", async () => {
    mocks.fetchBoundedJson.mockRejectedValue(new UpstreamHttpError(404));
    await expect(readAevaniCatalog()).resolves.toEqual({ status: "empty" });

    mocks.fetchBoundedJson.mockRejectedValue(new UpstreamTimeoutError("timed out"));
    await expect(readAevaniCatalog()).resolves.toEqual({ status: "empty" });

    mocks.fetchBoundedJson.mockResolvedValue({ contractVersion: "v2", products: [kit] });
    await expect(readAevaniCatalog()).resolves.toEqual({ status: "empty" });

    mocks.fetchBoundedJson.mockResolvedValue({ contractVersion: "v1", products: [{ id: "" }] });
    await expect(readAevaniCatalog()).resolves.toEqual({ status: "empty" });
  });

  it("uses an already-set catalog URL and otherwise the public default", () => {
    expect(resolveAevaniCatalogUrl(undefined).href).toBe("https://aevani.com/api/catalog/products");
    expect(resolveAevaniCatalogUrl("https://catalog.example.org/list").href).toBe(
      "https://catalog.example.org/list"
    );
    expect(resolveAevaniCatalogUrl("not a url").href).toBe("https://aevani.com/api/catalog/products");
  });
});
