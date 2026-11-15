import {
  EditorialActionLink,
  EditorialLink,
} from "@/components/ui/editorial";

/**
 * Crawlable home copy. It sits over the map until the canvas mounts, then the
 * shell hides it the way a skip link hides: still in the document, not a banner.
 */
export function CrawlableHome() {
  return (
    <section id="crawlable-home" className="home-crawlable">
      <div className="mx-auto flex w-full max-w-page flex-col px-page-inset py-section">
        <p className="font-editorial-label text-label text-ink-muted uppercase">
          PlantGeo · Site-Planning Map
        </p>
        <h1 className="mt-comfortable max-w-measure font-editorial-display text-headline font-extrabold text-balance text-ink">
          Read sun, slope, and water before you stake a single bed.
        </h1>
        <div className="mt-roomy max-w-measure font-editorial-text text-body text-ink [&>*+*]:mt-comfortable">
          <p>
            Every yard and 5-acre parcel hides low drainage swales, winter frost pockets, compacted
            paths, and late-afternoon heat sinks long before a shovel hits dirt. PlantGeo brings
            bare-earth elevation, wetlands, soil survey units, and flood boundaries onto one 3D
            map so growers, permaculture designers, and homesteaders can match desktop layers to a
            muddy-boots walk.
          </p>
          <div className="flex flex-wrap gap-snug">
            <EditorialActionLink href="/plan/sample-ada-county" tone="solid">
              Open the Ada County Sample Plan
            </EditorialActionLink>
            <EditorialActionLink href="/plan/new" tone="outline">
              Start your own site plan
            </EditorialActionLink>
          </div>
          <p>
            New to reading terrain, wet spots, and flood maps? Read our{" "}
            <EditorialLink href="/guides">field guides</EditorialLink> and{" "}
            <EditorialLink href="/blog">land-planning blog</EditorialLink>, or compare{" "}
            <EditorialLink href="/site-types">parcel site types</EditorialLink>. If you help
            landowners, schools, or growers plan sites, you can{" "}
            <EditorialLink href="/affiliate">become an affiliate</EditorialLink>.
          </p>
          <aside className="rule-top-hairline border-t-rule-faint pt-comfortable font-editorial-text text-caption text-ink-muted [&>*+*]:mt-tight">
            <p>
              <strong>Map &amp; preview note:</strong> Assembled from open-source geospatial parts
              with a Pacific Northwest default basemap extract. PlantGeo publishes no forecasts and
              is not a boundary survey, permitting decision, or insurance determination.{" "}
              <a
                href="https://aevani.com"
                className="underline decoration-accent decoration-2 underline-offset-4"
              >
                Catalogue items from Aevani
              </a>{" "}
              can be referenced inside a site plan; nothing from that preview catalogue is for sale
              yet, and no affiliate referral rate is quoted.{" "}
              <EditorialLink href="/about">Technical notes on About</EditorialLink>.
            </p>
          </aside>
        </div>
      </div>
    </section>
  );
}
