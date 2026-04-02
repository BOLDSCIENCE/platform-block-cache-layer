import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import {
  cacheInvalidateResponseSchema,
  cachePurgeResponseSchema,
  type CacheInvalidateResponse,
  type CachePurgeResponse,
} from "../types/schemas";

export function useInvalidateCache() {
  const queryClient = useQueryClient();
  return useMutation<
    CacheInvalidateResponse,
    Error,
    {
      workspaceId: string;
      projectId: string;
      queryContains?: string;
      citedDocumentIds?: string[];
      createdBefore?: string;
    }
  >({
    mutationFn: ({ workspaceId, projectId, ...criteria }) =>
      api.post("/v1/cache/invalidate", cacheInvalidateResponseSchema, {
        workspaceId,
        projectId,
        invalidationCriteria: criteria,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cache-stats"] });
    },
  });
}

export function usePurgeCache() {
  const queryClient = useQueryClient();
  return useMutation<
    CachePurgeResponse,
    Error,
    { workspaceId: string; projectId?: string; confirm: boolean }
  >({
    mutationFn: ({ workspaceId, projectId, confirm }) =>
      api.post("/v1/cache/purge", cachePurgeResponseSchema, {
        workspaceId,
        projectId,
        confirm,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cache-stats"] });
    },
  });
}
