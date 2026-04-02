import { useState } from "react";
import { useCacheLookup } from "../hooks/use-cache-entries";

interface Props {
  workspaceId: string;
  projectId: string;
}

export function CacheEntriesPage({ workspaceId, projectId }: Props) {
  const [query, setQuery] = useState("");
  const lookupMutation = useCacheLookup();

  function handleSearch() {
    if (!query.trim()) return;
    lookupMutation.mutate({ workspaceId, projectId, query });
  }

  return (
    <div className="space-y-6 p-6">
      <h1 className="text-2xl font-semibold">Cache Entries</h1>

      <div className="flex gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          placeholder="Search cache by query..."
          className="flex-1 rounded border px-3 py-1.5 text-sm"
        />
        <button onClick={handleSearch} disabled={lookupMutation.isPending} className="rounded bg-primary px-4 py-2 text-sm text-primary-foreground">
          {lookupMutation.isPending ? "Searching..." : "Search"}
        </button>
      </div>

      {lookupMutation.data && (
        <div className="rounded border p-4">
          <div className="mb-2 flex items-center gap-2">
            <span className={`rounded px-2 py-0.5 text-xs font-medium ${lookupMutation.data.status === "hit" ? "bg-green-100 text-green-800" : "bg-yellow-100 text-yellow-800"}`}>
              {lookupMutation.data.status.toUpperCase()}
            </span>
            {lookupMutation.data.source && (
              <span className="text-xs text-muted-foreground">via {lookupMutation.data.source}</span>
            )}
            <span className="text-xs text-muted-foreground">{lookupMutation.data.lookupLatencyMs.toFixed(1)}ms</span>
          </div>

          {lookupMutation.data.response && (
            <div className="mt-2">
              <div className="text-sm font-medium">Cached Response</div>
              <pre className="mt-1 max-h-60 overflow-auto rounded bg-muted p-3 text-xs">
                {lookupMutation.data.response.content}
              </pre>
              {lookupMutation.data.response.model && (
                <div className="mt-1 text-xs text-muted-foreground">Model: {lookupMutation.data.response.model}</div>
              )}
            </div>
          )}

          {lookupMutation.data.cacheMetadata && (
            <div className="mt-2 text-xs text-muted-foreground">
              Hits: {lookupMutation.data.cacheMetadata.hitCount} | Created: {lookupMutation.data.cacheMetadata.createdAt}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
