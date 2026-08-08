/**
 * The layer panel's scroll contract, stated once.
 *
 * Every rule here is a fix for a defect the audit found on the surfaces this panel replaces,
 * and `src/__tests__/components/LayerPanel.test.tsx` asserts them against the rendered tree so
 * they cannot regress:
 *
 * 1. **Height comes from the container, never from `vh`.** The shell is `top-*`/`bottom-*`
 *    inside MapView's `relative h-full w-full`. The rail's old `max-h-[70vh]` sat inside a
 *    `100dvh`-derived shell, so on a mobile browser with a retracting toolbar the cap could
 *    exceed the box it was capping.
 * 2. **Exactly one scroller.** `min-h-0 flex-1 overflow-y-auto` on one element. The `min-h-0`
 *    is mandatory: a flex child's default `min-height` is its content, which is why every data
 *    sheet today stretches and hands you a second scrollbar inside the first.
 * 3. **Every other direct child is `shrink-0`.** This is the exact defect at the old rail --
 *    flex items with no `shrink-0` and `min-height: auto` squash toward their icon instead of
 *    overflowing, silently trading away the 44px tap target on a short viewport.
 * 4. **Never set one overflow axis alone.** Per CSS Overflow 3 §3.1, when one axis is not
 *    `visible` the other's `visible` computes to `auto` -- so `overflow-y-auto` alone makes an
 *    element a scroll container on BOTH axes. That is what put a horizontal scrollbar on a
 *    44px-wide icon column the moment a 2px-outset badge appeared. The scroller therefore
 *    carries enough inline padding that a negatively-offset decoration lands inside its
 *    padding box.
 * 5. **`overscroll-contain`,** so reaching the end of the list does not chain the scroll into
 *    the map or the document.
 */

/** The dock itself: fixed to the container's edges, and never a scroll container. */
export const PANEL_SHELL =
  "pointer-events-auto absolute bottom-4 left-0 top-20 z-10 flex w-[19rem] max-w-[calc(100vw-1rem)] " +
  "flex-col overflow-hidden rounded-r-(--radius) border border-l-0 border-(--glass-border) " +
  "bg-(--glass-bg) shadow-(--shadow-lg) [backdrop-filter:blur(var(--glass-blur))]";

/** Header, footer, and anything else that must keep its height when the list is long. */
export const PANEL_FIXED_ROW = "shrink-0";

/** The one scrolling element in the panel. `pr-1` is rule 4's landing space. */
export const PANEL_SCROLLER =
  "min-h-0 flex-1 overflow-y-auto overscroll-contain px-2 pb-2 pr-1";
