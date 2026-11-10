import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { EssayDocument } from "@/components/writing/EssayDocument";
import { essayPageMetadata } from "@/lib/writing/essay-metadata";
import { ESSAYS, essayBySlug } from "@/lib/writing/essays";

export function generateStaticParams() {
  return ESSAYS.map((essay) => ({ slug: essay.slug }));
}

export const dynamicParams = false;

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const essay = essayBySlug(slug);
  if (!essay) return {};
  return essayPageMetadata(essay);
}

export default async function EssayPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const essay = essayBySlug(slug);
  if (!essay) notFound();
  return <EssayDocument essay={essay} />;
}
