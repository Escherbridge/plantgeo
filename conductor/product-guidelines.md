# PlantGeo - Product Guidelines

## Brand Identity

**Positioning:** Enterprise-grade open-source geospatial platform — credible, capable, and developer-first.

**Core Attributes:**
- **Authoritative** — data is accurate, real-time, and spatially precise
- **Transparent** — open-source by design, no hidden lock-in
- **Performant** — 3D rendering, large datasets, low latency
- **Composable** — modular layers, self-hostable, API-first

---

## Visual Design Principles

### Color
- Dark-mode primary — maps read better on dark backgrounds; UI follows suit
- Accent: electric teal (#00D4FF range) for interactive elements and highlights
- Semantic colors for data layers (fire = amber/red, water = blue, vegetation = green)
- Minimal chrome — UI should fade away so the map is the hero

### Typography
- Monospace for coordinates, technical values, and code
- Sans-serif (system stack or Inter) for UI labels and panels
- Small, dense information hierarchy — maximize map viewport

### Layout
- Map-first: full-viewport map with collapsible panels
- Panels slide in from left (layers) and right (detail/routing)
- Bottom sheet on mobile
- No modals over the map — use inline panels

### Iconography
- Lucide React — consistent stroke weight
- Map pins and layer icons use semantic colors from data context
- Avoid decorative icons; every icon must have a functional purpose

---

## UX Principles

1. **Zero friction to first map** — app loads with a working map before any auth
2. **Progressive disclosure** — advanced controls hidden until needed
3. **Keyboard + API accessible** — power users and developers are first-class
4. **Responsive but map-first** — mobile support via bottom sheet, not desktop shrink
5. **Feedback on every action** — loading states, error toasts, success confirmations for all async ops

---

## Developer Experience Guidelines

- All public-facing components documented with usage examples
- tRPC procedures typed end-to-end — no `any` at API boundaries
- Geospatial queries always go through PostGIS, never client-side computation for large datasets
- Tile endpoints cacheable and versioned
- Environment parity: Docker Compose local = Railway Pro production

---

## Content & Messaging

- Tone: direct, technical, no marketing fluff
- Comparisons to Google Maps are factual and feature-based, not hyperbolic
- Docs and error messages written for developers
- README and PLAYBOOK targeted at self-hosters and contributors
