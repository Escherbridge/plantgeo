import type { Metadata } from "next";
import { CrawlableHome } from "@/components/home/CrawlableHome";
import { HomeMap } from "@/components/home/HomeMap";

export const metadata: Metadata = {
  title: "PlantGeo — Read sun, slope, drainage, and soils on your land",
  description:
    "An interactive 3D land-planning map for growers, homesteaders, and designers. Explore the live Ada County sample parcel, read field guides on drainage and microclimates, and start your own site plan. PlantGeo publishes no forecasts.",
};

export default function HomePage() {
  return (
    <div className="home-document">
      <HomeMap />
      <CrawlableHome />
    </div>
  );
}
