import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CatalogPicker } from "@/components/plans/CatalogPicker";
import type { CatalogProductCard } from "@/lib/plans/catalog-view";

const comingSoon: CatalogProductCard = {
  id: "product_12",
  title: "Heirloom Tomato Collection",
  summary: "6 heritage varieties.",
  category: "Heirloom Seeds",
  canonicalUrl: "https://aevani.com/products/supplies/heirloom-tomato-collection",
  imageUrl: null,
  imageAlt: null,
  comingSoon: true,
  availabilityLabel: "Coming soon",
  priceLabel: "$34.99",
};

const forSale: CatalogProductCard = {
  ...comingSoon,
  id: "product_13",
  title: "In stock kit",
  comingSoon: false,
  availabilityLabel: "In stock",
  priceLabel: "$12.00",
};

describe("catalog picker", () => {
  it("hides the price on a Coming soon item and keeps the badge and the not-for-sale link", () => {
    render(
      <CatalogPicker
        catalog={{ status: "ready", products: [comingSoon, forSale] }}
        selectedIds={new Set()}
        readOnly={false}
        pendingProductId={null}
        onAdd={() => {}}
        onRemove={() => {}}
      />
    );

    expect(screen.getByText("Coming soon")).toBeTruthy();
    expect(screen.queryByText("$34.99")).toBeNull();
    const preview = screen.getAllByRole("link", { name: "View on Aevani — not for sale" });
    expect(preview[0]?.getAttribute("href")).toBe(comingSoon.canonicalUrl);
    expect(screen.getByText("$12.00")).toBeTruthy();
    expect(screen.getByText("In stock")).toBeTruthy();
  });
});
