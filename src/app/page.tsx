import { PublicJsonLd } from "@/components/content/PublicJsonLd";
import { CrawlableHome } from "@/components/home/CrawlableHome";
import { HomeMap } from "@/components/home/HomeMap";
import { publicPageMetadata } from "@/lib/public-metadata";

const TITLE = "PlantGeo — Read sun, slope, drainage, and soils on your land";
const DESCRIPTION =
  "An interactive 3D land-planning map for growers, homesteaders, and designers. Explore the live Ada County sample parcel, read field guides on drainage and microclimates, and start your own site plan. PlantGeo publishes no forecasts.";

export const metadata = publicPageMetadata({
  title: TITLE,
  description: DESCRIPTION,
  path: "/",
});

export default function HomePage() {
  return (
    <div className="home-document">
      <PublicJsonLd name={TITLE} description={DESCRIPTION} path="/" />
      <HomeMap />
      <CrawlableHome />
    </div>
  );
}
