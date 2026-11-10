import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { SiteTypeDocument } from "@/components/content/SiteTypeDocument";
import { siteTypeArticleMetadata } from "@/lib/content/site-type-metadata";
import { SITE_TYPES, siteTypeBySlug } from "@/lib/content/site-types";

export function generateStaticParams() {
  return SITE_TYPES.map((page) => ({ slug: page.slug }));
}

export const dynamicParams = false;

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const page = siteTypeBySlug(slug);
  if (!page) return {};
  return siteTypeArticleMetadata(page);
}

export default async function SiteTypePage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const page = siteTypeBySlug(slug);
  if (!page) notFound();
  return <SiteTypeDocument page={page} />;
}
