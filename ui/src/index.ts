// Pages
export { CacheDashboardPage } from "./pages/CacheDashboardPage";
export { CacheEntriesPage } from "./pages/CacheEntriesPage";
export { CacheConfigPage } from "./pages/CacheConfigPage";
export { CacheInvalidationPage } from "./pages/CacheInvalidationPage";

// Hooks
export { useCacheStats } from "./hooks/use-cache-stats";
export { useCacheLookup } from "./hooks/use-cache-entries";
export { useCacheConfig, useUpdateCacheConfig } from "./hooks/use-cache-config";
export { useInvalidateCache, usePurgeCache } from "./hooks/use-invalidate-cache";

// API client
export { configure } from "./api/client";

// Routes & navigation
export { routes, type RouteConfig } from "./routes";
export { navMetadata } from "./nav";

// Types
export type {
  CachedResponse,
  CacheConfig,
  CacheConfigResponse,
  CacheDeleteResponse,
  CacheInvalidateResponse,
  CacheLookupResponse,
  CacheMetadata,
  CachePurgeResponse,
  CacheStatsDetail,
  CacheStatsResponse,
  CacheWriteResponse,
  TokensSaved,
} from "./types/schemas";
