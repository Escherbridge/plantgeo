"use client";

import { useState, useEffect, useRef, useCallback } from "react";

export type ConnectionState = "connecting" | "open" | "closed";

interface UseSSEOptions {
  onMessage?: (data: unknown) => void;
}

interface UseSSEResult {
  data: unknown;
  connectionState: ConnectionState;
}

const MAX_BACKOFF_MS = 30_000;

export function useSSE(url: string, options?: UseSSEOptions): UseSSEResult {
  const [data, setData] = useState<unknown>(null);
  const [connectionState, setConnectionState] =
    useState<ConnectionState>("connecting");

  const esRef = useRef<EventSource | null>(null);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const onMessageRef = useRef(options?.onMessage);
  onMessageRef.current = options?.onMessage;

  const connect = useCallback(() => {
    const es = new EventSource(url);
    esRef.current = es;
    setConnectionState("connecting");

    es.onopen = () => {
      retryCountRef.current = 0;
      setConnectionState("open");
    };

    es.addEventListener("feature", (event: MessageEvent) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(event.data as string);
      } catch {
        parsed = event.data;
      }
      setData(parsed);
      onMessageRef.current?.(parsed);
    });

    es.onmessage = (event: MessageEvent) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(event.data as string);
      } catch {
        parsed = event.data;
      }
      setData(parsed);
      onMessageRef.current?.(parsed);
    };

    es.onerror = () => {
      if (esRef.current !== es) return;
      es.close();
      esRef.current = null;
      setConnectionState("closed");

      // Exponential backoff: 1s -> 2s -> 4s -> 8s -> max 30s
      const backoff = Math.min(
        1000 * Math.pow(2, retryCountRef.current),
        MAX_BACKOFF_MS
      );
      retryCountRef.current += 1;

      retryTimerRef.current = setTimeout(() => {
        connect();
      }, backoff);
    };
  }, [url]);

  useEffect(() => {
    connect();

    return () => {
      clearTimeout(retryTimerRef.current);
      esRef.current?.close();
      esRef.current = null;
    };
  }, [connect]);

  return { data, connectionState };
}
