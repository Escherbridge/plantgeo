import { notFound } from "next/navigation";

export function generateStaticParams(): { slug: string }[] {
  return [];
}

export const dynamicParams = false;

export default function NewsArticlePage() {
  notFound();
}
