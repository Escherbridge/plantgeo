/** Allowlisted catalog card. A Coming soon item never carries a price. */
export type CatalogProductCard = {
  id: string;
  title: string;
  summary: string | null;
  category: string | null;
  canonicalUrl: string | null;
  imageUrl: string | null;
  imageAlt: string | null;
  comingSoon: boolean;
  availabilityLabel: string;
  priceLabel: string | null;
};

export type CatalogRead =
  | { status: "ready"; products: CatalogProductCard[] }
  | { status: "empty" };
