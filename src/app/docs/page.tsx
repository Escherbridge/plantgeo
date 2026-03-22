import ApiTester from "./ApiTester";

export const metadata = {
  title: "API Documentation — PlantGeo",
  description: "PlantGeo public REST API documentation with interactive explorer",
};

function CodeBlock({ code }: { code: string }) {
  return (
    <pre className="overflow-x-auto rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--muted))] p-4 text-xs text-[hsl(var(--muted-foreground))]">
      {code}
    </pre>
  );
}

function Badge({ method }: { method: "GET" | "POST" | "PUT" | "DELETE" }) {
  const colors: Record<string, string> = {
    GET: "bg-blue-500/20 text-blue-400",
    POST: "bg-green-500/20 text-green-400",
    PUT: "bg-yellow-500/20 text-yellow-400",
    DELETE: "bg-red-500/20 text-red-400",
  };
  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-xs font-mono font-bold ${colors[method]}`}
    >
      {method}
    </span>
  );
}

function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-24 space-y-4">
      <h2 className="border-b border-[hsl(var(--border))] pb-2 text-xl font-semibold text-[hsl(var(--foreground))]">
        {title}
      </h2>
      {children}
    </section>
  );
}

function EndpointCard({
  method,
  path,
  description,
  params,
  responseFormat,
  curlExample,
}: {
  method: "GET" | "POST";
  path: string;
  description: string;
  params: Array<{ name: string; type: string; required: boolean; description: string }>;
  responseFormat: string;
  curlExample: string;
}) {
  return (
    <div className="rounded-lg border border-[hsl(var(--border))] p-5 space-y-4">
      <div className="flex items-center gap-3">
        <Badge method={method} />
        <code className="text-sm font-mono font-medium text-[hsl(var(--foreground))]">
          {path}
        </code>
      </div>
      <p className="text-sm text-[hsl(var(--muted-foreground))]">{description}</p>

      {params.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-semibold text-[hsl(var(--foreground))]">Parameters</h4>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[hsl(var(--border))]">
                  <th className="py-1.5 pr-4 text-left text-xs font-medium text-[hsl(var(--muted-foreground))]">
                    Name
                  </th>
                  <th className="py-1.5 pr-4 text-left text-xs font-medium text-[hsl(var(--muted-foreground))]">
                    Type
                  </th>
                  <th className="py-1.5 pr-4 text-left text-xs font-medium text-[hsl(var(--muted-foreground))]">
                    Required
                  </th>
                  <th className="py-1.5 text-left text-xs font-medium text-[hsl(var(--muted-foreground))]">
                    Description
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[hsl(var(--border))]">
                {params.map((p) => (
                  <tr key={p.name}>
                    <td className="py-1.5 pr-4">
                      <code className="text-xs font-mono text-[hsl(var(--foreground))]">
                        {p.name}
                      </code>
                    </td>
                    <td className="py-1.5 pr-4 text-xs text-[hsl(var(--muted-foreground))]">
                      {p.type}
                    </td>
                    <td className="py-1.5 pr-4 text-xs text-[hsl(var(--muted-foreground))]">
                      {p.required ? (
                        <span className="text-[hsl(var(--destructive))]">Yes</span>
                      ) : (
                        "No"
                      )}
                    </td>
                    <td className="py-1.5 text-xs text-[hsl(var(--muted-foreground))]">
                      {p.description}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="space-y-1">
        <h4 className="text-sm font-semibold text-[hsl(var(--foreground))]">
          Response Format
        </h4>
        <CodeBlock code={responseFormat} />
      </div>

      <div className="space-y-1">
        <h4 className="text-sm font-semibold text-[hsl(var(--foreground))]">
          cURL Example
        </h4>
        <CodeBlock code={curlExample} />
      </div>
    </div>
  );
}

export default function DocsPage() {
  return (
    <div className="min-h-screen bg-[hsl(var(--background))]">
      {/* Header */}
      <header className="sticky top-0 z-10 border-b border-[hsl(var(--border))] bg-[hsl(var(--background)/0.95)] backdrop-blur-sm">
        <div className="mx-auto max-w-5xl px-6 py-4 flex items-center justify-between">
          <div>
            <span className="text-lg font-bold text-[hsl(var(--foreground))]">PlantGeo</span>
            <span className="ml-2 text-sm text-[hsl(var(--muted-foreground))]">API Docs</span>
          </div>
          <nav className="hidden gap-6 text-sm sm:flex">
            <a href="#authentication" className="text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors">
              Auth
            </a>
            <a href="#features" className="text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors">
              Features
            </a>
            <a href="#layers" className="text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors">
              Layers
            </a>
            <a href="#route" className="text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors">
              Route
            </a>
            <a href="#geocode" className="text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors">
              Geocode
            </a>
            <a href="#try-it" className="text-[hsl(var(--muted-foreground))] hover:text-[hsl(var(--foreground))] transition-colors">
              Try It
            </a>
          </nav>
        </div>
      </header>

      <div className="mx-auto max-w-5xl px-6 py-12 space-y-16">
        {/* Hero */}
        <div className="space-y-3">
          <h1 className="text-3xl font-bold text-[hsl(var(--foreground))]">
            PlantGeo Public API
          </h1>
          <p className="text-[hsl(var(--muted-foreground))] max-w-2xl">
            Access geospatial features, routing, and geocoding via the PlantGeo REST API.
            All endpoints require an API key passed in the{" "}
            <code className="font-mono text-sm">x-api-key</code> header.
          </p>
          <div className="flex items-center gap-3">
            <span className="rounded-full bg-[hsl(var(--muted))] px-3 py-1 text-xs font-medium text-[hsl(var(--muted-foreground))]">
              Base URL: /api/v1
            </span>
            <span className="rounded-full bg-[hsl(var(--muted))] px-3 py-1 text-xs font-medium text-[hsl(var(--muted-foreground))]">
              JSON responses
            </span>
            <span className="rounded-full bg-[hsl(var(--muted))] px-3 py-1 text-xs font-medium text-[hsl(var(--muted-foreground))]">
              Rate limited
            </span>
          </div>
        </div>

        {/* Authentication */}
        <Section id="authentication" title="Authentication">
          <p className="text-sm text-[hsl(var(--muted-foreground))]">
            All API requests require an API key. Generate one from your account settings under
            API Keys. Pass it in the{" "}
            <code className="font-mono text-sm text-[hsl(var(--foreground))]">
              x-api-key
            </code>{" "}
            request header.
          </p>
          <CodeBlock
            code={`# All requests require this header\ncurl -H "x-api-key: pg_your_key_here" https://plantgeo.app/api/v1/layers`}
          />
          <div className="rounded-md border border-[hsl(var(--border))] p-4 space-y-2">
            <h3 className="text-sm font-semibold text-[hsl(var(--foreground))]">
              Error Responses
            </h3>
            <p className="text-xs text-[hsl(var(--muted-foreground))]">
              Missing or invalid keys return HTTP 401. Rate limit exceeded returns HTTP 429
              with a <code className="font-mono">Retry-After: 60</code> header.
            </p>
            <CodeBlock code={`// 401 Unauthorized\n{ "error": "Invalid or missing API key" }\n\n// 429 Too Many Requests\n{ "error": "Rate limit exceeded" }`} />
          </div>
          <div className="rounded-md border border-[hsl(var(--border))] p-4 space-y-2">
            <h3 className="text-sm font-semibold text-[hsl(var(--foreground))]">
              Rate Limits
            </h3>
            <p className="text-xs text-[hsl(var(--muted-foreground))]">
              Default limit is 1000 requests per minute per API key. Custom limits can be
              configured when creating a key. The routing endpoint enforces per-minute
              windowed counters via Redis.
            </p>
          </div>
        </Section>

        {/* Features endpoint */}
        <Section id="features" title="Features">
          <p className="text-sm text-[hsl(var(--muted-foreground))]">
            Query geospatial features as OGC-compliant GeoJSON FeatureCollections.
          </p>
          <EndpointCard
            method="GET"
            path="/api/v1/features"
            description="Retrieve a paginated GeoJSON FeatureCollection of features. Filter by bounding box or layer ID."
            params={[
              {
                name: "bbox",
                type: "string",
                required: false,
                description: "Bounding box filter: west,south,east,north",
              },
              {
                name: "layer_id",
                type: "UUID",
                required: false,
                description: "Filter features by layer UUID",
              },
              {
                name: "limit",
                type: "integer",
                required: false,
                description: "Max features to return (default 100, max 1000)",
              },
              {
                name: "offset",
                type: "integer",
                required: false,
                description: "Pagination offset (default 0)",
              },
            ]}
            responseFormat={`// Content-Type: application/geo+json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "id": "uuid",
      "geometry": null,
      "properties": {
        "layer_id": "uuid",
        "status": "published",
        "created_at": "2024-01-01T00:00:00Z",
        ...custom properties
      }
    }
  ],
  "numberReturned": 1,
  "numberMatched": 1
}`}
            curlExample={`curl -H "x-api-key: pg_your_key" \\
  "https://plantgeo.app/api/v1/features?limit=50&bbox=-122.5,37.7,-122.3,37.9"`}
          />
        </Section>

        {/* Layers endpoint */}
        <Section id="layers" title="Layers">
          <p className="text-sm text-[hsl(var(--muted-foreground))]">
            List all publicly available layers in the platform.
          </p>
          <EndpointCard
            method="GET"
            path="/api/v1/layers"
            description="Returns all public layers (is_public = true) with their metadata."
            params={[]}
            responseFormat={`[
  {
    "id": "uuid",
    "name": "Wildfire Perimeters",
    "type": "vector",
    "description": "Current and historical fire perimeters"
  }
]`}
            curlExample={`curl -H "x-api-key: pg_your_key" \\
  "https://plantgeo.app/api/v1/layers"`}
          />
        </Section>

        {/* Route endpoint */}
        <Section id="route" title="Routing">
          <p className="text-sm text-[hsl(var(--muted-foreground))]">
            Multi-modal routing powered by Valhalla. Supports driving, cycling, walking, and
            more. Rate limited per API key.
          </p>
          <EndpointCard
            method="POST"
            path="/api/v1/route"
            description="Proxy to Valhalla routing engine. Send a standard Valhalla route request and receive a turn-by-turn route response."
            params={[]}
            responseFormat={`// Valhalla route response
{
  "trip": {
    "locations": [...],
    "legs": [...],
    "summary": {
      "time": 3600,
      "length": 45.2
    }
  }
}`}
            curlExample={`curl -X POST \\
  -H "x-api-key: pg_your_key" \\
  -H "Content-Type: application/json" \\
  -d '{
    "locations": [
      {"lon": -122.4194, "lat": 37.7749},
      {"lon": -118.2437, "lat": 34.0522}
    ],
    "costing": "auto",
    "directions_options": {"units": "kilometers"}
  }' \\
  "https://plantgeo.app/api/v1/route"`}
          />
        </Section>

        {/* Geocode endpoint */}
        <Section id="geocode" title="Geocoding">
          <p className="text-sm text-[hsl(var(--muted-foreground))]">
            Forward geocoding powered by Photon (backed by OpenStreetMap/Nominatim).
          </p>
          <EndpointCard
            method="GET"
            path="/api/v1/geocode"
            description="Forward geocode a place name or address. Returns a GeoJSON FeatureCollection from Photon."
            params={[
              {
                name: "q",
                type: "string",
                required: true,
                description: "Search query (place name or address)",
              },
              {
                name: "limit",
                type: "integer",
                required: false,
                description: "Max results to return (default 5, max 50)",
              },
            ]}
            responseFormat={`// Photon GeoJSON FeatureCollection
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "geometry": { "type": "Point", "coordinates": [-122.4194, 37.7749] },
      "properties": {
        "name": "San Francisco",
        "country": "United States",
        "state": "California",
        "type": "city",
        "osm_id": 111968
      }
    }
  ]
}`}
            curlExample={`curl -H "x-api-key: pg_your_key" \\
  "https://plantgeo.app/api/v1/geocode?q=San+Francisco&limit=3"`}
          />
        </Section>

        {/* Try It */}
        <Section id="try-it" title="Try It">
          <p className="text-sm text-[hsl(var(--muted-foreground))]">
            Interactively test API endpoints. Enter your API key and send requests directly
            from the browser.
          </p>
          <div className="rounded-lg border border-[hsl(var(--border))] p-5">
            <ApiTester />
          </div>
        </Section>
      </div>
    </div>
  );
}
