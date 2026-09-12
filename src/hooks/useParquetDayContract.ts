"use client";

import { useEffect, useState } from "react";
import {
  withParquetDayContract,
  withParquetFieldDayContract,
  type ParquetDayRequest,
} from "@/lib/environmental/parquet-day-contract";
import type { ParquetBrowserReaderResult } from "@/lib/environmental/parquet-presentation";

/** Retain only committed, verified responses with their original request context; see AGENTS.md. */
export function useParquetDayContract<Q extends {
  data: ParquetBrowserReaderResult<unknown> | undefined;
  isPlaceholderData?: boolean;
  isSuccess?: boolean;
}>(query: Q, request: ParquetDayRequest) {
  const [accepted, setAccepted] = useState<{ data: Q["data"]; request: ParquetDayRequest } | null>(null);
  const result = withParquetDayContract(query, {
    ...request,
    retainedRequest: accepted?.data === query.data ? accepted?.request : undefined,
  });
  const { requestedDay, policy, subject, today } = request;
  useEffect(() => {
    if (query.isPlaceholderData || query.isSuccess !== true || query.data === undefined || result.temporalRefused) return;
    const originalDay = requestedDay ?? ("requestedDay" in query.data ? query.data.requestedDay : undefined);
    setAccepted((current) => current?.data === query.data ? current : {
      data: query.data,
      request: { requestedDay: originalDay, policy, subject, today: today ?? new Date().toISOString().slice(0, 10) },
    });
  }, [query.data, query.isPlaceholderData, query.isSuccess, result.temporalRefused, requestedDay, policy, subject, today]);
  return result;
}

/** The same accepted-frame rule for the soil/climate collection vocabulary. */
export function useParquetFieldDayContract<Q extends {
  data: { requestedDay: string; observedDay: string | null; availability: string } | undefined;
  isPlaceholderData?: boolean;
  isSuccess?: boolean;
}>(query: Q, requestedDay: string | undefined, subject: string) {
  const [accepted, setAccepted] = useState<{ data: Q["data"]; day: string } | null>(null);
  const result = withParquetFieldDayContract(query, requestedDay, subject,
    accepted?.data === query.data ? accepted?.day : undefined);
  useEffect(() => {
    if (query.isPlaceholderData || query.isSuccess !== true || query.data === undefined || result.temporalRefused) return;
    const data = query.data;
    setAccepted((current) => current?.data === data ? current : {
      data, day: requestedDay ?? data.requestedDay,
    });
  }, [query.data, query.isPlaceholderData, query.isSuccess, result.temporalRefused, requestedDay]);
  return result;
}
