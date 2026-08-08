"use client";

import {
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
