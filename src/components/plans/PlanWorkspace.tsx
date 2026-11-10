"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { CatalogPicker } from "@/components/plans/CatalogPicker";
import { SamplePlanBanner } from "@/components/plans/SamplePlanBanner";
import { editorialFocusRing, editorialFocusRingInverse } from "@/components/ui/editorial";
import type { CatalogProductCard, CatalogRead } from "@/lib/plans/catalog-view";
import { trpc } from "@/lib/trpc/client";
import { cn } from "@/lib/utils";

const MapView = dynamic(() => import("@/components/map/MapView"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full w-full items-center justify-center bg-paper">
      <p className="font-editorial-text text-body text-ink">Loading map...</p>
    </div>
  ),
});

export type PlanWorkspaceItem = {
  id: string;
  aevaniProductId: string;
  title: string;
  quantity: number;
  notes: string | null;
};

export type PlanWorkspaceModel = {
  publicId: string;
  title: string;
  description: string | null;
  isSample: boolean;
  sampleLabel: string | null;
  canEdit: boolean;
  savedToAccount: boolean;
  missing: boolean;
  items: PlanWorkspaceItem[];
  camera: { longitude: number; latitude: number; zoom: number } | null;
};

const fieldClass =
  "editorial-flat rule-all-medium w-full border-rule bg-paper px-snug py-tight font-editorial-text text-body text-ink";

