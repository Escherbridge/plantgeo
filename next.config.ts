import type { NextConfig } from "next";

/** Routes whose URL carries a live single-use credential. */
const TOKEN_BEARING_SOURCES = [
  "/invite/:path*",
  "/join/:path*",
  "/verify-email",
  "/reset-password",
];

/** Negative lookahead matching every path the list above does not cover. */
const NON_TOKEN_BEARING_SOURCE =
  "/((?!invite$|invite/|join$|join/|verify-email$|reset-password$).*)";

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
  turbopack: {
    resolveAlias: {
      "maplibre-gl$": "maplibre-gl/dist/maplibre-gl",
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
  // Baseline response headers. Deliberately no Content-Security-Policy yet:
  // the map stack needs worker/blob/wasm sources and a wrong policy breaks
  // rendering outright, so that is tracked as its own change.
  async headers() {
    const baseline = [
      { key: "X-Content-Type-Options", value: "nosniff" },
      // HSTS is only honored over TLS, and asserting it in dev is noise.
      ...(process.env.NODE_ENV === "production"
        ? [
            {
              key: "Strict-Transport-Security",
              value: "max-age=63072000; includeSubDomains",
            },
          ]
        : []),
    ];

    return [
      // "/(.*)" rather than "/:path*": the latter does not match "/" itself.
      { source: "/(.*)", headers: baseline },
      {
        // /embed is meant to be iframed by third parties; nothing else is.
        source: "/((?!embed$|embed/).*)",
        headers: [{ key: "X-Frame-Options", value: "DENY" }],
      },
      // Single-use tokens live in these URLs, so the path must never reach a
      // third party through the Referer header.
      ...TOKEN_BEARING_SOURCES.map((source) => ({
        source,
        headers: [{ key: "Referrer-Policy", value: "no-referrer" }],
      })),
      {
        source: NON_TOKEN_BEARING_SOURCE,
        headers: [
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
    ];
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
