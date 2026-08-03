import {
  fetchBoundedJson,
  fetchBoundedText,
  UpstreamHttpError,
} from "@/lib/server/http/bounded-upstream";

export interface WebEvidenceResult {
  title: string;
  url: string;
  /** Pre-trimmed excerpt. Never the full page — token cost is the binding constraint. */
  snippet: string;
}

export interface WebEvidenceSearchOptions {
  maxResults?: number;
  signal?: AbortSignal;
}

/**
 * Swappable search backend. Jina is the configured default; Brave, Tavily, and
 * Exa all satisfy this shape, so switching vendors touches only this file.
 */
export interface WebEvidenceProvider {
  readonly name: string;
  search(
    query: string,
    options?: WebEvidenceSearchOptions
  ): Promise<WebEvidenceResult[]>;
}

export class WebEvidenceUnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WebEvidenceUnavailableError";
  }
}

const SEARCH_TIMEOUT_MS = 12_000;
const SEARCH_MAX_BYTES = 512 * 1024;
const DEFAULT_MAX_RESULTS = 4;
const MAX_RESULTS_CEILING = 8;
/** ~250 tokens per result keeps a 4-result search near 1k tokens of context. */
const SNIPPET_MAX_CHARS = 1_000;
const READER_TIMEOUT_MS = 15_000;
const READER_MAX_BYTES = 1024 * 1024;
const READER_MAX_CHARS = 6_000;

function clampResultCount(requested: number | undefined): number {
  if (!Number.isInteger(requested) || requested === undefined) {
    return DEFAULT_MAX_RESULTS;
  }
  return Math.min(Math.max(requested, 1), MAX_RESULTS_CEILING);
}

/** Collapses whitespace and hard-truncates so one bad page cannot blow the budget. */
export function trimForContext(text: string, maxChars: number): string {
  const collapsed = text.replace(/\s+/g, " ").trim();
  return collapsed.length <= maxChars
    ? collapsed
    : `${collapsed.slice(0, maxChars)}…`;
}

interface JinaSearchItem {
  title?: unknown;
  url?: unknown;
  description?: unknown;
  content?: unknown;
}

function asJinaItems(payload: unknown): JinaSearchItem[] {
  if (!payload || typeof payload !== "object") return [];
  const data = (payload as { data?: unknown }).data;
  return Array.isArray(data) ? (data as JinaSearchItem[]) : [];
}

function asHttpsUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:"
      ? url.toString()
      : null;
  } catch {
    return null;
  }
}

/**
 * Jina reads `s.jina.ai` for ranked results and `r.jina.ai` for clean markdown
 * extraction. `X-Respond-With: no-content` keeps the search call to snippets so
 * a full page body is fetched only when the agent explicitly asks to read one.
 */
export class JinaWebEvidenceProvider implements WebEvidenceProvider {
  readonly name = "jina";

  constructor(private readonly apiKey: string) {}

  private authHeaders(extra: Record<string, string> = {}): HeadersInit {
    return {
      Authorization: `Bearer ${this.apiKey}`,
      Accept: "application/json",
      ...extra,
    };
  }

  async search(
    query: string,
    options: WebEvidenceSearchOptions = {}
  ): Promise<WebEvidenceResult[]> {
    const trimmedQuery = query.trim();
    if (!trimmedQuery) return [];

    const maxResults = clampResultCount(options.maxResults);
    const url = new URL("https://s.jina.ai/");
    url.searchParams.set("q", trimmedQuery);

    let payload: unknown;
    try {
      payload = await fetchBoundedJson(
        url,
        {
          headers: this.authHeaders({ "X-Respond-With": "no-content" }),
          signal: options.signal,
        },
        { maxBytes: SEARCH_MAX_BYTES, timeoutMs: SEARCH_TIMEOUT_MS }
      );
    } catch (error) {
      if (error instanceof UpstreamHttpError) {
        throw new WebEvidenceUnavailableError(
          `Search provider returned ${error.status}`
        );
      }
      throw new WebEvidenceUnavailableError("Search provider is unreachable");
    }

    const results: WebEvidenceResult[] = [];
    for (const item of asJinaItems(payload)) {
      const itemUrl = asHttpsUrl(item.url);
      if (!itemUrl) continue;
      const body =
        typeof item.description === "string" && item.description.trim()
          ? item.description
          : typeof item.content === "string"
            ? item.content
            : "";
      results.push({
        title:
          typeof item.title === "string" && item.title.trim()
            ? trimForContext(item.title, 160)
            : itemUrl,
        url: itemUrl,
        snippet: trimForContext(body, SNIPPET_MAX_CHARS),
      });
      if (results.length >= maxResults) break;
    }
    return results;
  }

  /** Fetches one page as markdown. Only called when a snippet is insufficient. */
  async read(url: string, signal?: AbortSignal): Promise<string> {
    const target = asHttpsUrl(url);
    if (!target) throw new WebEvidenceUnavailableError("Unsupported URL");

    try {
      const markdown = await fetchBoundedText(
        `https://r.jina.ai/${target}`,
        {
          headers: this.authHeaders({ "X-Return-Format": "markdown" }),
          signal,
        },
        { maxBytes: READER_MAX_BYTES, timeoutMs: READER_TIMEOUT_MS }
      );
      return trimForContext(markdown, READER_MAX_CHARS);
    } catch (error) {
      if (error instanceof UpstreamHttpError) {
        throw new WebEvidenceUnavailableError(
          `Reader returned ${error.status}`
        );
      }
      throw new WebEvidenceUnavailableError("Reader is unreachable");
    }
  }
}

let cachedProvider: WebEvidenceProvider | null | undefined;

/** Returns null when no search key is configured; the agent then runs offline. */
export function getWebEvidenceProvider(): WebEvidenceProvider | null {
  if (cachedProvider !== undefined) return cachedProvider;

  const apiKey = process.env.JINA_API_KEY?.trim();
  cachedProvider = apiKey ? new JinaWebEvidenceProvider(apiKey) : null;
  return cachedProvider;
}

/** Test seam — clears the memoised provider after an env change. */
export function resetWebEvidenceProvider(): void {
  cachedProvider = undefined;
}
