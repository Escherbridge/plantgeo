import { EditorialLink } from "@/components/ui/editorial";

/**
 * Crawlable home copy. It sits over the map until the canvas mounts, then the
 * shell hides it the way a skip link hides: still in the document, not a banner.
 */
export function CrawlableHome() {
  return (
    <section id="crawlable-home" className="home-crawlable">
      <div className="mx-auto flex w-full max-w-page flex-col px-page-inset py-section">
        <p className="font-editorial-label text-label text-ink-muted uppercase">PlantGeo</p>
        <h1 className="mt-comfortable max-w-measure font-editorial-display text-headline font-extrabold text-balance text-ink">
          Plan the land on a map you can inspect.
        </h1>
        <div className="mt-roomy max-w-measure font-editorial-text text-body text-ink [&>*+*]:mt-comfortable">
          <p>
            PlantGeo is a 3D geospatial platform assembled from open-source parts. Affiliates can
            invite someone — a landowner, a grower, a school, a homestead — to plan a site here.{" "}
            <EditorialLink href="https://aevani.com">Catalogue items from Aevani</EditorialLink> can
            be added to a plan later; nothing from that catalogue is for sale yet.
          </p>
          <p>
            The default basemap currently ships from a Pacific Northwest extract. PlantGeo publishes
            no forecasts. Strategy recommendations are refused until validated evidence is published.
          </p>
          <p>
            <EditorialLink href="/">Open the map</EditorialLink>
            {" · "}
            <EditorialLink href="/about">Read the About page</EditorialLink>
            {" · "}
            <EditorialLink href="/affiliate">Become an affiliate</EditorialLink>
          </p>
        </div>
      </div>
    </section>
  );
}
