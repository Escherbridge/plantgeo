interface InspectionState {
  suppressedLayers: Set<string>;
  listeners: Set<() => void>;
}

const states = new WeakMap<object, InspectionState>();

function stateFor(map: object): InspectionState {
  let state = states.get(map);
  if (!state) {
    state = { suppressedLayers: new Set(), listeners: new Set() };
    states.set(map, state);
  }
  return state;
}

/** Excludes invalidated native features while serialized source clearing is pending. */
export function isScalarFieldInspectionAllowed(map: object, layerId: string): boolean {
  return !states.get(map)?.suppressedLayers.has(layerId);
}

/** Publishes immediate inspection invalidation without mutating MapLibre layout. */
export function setScalarFieldInspectionSuppressed(map: object, layerIds: readonly string[], suppressed: boolean): void {
  const state = stateFor(map);
  let changed = false;
  for (const layerId of layerIds) {
    if (state.suppressedLayers.has(layerId) === suppressed) continue;
    if (suppressed) state.suppressedLayers.add(layerId);
    else state.suppressedLayers.delete(layerId);
    changed = true;
  }
  if (changed) for (const listener of state.listeners) listener();
}

/** Subscribes existing hover and pinned captions to synchronous invalidation. */
export function subscribeScalarFieldInspection(map: object, listener: () => void): () => void {
  const state = stateFor(map);
  state.listeners.add(listener);
  return () => { state.listeners.delete(listener); };
}
