import { useMutation } from "@tanstack/react-query";
import { api } from "../api/client";
import { cacheLookupResponseSchema, type CacheLookupResponse } from "../types/schemas";

/** Lookup a specific cache entry by query (for browsing/search). */
export function useCacheLookup() {
  return useMutation<
    CacheLookupResponse,
    Error,
    { workspaceId: string; projectId: string; query: string }
  >({
    mutationFn: ({ workspaceId, projectId, query }) =>
      api.post("/v1/cache/lookup", cacheLookupResponseSchema, {
        workspaceId,
        projectId,
        query,
        lookupConfig: { enableExactMatch: true, enableSemantic: true },
      }),
  });
}
