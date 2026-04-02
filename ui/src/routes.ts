import type { ComponentType } from "react";
import { CacheConfigPage } from "./pages/CacheConfigPage";
import { CacheDashboardPage } from "./pages/CacheDashboardPage";
import { CacheEntriesPage } from "./pages/CacheEntriesPage";
import { CacheInvalidationPage } from "./pages/CacheInvalidationPage";

export interface RouteConfig {
  path: string;
  component: ComponentType<{ workspaceId: string; projectId: string }>;
  label: string;
}

export const routes: RouteConfig[] = [
  { path: "/cache-layer", component: CacheDashboardPage, label: "Dashboard" },
  { path: "/cache-layer/entries", component: CacheEntriesPage, label: "Entries" },
  { path: "/cache-layer/config", component: CacheConfigPage, label: "Configuration" },
  { path: "/cache-layer/invalidation", component: CacheInvalidationPage, label: "Invalidation" },
];
