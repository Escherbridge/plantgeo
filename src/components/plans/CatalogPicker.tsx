"use client";

import type { CatalogProductCard, CatalogRead } from "@/lib/plans/catalog-view";
import { editorialFocusRing, editorialFocusRingInverse } from "@/components/ui/editorial";
import { cn } from "@/lib/utils";

export function CatalogPicker({
  catalog,
  selectedIds,
  readOnly,
  pendingProductId,
  onAdd,
  onRemove,
}: {
  catalog: CatalogRead;
  selectedIds: ReadonlySet<string>;
  readOnly: boolean;
  pendingProductId: string | null;
  onAdd: (product: CatalogProductCard) => void;
  onRemove: (productId: string) => void;
}) {
  if (catalog.status === "empty") {
    return (
      <p role="status" className="font-editorial-text text-body text-ink">
        The Aevani catalog is not available right now. The map still shows live PlantGeo data.
      </p>
    );
  }

  return (
    <ul className="flex flex-col gap-comfortable">
      {catalog.products.map((product) => {
        const selected = selectedIds.has(product.id);
        const pending = pendingProductId === product.id;
        return (
          <li key={product.id} className="rule-all-hairline border-rule bg-paper p-snug">
            {product.imageUrl ? (
              <img
                src={product.imageUrl}
                alt={product.imageAlt ?? ""}
                className="mb-snug h-24 w-full object-cover"
              />
            ) : null}
            {product.category ? (
              <p className="font-editorial-label text-label text-ink-muted uppercase">{product.category}</p>
            ) : null}
            <h3 className="mt-tight font-editorial-display text-subheading text-ink">{product.title}</h3>
            {product.summary ? (
              <p className="mt-tight font-editorial-text text-caption text-ink">{product.summary}</p>
            ) : null}
            <p className="mt-snug font-editorial-label text-label text-ink uppercase">
              {product.comingSoon ? "Coming soon" : product.availabilityLabel}
            </p>
            {product.priceLabel ? (
              <p className="mt-tight font-editorial-text text-caption text-ink">{product.priceLabel}</p>
            ) : null}
            {product.canonicalUrl ? (
              <a
                href={product.canonicalUrl}
                target="_blank"
                rel="noreferrer noopener"
                className={cn(
                  "mt-snug inline-block font-editorial-text text-caption text-ink underline decoration-accent decoration-2 underline-offset-4",
                  editorialFocusRing
                )}
              >
                View on Aevani — not for sale
              </a>
            ) : null}
            {readOnly ? null : selected ? (
              <button
                type="button"
                disabled={pending}
                onClick={() => onRemove(product.id)}
                className={cn(
                  "mt-snug bg-paper px-snug py-tight font-editorial-label text-label text-ink uppercase rule-all-hairline border-rule hover:border-accent hover:text-accent disabled:opacity-70",
                  editorialFocusRing
                )}
              >
                {pending ? "Removing" : "Remove"}
              </button>
            ) : (
              <button
                type="button"
                disabled={pending}
                onClick={() => onAdd(product)}
                className={cn(
                  "mt-snug bg-ink px-snug py-tight font-editorial-label text-label text-paper uppercase hover:bg-accent hover:text-accent-ink disabled:opacity-70",
                  editorialFocusRingInverse
                )}
              >
                {pending ? "Adding" : "Add to plan"}
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
