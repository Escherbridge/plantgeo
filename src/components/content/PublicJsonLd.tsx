import { JsonLd } from "@/components/content/JsonLd";
import { publicPageGraph } from "@/lib/content/structured-data";

export function PublicJsonLd({
  name,
  description,
  path,
}: {
  name: string;
  description: string;
  path: string;
}) {
  return <JsonLd data={publicPageGraph({ name, description, path })} />;
}
