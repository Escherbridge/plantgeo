"use client";

import { useEffect } from "react";
import { useMap } from "@/lib/map/map-context";
import { trpc } from "@/lib/trpc/client";
import {
  readInterventionPublicationRevision,
  refreshPublishedInterventionSource,
  subscribeInterventionPublication,
} from "@/lib/map/intervention-publication";

/** Keep open maps and contributor outcomes current after a publication. */
export default function InterventionPublicationSync() {
  const map = useMap();
  const utils = trpc.useUtils();

  useEffect(() => {
    if (!map) return;
    let seenRevision = "";
    const refresh = () => {
      const revision = readInterventionPublicationRevision();
      refreshPublishedInterventionSource(map, revision);
      if (revision && revision !== seenRevision) {
        seenRevision = revision;
        void utils.interventions.invalidate();
        void utils.contributions.invalidate();
      }
    };
    const unsubscribe = subscribeInterventionPublication(refresh);
    map.on("style.load", refresh);
    map.on("sourcedata", refresh);
    refresh();
    return () => {
      unsubscribe();
      map.off("style.load", refresh);
      map.off("sourcedata", refresh);
    };
  }, [map, utils]);

  return null;
}
