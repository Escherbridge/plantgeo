"use client";

import { useState, useEffect, useRef, useCallback } from "react";

export type WSConnectionState = "connecting" | "open" | "closed";

interface UseWebSocketOptions {
  onMessage?: (data: unknown) => void;
  heartbeatIntervalMs?: number;
}

interface UseWebSocketResult {
  connectionState: WSConnectionState;
  send: (data: unknown) => void;
}

const MAX_BACKOFF_MS = 30_000;

export function useWebSocket(
  url: string,
  options?: UseWebSocketOptions
): UseWebSocketResult {
  const [connectionState, setConnectionState] =
    useState<WSConnectionState>("connecting");

  const wsRef = useRef<WebSocket | null>(null);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const heartbeatTimerRef = useRef<ReturnType<typeof setInterval>>(undefined);
  const onMessageRef = useRef(options?.onMessage);
  onMessageRef.current = options?.onMessage;
  const heartbeatIntervalMs = options?.heartbeatIntervalMs ?? 15_000;

  const clearHeartbeat = useCallback(() => {
    clearInterval(heartbeatTimerRef.current);
  }, []);

  const startHeartbeat = useCallback(
    (ws: WebSocket) => {
      clearHeartbeat();
      heartbeatTimerRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "ping" }));
        }
      }, heartbeatIntervalMs);
    },
    [clearHeartbeat, heartbeatIntervalMs]
  );

  const connect = useCallback(() => {
    setConnectionState("connecting");
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      retryCountRef.current = 0;
      setConnectionState("open");
      startHeartbeat(ws);
    };

    ws.onmessage = (event: MessageEvent) => {
      let parsed: unknown;
      try {
        parsed = JSON.parse(event.data as string);
      } catch {
        parsed = event.data;
      }
      onMessageRef.current?.(parsed);
    };

    ws.onclose = () => {
      clearHeartbeat();
      wsRef.current = null;
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

    ws.onerror = () => {
      ws.close();
    };
  }, [url, startHeartbeat, clearHeartbeat]);

  useEffect(() => {
    connect();

    return () => {
      clearTimeout(retryTimerRef.current);
      clearHeartbeat();
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [connect, clearHeartbeat]);

  const send = useCallback((data: unknown) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data));
    }
  }, []);

  return { connectionState, send };
}
