/**
 * Plan pages open on a stored bbox. ServiceAreaLayer's first fitBounds would
 * replace that camera with the regional coverage box. MapView sets this from a
 * layout effect, which runs before ServiceAreaLayer's passive effect.
 */
let held = false;

export function holdPlanCamera(next: boolean): void {
  held = next;
}

export function planCameraIsHeld(): boolean {
  return held;
}
