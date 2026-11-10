/** Allowlisted catalog card. Prices appear only when the catalog response included them. */
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
