import { useState } from "react";
import { useInvalidateCache, usePurgeCache } from "../hooks/use-invalidate-cache";

interface Props {
  workspaceId: string;
  projectId: string;
}

export function CacheInvalidationPage({ workspaceId, projectId }: Props) {
  const [queryContains, setQueryContains] = useState("");
  const [showPurgeConfirm, setShowPurgeConfirm] = useState(false);

  const invalidateMutation = useInvalidateCache();
  const purgeMutation = usePurgeCache();

  function handleInvalidate() {
    invalidateMutation.mutate({
      workspaceId,
      projectId,
      queryContains: queryContains || undefined,
    });
  }

  function handlePurge() {
    purgeMutation.mutate({ workspaceId, projectId, confirm: true }, {
      onSuccess: () => setShowPurgeConfirm(false),
    });
  }

  return (
    <div className="space-y-6 p-6">
      <h1 className="text-2xl font-semibold">Cache Invalidation</h1>

      <div className="space-y-4 rounded border p-4">
        <h2 className="font-medium">Selective Invalidation</h2>
        <div>
          <label className="text-sm text-muted-foreground">Query Contains</label>
          <input type="text" value={queryContains} onChange={(e) => setQueryContains(e.target.value)} placeholder="Filter by query substring..." className="mt-1 w-full rounded border px-3 py-1.5 text-sm" />
        </div>
        <button onClick={handleInvalidate} disabled={invalidateMutation.isPending} className="rounded bg-primary px-4 py-2 text-sm text-primary-foreground">
          {invalidateMutation.isPending ? "Invalidating..." : "Invalidate Matching Entries"}
        </button>
        {invalidateMutation.data && (
          <p className="text-sm text-muted-foreground">{invalidateMutation.data.entriesInvalidated} entries invalidated</p>
        )}
      </div>

      <div className="space-y-4 rounded border border-destructive/50 p-4">
        <h2 className="font-medium text-destructive">Purge All</h2>
        <p className="text-sm text-muted-foreground">Permanently invalidate all cache entries for this project.</p>
        {!showPurgeConfirm ? (
          <button onClick={() => setShowPurgeConfirm(true)} className="rounded border border-destructive px-4 py-2 text-sm text-destructive">Purge All Entries</button>
        ) : (
          <div className="flex gap-2">
            <button onClick={handlePurge} disabled={purgeMutation.isPending} className="rounded bg-destructive px-4 py-2 text-sm text-destructive-foreground">
              {purgeMutation.isPending ? "Purging..." : "Confirm Purge"}
            </button>
            <button onClick={() => setShowPurgeConfirm(false)} className="rounded border px-4 py-2 text-sm">Cancel</button>
          </div>
        )}
        {purgeMutation.data && (
          <p className="text-sm text-muted-foreground">{purgeMutation.data.entriesPurged} entries purged</p>
        )}
      </div>
    </div>
  );
}