export function PlanWorkspace({
  plan,
  catalog,
  guestToken,
  refCode,
  showStart,
}: {
  plan: PlanWorkspaceModel;
  catalog: CatalogRead;
  guestToken: string | null;
  refCode: string | null;
  showStart: boolean;
}) {
  const router = useRouter();
  const [items, setItems] = useState(plan.items);
  const [title, setTitle] = useState("Site plan");
  const [notice, setNotice] = useState<string | null>(null);
  const [pendingProductId, setPendingProductId] = useState<string | null>(null);
  const createPlan = trpc.plans.create.useMutation();
  const addItem = trpc.plans.addItem.useMutation();
  const updateItem = trpc.plans.updateItem.useMutation();
  const removeItem = trpc.plans.removeItem.useMutation();

  const selectedIds = useMemo(
    () => new Set(items.map((item) => item.aevaniProductId)),
    [items]
  );
  const readOnly = !plan.canEdit;

  async function startPlan() {
    setNotice(null);
    try {
      const created = await createPlan.mutateAsync({
        title,
        refCode: refCode ?? undefined,
      });
      const params = new URLSearchParams();
      if (created.guestToken) params.set("token", created.guestToken);
      const query = params.toString();
      router.push(query ? `/plan/${created.publicId}?${query}` : `/plan/${created.publicId}`);
    } catch {
      setNotice("The plan could not be started. Try again in a moment.");
    }
  }

  async function handleAdd(product: CatalogProductCard) {
    setNotice(null);
    setPendingProductId(product.id);
    try {
      const next = await addItem.mutateAsync({
        publicId: plan.publicId,
        guestToken: guestToken ?? undefined,
        aevaniProductId: product.id,
      });
      setItems(next);
    } catch {
      setNotice("That item could not be added.");
    } finally {
      setPendingProductId(null);
    }
  }

  async function handleRemove(productId: string) {
    const item = items.find((entry) => entry.aevaniProductId === productId);
    if (!item) return;
    setNotice(null);
    setPendingProductId(productId);
    try {
      const next = await removeItem.mutateAsync({
        publicId: plan.publicId,
        guestToken: guestToken ?? undefined,
        itemId: item.id,
      });
      setItems(next);
    } catch {
      setNotice("That item could not be removed.");
    } finally {
      setPendingProductId(null);
    }
  }

  async function changeQuantity(item: PlanWorkspaceItem, quantity: number) {
    if (quantity < 1) return;
    setPendingProductId(item.aevaniProductId);
    try {
      const next = await updateItem.mutateAsync({
        publicId: plan.publicId,
        guestToken: guestToken ?? undefined,
        itemId: item.id,
        quantity,
        notes: item.notes,
      });
      setItems(next);
    } catch {
      setNotice("The quantity could not be updated.");
    } finally {
      setPendingProductId(null);
    }
  }

  return (
    <div className="viewport-below-top-bar flex w-full flex-col overflow-hidden bg-paper">
      {plan.isSample ? <SamplePlanBanner label={plan.sampleLabel} /> : null}
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <div className="relative min-h-0 flex-1">
          <MapView
            initialLongitude={plan.camera?.longitude}
            initialLatitude={plan.camera?.latitude}
            initialZoom={plan.camera?.zoom}
          />
        </div>
        <aside className="h-[46%] shrink-0 overflow-y-auto rule-top-medium border-t-rule bg-paper px-comfortable py-comfortable md:h-auto md:w-[24rem] md:border-t-0 md:rule-left-medium md:border-l-rule">
          <p className="font-editorial-label text-label text-ink-muted uppercase">Site plan</p>
          <h1 className="mt-tight font-editorial-display text-heading text-ink">{plan.title}</h1>
          {plan.description ? (
            <p className="mt-snug font-editorial-text text-body text-ink">{plan.description}</p>
          ) : null}
          {plan.missing ? (
            <p className="mt-snug font-editorial-text text-body text-ink" role="status">
              The sample plan is not available yet, so the map is on its usual opening view.
            </p>
          ) : null}
          {plan.isSample ? (
            <p className="mt-snug font-editorial-text text-body text-ink">
              This demonstration uses live map data for Ada County, Idaho. It is not a real customer.
            </p>
          ) : plan.savedToAccount ? (
            <p className="mt-snug font-editorial-text text-body text-ink">This plan is saved to your account.</p>
          ) : plan.canEdit ? (
            <p className="mt-snug font-editorial-text text-body text-ink">
              This plan is saved to this private link. Keep the link; it is how you edit the plan.
            </p>
          ) : (
            <p className="mt-snug font-editorial-text text-body text-ink">
              You can view this plan. Editing needs the private link, or the account that created it.
            </p>
          )}

          {showStart ? (
            <form
              className="mt-comfortable flex flex-col gap-snug"
              onSubmit={(event) => {
                event.preventDefault();
                void startPlan();
              }}
            >
              <label className="font-editorial-label text-label text-ink-muted uppercase" htmlFor="plan-title">
                Plan name
              </label>
              <input
                id="plan-title"
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                maxLength={200}
                required
                className={cn(fieldClass, editorialFocusRing)}
              />
              <button
                type="submit"
                disabled={createPlan.isPending}
                className={cn(
                  "bg-ink px-comfortable py-snug font-editorial-label text-label text-paper uppercase hover:bg-accent hover:text-accent-ink disabled:opacity-70",
                  editorialFocusRingInverse
                )}
              >
                {createPlan.isPending ? "Starting" : "Start your plan"}
              </button>
              <p className="font-editorial-text text-caption text-ink">
                Signed in, the plan is saved to your account. Otherwise you get a private link.{" "}
                <Link href="/login?callbackUrl=%2Fplan%2Fnew" className="underline decoration-accent decoration-2 underline-offset-4">
                  Sign in
                </Link>{" "}
                or{" "}
                <Link href="/register?callbackUrl=%2Fplan%2Fnew" className="underline decoration-accent decoration-2 underline-offset-4">
                  create an account
                </Link>
                .
              </p>
            </form>
          ) : null}

          {items.length > 0 ? (
            <ul className="mt-comfortable flex flex-col gap-snug">
              {items.map((item) => (
                <li key={item.id} className="rule-top-hairline border-t-rule-faint pt-snug">
                  <p className="font-editorial-text text-body text-ink">{item.title}</p>
                  {plan.canEdit ? (
                    <div className="mt-tight flex items-center gap-snug">
                      <button
                        type="button"
                        className={cn("px-snug py-tight font-editorial-label text-label text-ink uppercase", editorialFocusRing)}
                        onClick={() => void changeQuantity(item, item.quantity - 1)}
                        disabled={item.quantity <= 1 || pendingProductId === item.aevaniProductId}
                      >
                        Less
                      </button>
                      <span className="font-editorial-label text-label text-ink">{item.quantity}</span>
                      <button
                        type="button"
                        className={cn("px-snug py-tight font-editorial-label text-label text-ink uppercase", editorialFocusRing)}
                        onClick={() => void changeQuantity(item, item.quantity + 1)}
                        disabled={pendingProductId === item.aevaniProductId}
                      >
                        More
                      </button>
                    </div>
                  ) : (
                    <p className="font-editorial-label text-label text-ink-muted uppercase">Quantity {item.quantity}</p>
                  )}
                </li>
              ))}
            </ul>
          ) : null}

          {notice ? (
            <p role="status" className="mt-snug font-editorial-text text-caption text-ink">
              {notice}
            </p>
          ) : null}

          <h2 className="mt-section font-editorial-display text-subheading text-ink">Aevani catalog</h2>
          <p className="mt-tight font-editorial-text text-caption text-ink">
            These are planning references. Coming soon means the product is not for sale here.
          </p>
          <div className="mt-comfortable">
            <CatalogPicker
              catalog={catalog}
              selectedIds={selectedIds}
              readOnly={readOnly}
              pendingProductId={pendingProductId}
              onAdd={(product) => void handleAdd(product)}
              onRemove={(productId) => void handleRemove(productId)}
            />
          </div>
        </aside>
      </div>
    </div>
  );
}
