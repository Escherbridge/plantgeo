"use client";

import { useEffect } from "react";
import type maplibregl from "maplibre-gl";
import { useMap } from "@/lib/map/map-context";
import { useImageryStore } from "@/stores/imagery-store";

const ACCESS_TOKEN = process.env.NEXT_PUBLIC_MAPILLARY_ACCESS_TOKEN || "";

const SOURCE_ID = "mapillary-coverage";
const LAYER_ID = "mapillary-sequences";

export function StreetCoverage() {
  const map = useMap();
  const { isStreetViewEnabled, setSelectedImageId } = useImageryStore();

  useEffect(() => {
    if (!map) return;

    const setup = () => {
      if (!map.getSource(SOURCE_ID)) {
        map.addSource(SOURCE_ID, {
          type: "vector",
          tiles: [
            `https://tiles.mapillary.com/maps/vtp/mly1_public/2/{z}/{x}/{y}?access_token=${ACCESS_TOKEN}`,
          ],
          minzoom: 6,
          maxzoom: 14,
        });
      }

      if (!map.getLayer(LAYER_ID)) {
        map.addLayer({
          id: LAYER_ID,
          type: "line",
          source: SOURCE_ID,
          "source-layer": "sequence",
          layout: {
            "line-join": "round",
            "line-cap": "round",
            visibility: "none",
          },
          paint: {
            "line-color": "#05cb63",
            "line-width": 2,
            "line-opacity": 0.8,
          },
        });
      }

      if (!map.getLayer("mapillary-images")) {
        map.addLayer({
          id: "mapillary-images",
          type: "circle",
          source: SOURCE_ID,
          "source-layer": "image",
          layout: {
            visibility: "none",
          },
          paint: {
            "circle-radius": 4,
            "circle-color": "#05cb63",
            "circle-opacity": 0.9,
            "circle-stroke-width": 1,
            "circle-stroke-color": "#ffffff",
          },
        });
      }
    };

    if (map.isStyleLoaded()) {
      setup();
    } else {
      map.once("style.load", setup);
    }

    const handleImageClick = (e: maplibregl.MapLayerMouseEvent) => {
      const features = e.features;
      if (!features || features.length === 0) return;
      const feature = features[0];
      const imageId = feature.properties?.id as string | undefined;
      if (imageId) {
        setSelectedImageId(imageId);
      }
    };

    map.on("click", "mapillary-images", handleImageClick);

    const handleMouseEnter = () => {
      map.getCanvas().style.cursor = "pointer";
    };
    const handleMouseLeave = () => {
      map.getCanvas().style.cursor = "";
    };

    map.on("mouseenter", "mapillary-images", handleMouseEnter);
    map.on("mouseleave", "mapillary-images", handleMouseLeave);

    return () => {
      map.off("click", "mapillary-images", handleImageClick);
      map.off("mouseenter", "mapillary-images", handleMouseEnter);
      map.off("mouseleave", "mapillary-images", handleMouseLeave);

      if (map.getLayer("mapillary-images")) map.removeLayer("mapillary-images");
      if (map.getLayer(LAYER_ID)) map.removeLayer(LAYER_ID);
      if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID);
    };
  }, [map, setSelectedImageId]);

  useEffect(() => {
    if (!map || !map.isStyleLoaded()) return;

    const visibility = isStreetViewEnabled ? "visible" : "none";

    if (map.getLayer(LAYER_ID)) {
      map.setLayoutProperty(LAYER_ID, "visibility", visibility);
    }
    if (map.getLayer("mapillary-images")) {
      map.setLayoutProperty("mapillary-images", "visibility", visibility);
    }
  }, [map, isStreetViewEnabled]);

  return null;
}

export default StreetCoverage;
