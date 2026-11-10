import type { Metadata } from "next";
import { CrawlableHome } from "@/components/home/CrawlableHome";
import { HomeMap } from "@/components/home/HomeMap";

export const metadata: Metadata = {
  title: "PlantGeo — The whole map, unbundled",
  description:
    "An open-source 3D map for planning a site, assembled from parts we run ourselves. The default basemap is a Pacific Northwest extract. PlantGeo publishes no forecasts.",
};

export default function HomePage() {
  return (
    <div className="home-document">
      <HomeMap />
      <CrawlableHome />
    </div>
  );
}
