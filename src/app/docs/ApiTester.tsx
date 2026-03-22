"use client";

import { useState } from "react";

type Endpoint = {
  id: string;
  label: string;
  method: "GET" | "POST";
  path: string;
  params: Array<{ name: string; placeholder: string; required?: boolean }>;
  bodyTemplate?: string;
};

const ENDPOINTS: Endpoint[] = [
  {
    id: "layers",
    label: "GET /api/v1/layers",
    method: "GET",
    path: "/api/v1/layers",
    params: [],
  },
  {
    id: "features",
    label: "GET /api/v1/features",
    method: "GET",
    path: "/api/v1/features",
    params: [
      { name: "bbox", placeholder: "west,south,east,north (optional)" },
      { name: "limit", placeholder: "100" },
      { name: "offset", placeholder: "0" },
      { name: "layer_id", placeholder: "UUID (optional)" },
    ],
  },
  {
    id: "geocode",
    label: "GET /api/v1/geocode",
    method: "GET",
    path: "/api/v1/geocode",
    params: [
      { name: "q", placeholder: "Search query", required: true },
      { name: "limit", placeholder: "5" },
    ],
  },
  {
    id: "route",
    label: "POST /api/v1/route",
    method: "POST",
    path: "/api/v1/route",
    params: [],
    bodyTemplate: JSON.stringify(
      {
        locations: [
          { lon: -122.4194, lat: 37.7749 },
          { lon: -118.2437, lat: 34.0522 },
        ],
        costing: "auto",
        directions_options: { units: "kilometers" },
      },
      null,
      2
    ),
  },
];

export default function ApiTester() {
  const [selectedEndpoint, setSelectedEndpoint] = useState<string>("layers");
  const [apiKey, setApiKey] = useState("");
  const [params, setParams] = useState<Record<string, string>>({});
  const [body, setBody] = useState("");
  const [response, setResponse] = useState<string | null>(null);
  const [status, setStatus] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);

  const endpoint = ENDPOINTS.find((e) => e.id === selectedEndpoint)!;

  function handleEndpointChange(id: string) {
    setSelectedEndpoint(id);
    setParams({});
    const ep = ENDPOINTS.find((e) => e.id === id);
    setBody(ep?.bodyTemplate ?? "");
    setResponse(null);
    setStatus(null);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setResponse(null);
    setStatus(null);

    try {
      let url = endpoint.path;
      if (endpoint.method === "GET") {
        const sp = new URLSearchParams();
        for (const [k, v] of Object.entries(params)) {
          if (v.trim()) sp.set(k, v.trim());
        }
        const qs = sp.toString();
        if (qs) url += `?${qs}`;
      }

      const headers: Record<string, string> = {};
      if (apiKey.trim()) headers["x-api-key"] = apiKey.trim();
      if (endpoint.method === "POST") headers["Content-Type"] = "application/json";

      const res = await fetch(url, {
        method: endpoint.method,
        headers,
        body: endpoint.method === "POST" && body ? body : undefined,
      });

      setStatus(res.status);
      const ct = res.headers.get("content-type") ?? "";
      const text = await res.text();
      try {
        setResponse(JSON.stringify(JSON.parse(text), null, 2));
      } catch {
        setResponse(text);
      }
    } catch (err) {
      setResponse(String(err));
      setStatus(0);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <label className="block text-sm font-medium text-[hsl(var(--foreground))]">
          Endpoint
        </label>
        <select
          value={selectedEndpoint}
          onChange={(e) => handleEndpointChange(e.target.value)}
          className="w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
        >
          {ENDPOINTS.map((ep) => (
            <option key={ep.id} value={ep.id}>
              {ep.label}
            </option>
          ))}
        </select>
      </div>

      <div className="space-y-1">
        <label className="block text-sm font-medium text-[hsl(var(--foreground))]">
          API Key
        </label>
        <input
          type="text"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          className="w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 text-sm font-mono text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
          placeholder="pg_..."
        />
      </div>

      {endpoint.params.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm font-medium text-[hsl(var(--foreground))]">Parameters</p>
          {endpoint.params.map((p) => (
            <div key={p.name} className="flex items-center gap-2">
              <span className="w-24 shrink-0 text-xs font-mono text-[hsl(var(--muted-foreground))]">
                {p.name}
                {p.required && (
                  <span className="ml-0.5 text-[hsl(var(--destructive))]">*</span>
                )}
              </span>
              <input
                type="text"
                value={params[p.name] ?? ""}
                onChange={(e) => setParams((prev) => ({ ...prev, [p.name]: e.target.value }))}
                placeholder={p.placeholder}
                className="flex-1 rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-1.5 text-sm text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
              />
            </div>
          ))}
        </div>
      )}

      {endpoint.method === "POST" && (
        <div className="space-y-1">
          <label className="block text-sm font-medium text-[hsl(var(--foreground))]">
            Request Body (JSON)
          </label>
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            rows={8}
            className="w-full rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--background))] px-3 py-2 font-mono text-xs text-[hsl(var(--foreground))] focus:outline-none focus:ring-2 focus:ring-[hsl(var(--ring))]"
          />
        </div>
      )}

      <button
        onClick={handleSubmit}
        disabled={loading}
        className="rounded-md bg-[hsl(var(--primary))] px-4 py-2 text-sm font-medium text-[hsl(var(--primary-foreground))] hover:opacity-90 disabled:opacity-50 transition-opacity"
      >
        {loading ? "Sending..." : "Send Request"}
      </button>

      {(response !== null || status !== null) && (
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium text-[hsl(var(--foreground))]">Response</p>
            {status !== null && (
              <span
                className={`rounded px-2 py-0.5 text-xs font-mono font-medium ${
                  status >= 200 && status < 300
                    ? "bg-green-500/20 text-green-400"
                    : "bg-red-500/20 text-red-400"
                }`}
              >
                {status}
              </span>
            )}
          </div>
          <pre className="max-h-96 overflow-auto rounded-md border border-[hsl(var(--border))] bg-[hsl(var(--muted))] p-4 text-xs text-[hsl(var(--muted-foreground))]">
            {response}
          </pre>
        </div>
      )}
    </div>
  );
}
