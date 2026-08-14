"use client";

import {
  BarChart3,
  Bell,
  Building2,
  CloudRain,
  CloudSun,
  Droplets,
  Flame,
  FlameKindling,
  Gauge,
  HardDrive,
  Layers,
  Leaf,
  Mountain,
  RadioTower,
  Search,
  ShieldAlert,
  Sprout,
  Sun,
  SunMoon,
  Thermometer,
  Users,
  Waves,
  Wind,
  type LucideIcon,
} from "lucide-react";
import type { DockSectionKey } from "@/components/map/layer-panel/dock-sections";
import type { LayerIconName } from "@/lib/map/layer-registry";

/**
 * The one place a registry `LayerIconName` becomes a component.
 *
 * Exhaustive over the union by its `Record` type, so adding an icon name to the registry
 * fails to compile here rather than rendering a blank gutter. The registry itself stays free
 * of React: it is imported by stores, by `layers.ts` and by node-run tests.
 */
const LAYER_ICON_COMPONENTS: Record<LayerIconName, LucideIcon> = {
  flame: Flame,
  "flame-kindling": FlameKindling,
  "shield-alert": ShieldAlert,
  droplets: Droplets,
  waves: Waves,
  wind: Wind,
  "radio-tower": RadioTower,
  "cloud-sun": CloudSun,
  // The three added with the nine climate rows on 2026-08-10. Nine rows in one dock group are
  // told apart by their glyphs before their labels are read, so precipitation, humidity and
  // solar radiation each got their own rather than sharing the section's cloud.
  "cloud-rain": CloudRain,
  sun: Sun,
  gauge: Gauge,
  leaf: Leaf,
  mountain: Mountain,
  layers: Layers,
  thermometer: Thermometer,
  sprout: Sprout,
  users: Users,
  "building-2": Building2,
};

/** Decorative by design: the row's text label is what names the layer. */
export function LayerIcon({ name, className }: { name: LayerIconName; className?: string }) {
  const Icon = LAYER_ICON_COMPONENTS[name];
  return <Icon aria-hidden="true" className={className} />;
}

/**
 * The glyph each manager section carries.
 *
 * Inherited from the icon rail this panel replaced, so a reader who knew the rail's seven
 * buttons finds the same seven marks in the same order down the column. Exhaustive over
 * `DockSectionKey`, so a new section fails to compile here rather than rendering a blank
 * gutter beside its name.
 */
const DOCK_SECTION_ICON_COMPONENTS: Record<DockSectionKey, LucideIcon> = {
  // The magnifier the floating search field wore before it became this section, so the
  // control a reader is looking for is marked the way they last saw it.
  search: Search,
  // No "time" glyph. That section held one scrubber for the whole map and was replaced on
  // 2026-08-09 by a slider on every layer row, so there is no map-wide date section left to mark
  // -- and a calendar in this gutter would advertise one.
  //
  // Render mode -- basemap, terrain, globe, tilt -- is how the same data is LIT, which is the
  // one thing every control in that section has in common.
  view: SunMoon,
  fire: Flame,
  water: Droplets,
  vegetation: Leaf,
  soil: Mountain,
  climate: CloudSun,
  community: Users,
  team: Building2,
  analytics: BarChart3,
  alerts: Bell,
  // Carried over from the bottom-right floating toggle it replaced, so a reader who knew that
  // corner finds the same mark in the dock.
  offline: HardDrive,
  Basemap: Layers,
};

/** Decorative by design: the section's text label is what names it. */
export function DockSectionIcon({
  sectionKey,
  className,
}: {
  sectionKey: DockSectionKey;
  className?: string;
}) {
  const Icon = DOCK_SECTION_ICON_COMPONENTS[sectionKey];
  return <Icon aria-hidden="true" className={className} />;
}
