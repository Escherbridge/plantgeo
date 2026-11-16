import type { Metadata } from "next";
import type { ReactNode } from "react";
import { PRIVATE_ROUTE_ROBOTS } from "@/lib/public-site";

export const metadata: Metadata = {
  robots: PRIVATE_ROUTE_ROBOTS,
};

export default function DashboardLayout({ children }: { children: ReactNode }) {
  return children;
}
