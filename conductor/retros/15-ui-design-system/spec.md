# Track 15: UI Design System & UX

## Goal
Build a cohesive, dark-mode-first design system optimized for geospatial applications with responsive layouts, accessible components, and seamless map-UI integration.

## Features
1. **Design Tokens**
   - Dark mode color palette (slate/emerald/amber)
   - Light mode variant
   - Typography scale
   - Spacing scale
   - Glassmorphism variables for map overlays

2. **Core Components**
   - Button (primary, secondary, ghost, icon)
   - Input (text, number, select, range slider)
   - Dialog/Modal
   - Dropdown menu
   - Tabs
   - Accordion
   - Toast notifications
   - Sheet/Drawer (mobile panels)
   - Popover
   - Command palette (Cmd+K)
   - Tooltip

3. **Map-Specific Components**
   - Floating toolbar (glassmorphic, map overlay)
   - Side panel (collapsible, resizable)
   - Bottom sheet (mobile)
   - Map popup card
   - Coordinate display
   - Zoom level indicator
   - Loading overlay (map-aware)

4. **Layout System**
   - Full-bleed map with overlay panels
   - Split view (map + data panels)
   - Dashboard grid layout
   - Mobile-first responsive breakpoints

5. **Animations**
   - Panel slide in/out
   - Map control transitions
   - Data loading skeletons
   - Toast entrance/exit

## Files to Create/Modify
- `src/components/ui/button.tsx` - Button variants
- `src/components/ui/input.tsx` - Input components
- `src/components/ui/dialog.tsx` - Modal dialog
- `src/components/ui/sheet.tsx` - Side/bottom sheet
- `src/components/ui/dropdown-menu.tsx` - Dropdown
- `src/components/ui/tabs.tsx` - Tab navigation
- `src/components/ui/toast.tsx` - Notifications
- `src/components/ui/command.tsx` - Command palette
- `src/components/ui/popover.tsx` - Popover
- `src/components/layout/MapLayout.tsx` - Map + panels layout
- `src/components/layout/SidePanel.tsx` - Resizable side panel
- `src/components/layout/BottomSheet.tsx` - Mobile bottom sheet
- `src/lib/utils.ts` - cn() utility (clsx + tailwind-merge)

## Acceptance Criteria
- [ ] Dark mode looks polished on map overlay
- [ ] Light mode toggle works throughout
- [ ] All components are keyboard accessible
- [ ] Mobile layout uses bottom sheet for panels
- [ ] Glassmorphic overlays work over map
- [ ] Panel resize works smoothly
- [ ] Toast notifications display correctly
- [ ] Command palette (Cmd+K) opens and navigates
