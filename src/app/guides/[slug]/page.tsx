import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { GuideDocument } from "@/components/content/GuideDocument";
import { guideArticleMetadata } from "@/lib/content/guide-metadata";
import { GUIDES, guideBySlug } from "@/lib/content/guides";

export function generateStaticParams() {
  return GUIDES.map((page) => ({ slug: page.slug }));
}

export const dynamicParams = false;

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const page = guideBySlug(slug);
  if (!page) return {};
  return guideArticleMetadata(page);
}

export default async function GuidePage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const page = guideBySlug(slug);
  if (!page) notFound();
  return <GuideDocument page={page} />;
}
