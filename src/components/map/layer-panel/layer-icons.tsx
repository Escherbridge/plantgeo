"use client";

import {
  BarChart3,
  Bell,
  Building2,
  CloudSun,
  Droplets,
  Flame,
  FlameKindling,
  Layers,
  Leaf,
  Mountain,
  RadioTower,
  ShieldAlert,
  Sprout,
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
 * The glyph each dock section carries.
 *
 * Inherited from the icon rail this dock replaced, so a reader who knew the rail's seven
 * buttons finds the same seven marks in the same order down the dock. Exhaustive over
 * `DockSectionKey`, so a new section fails to compile here rather than rendering a blank
 * gutter beside its name.
 */
const DOCK_SECTION_ICON_COMPONENTS: Record<DockSectionKey, LucideIcon> = {
  fire: Flame,
  water: Droplets,
  vegetation: Leaf,
  soil: Mountain,
  climate: CloudSun,
  community: Users,
  team: Building2,
  analytics: BarChart3,
  alerts: Bell,
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
