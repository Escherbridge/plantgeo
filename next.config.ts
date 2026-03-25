import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Required for Docker/Railway deployment — produces a self-contained server
  // in .next/standalone that the Dockerfile copies into the runtime image.
  output: "standalone",
  // Resolve workspace root to this project (avoids parent lockfile confusion)
  outputFileTracingRoot: __dirname,
  experimental: {
    serverActions: {
      bodySizeLimit: "10mb",
    },
  },
  // Required for maplibre-gl WebGL rendering — alias only the bare specifier
  // so CSS imports like "maplibre-gl/dist/maplibre-gl.css" are not rewritten.
  webpack: (config) => {
    config.resolve.alias = {
      ...config.resolve.alias,
      "maplibre-gl$": "maplibre-gl/dist/maplibre-gl",
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
