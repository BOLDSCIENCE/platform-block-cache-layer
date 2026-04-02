import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import {
  cacheConfigResponseSchema,
  type CacheConfig,
  type CacheConfigResponse,
} from "../types/schemas";

export function useCacheConfig(workspaceId: string, projectId: string) {
  return useQuery<CacheConfigResponse>({
    queryKey: ["cache-config", workspaceId, projectId],
    queryFn: () =>
      api.get("/v1/cache/config", cacheConfigResponseSchema, {
        workspace_id: workspaceId,
        project_id: projectId,
      }),
    enabled: !!workspaceId && !!projectId,
  });
}

export function useUpdateCacheConfig(workspaceId: string, projectId: string) {
  const queryClient = useQueryClient();
  return useMutation<CacheConfigResponse, Error, CacheConfig>({
    mutationFn: (config) =>
      api.put("/v1/cache/config", cacheConfigResponseSchema, {
        workspaceId,
        projectId,
        config,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cache-config", workspaceId, projectId] });
    },
  });
}
