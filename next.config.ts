import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Required for Docker/Railway deployment — produces a self-contained server
  // in .next/standalone that the Dockerfile copies into the runtime image.
  output: "standalone",
  experimental: {
    serverActions: {
      bodySizeLimit: "10mb",
    },
  },
  // Required for maplibre-gl WebGL rendering
  webpack: (config) => {
    config.resolve.alias = {
      ...config.resolve.alias,
      "maplibre-gl": "maplibre-gl/dist/maplibre-gl",
    };
    return config;
  },
  // Allow tile/terrain image sources
  images: {
    remotePatterns: [
      { protocol: "https", hostname: "*.protomaps.com" },
      { protocol: "https", hostname: "*.maplibre.org" },
      { protocol: "https", hostname: "tile.openstreetmap.org" },
    ],
  },
};

export default nextConfig;
