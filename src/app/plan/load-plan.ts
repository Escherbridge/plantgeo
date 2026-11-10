import { TRPCError } from "@trpc/server";
import { notFound } from "next/navigation";
import type { PlanWorkspaceModel } from "@/components/plans/PlanWorkspace";
import { cameraForPlanBbox, isPlanBbox } from "@/lib/plans/bbox";
import type { CatalogRead } from "@/lib/plans/catalog-view";
import { readAevaniCatalog } from "@/lib/server/aevani/catalog";
import { appRouter } from "@/lib/server/trpc/router";
import { createTRPCContext } from "@/lib/server/trpc/init";

type LoadedPlan = {
  publicId: string;
  title: string;
  description: string | null;
  isSample: boolean;
  sampleLabel: string | null;
  bbox: unknown;
  canEdit: boolean;
  savedToAccount: boolean;
  items: PlanWorkspaceModel["items"];
};

export function toWorkspacePlan(plan: LoadedPlan | null, missing: boolean): PlanWorkspaceModel {
  const camera = plan && isPlanBbox(plan.bbox) ? cameraForPlanBbox(plan.bbox) : null;
  if (!plan || !camera) {
    return {
      publicId: plan?.publicId ?? "",
      title: plan?.title ?? "Sample plan",
      description: plan?.description ?? null,
      isSample: plan?.isSample ?? true,
      sampleLabel: plan?.sampleLabel ?? null,
      canEdit: false,
      savedToAccount: false,
      missing: true,
      items: plan?.items ?? [],
      camera: null,
    };
  }
  return {
    publicId: plan.publicId,
    title: plan.title,
    description: plan.description,
    isSample: plan.isSample,
    sampleLabel: plan.sampleLabel,
    canEdit: plan.canEdit,
    savedToAccount: plan.savedToAccount,
    missing,
    items: plan.items,
    camera,
  };
}

export async function loadCatalog(): Promise<CatalogRead> {
  return readAevaniCatalog();
}

export async function loadSamplePlan(): Promise<LoadedPlan | null> {
  const caller = appRouter.createCaller(await createTRPCContext());
  return caller.plans.getSample();
}

export async function loadPlanByPublicId(
  publicId: string,
  guestToken: string | undefined
): Promise<LoadedPlan> {
  const caller = appRouter.createCaller(await createTRPCContext());
  try {
    return await caller.plans.getByPublicId({ publicId, guestToken });
  } catch (error) {
    if (error instanceof TRPCError && error.code === "NOT_FOUND") notFound();
    if (error instanceof TRPCError && error.code === "BAD_REQUEST") notFound();
    throw error;
  }
}
