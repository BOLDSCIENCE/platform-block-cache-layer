import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { cacheStatsResponseSchema, type CacheStatsResponse } from "../types/schemas";

export function useCacheStats(workspaceId: string, projectId: string, period = "24h") {
  return useQuery<CacheStatsResponse>({
    queryKey: ["cache-stats", workspaceId, projectId, period],
    queryFn: () =>
      api.get("/v1/cache/stats", cacheStatsResponseSchema, {
        workspace_id: workspaceId,
        project_id: projectId,
        period,
      }),
    enabled: !!workspaceId && !!projectId,
  });
}
