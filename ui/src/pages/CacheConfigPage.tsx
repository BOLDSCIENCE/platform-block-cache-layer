import { useState } from "react";
import { useCacheConfig, useUpdateCacheConfig } from "../hooks/use-cache-config";
import type { CacheConfig } from "../types/schemas";

interface Props {
  workspaceId: string;
  projectId: string;
}

export function CacheConfigPage({ workspaceId, projectId }: Props) {
  const { data, isLoading, error } = useCacheConfig(workspaceId, projectId);
  const updateMutation = useUpdateCacheConfig(workspaceId, projectId);
  const [draft, setDraft] = useState<CacheConfig | null>(null);

  if (isLoading) return <div className="p-6 text-sm text-muted-foreground">Loading config...</div>;
  if (error) return <div className="p-6 text-sm text-destructive">Error: {error.message}</div>;
  if (!data) return null;

  const config = draft ?? data.config;

  function update(patch: Partial<CacheConfig>) {
    setDraft({ ...config, ...patch });
  }

  function handleSave() {
    if (!draft) return;
    updateMutation.mutate(draft, { onSuccess: () => setDraft(null) });
  }

  return (
    <div className="space-y-6 p-6">
      <h1 className="text-2xl font-semibold">Cache Configuration</h1>

      <div className="space-y-4 rounded border p-4">
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={config.enabled} onChange={(e) => update({ enabled: e.target.checked })} />
          <span>Caching Enabled</span>
        </label>

        <div className="grid grid-cols-2 gap-4">
          <Field label="Default TTL (seconds)" type="number" value={config.defaultTtlSeconds} onChange={(v) => update({ defaultTtlSeconds: Number(v) })} />
          <Field label="Semantic TTL (seconds)" type="number" value={config.semanticTtlSeconds} onChange={(v) => update({ semanticTtlSeconds: Number(v) })} />
          <Field label="Similarity Threshold" type="number" value={config.similarityThreshold} onChange={(v) => update({ similarityThreshold: Number(v) })} />
          <Field label="Max Entry Size (bytes)" type="number" value={config.maxEntrySizeBytes} onChange={(v) => update({ maxEntrySizeBytes: Number(v) })} />
        </div>

        <label className="flex items-center gap-2">
          <input type="checkbox" checked={config.eventDrivenInvalidation} onChange={(e) => update({ eventDrivenInvalidation: e.target.checked })} />
          <span>Event-Driven Invalidation</span>
        </label>
      </div>

      {draft && (
        <div className="flex gap-2">
          <button onClick={handleSave} disabled={updateMutation.isPending} className="rounded bg-primary px-4 py-2 text-sm text-primary-foreground">
            {updateMutation.isPending ? "Saving..." : "Save Changes"}
          </button>
          <button onClick={() => setDraft(null)} className="rounded border px-4 py-2 text-sm">Cancel</button>
        </div>
      )}

      {data.updatedAt && (
        <p className="text-xs text-muted-foreground">Last updated: {data.updatedAt} by {data.updatedBy ?? "unknown"}</p>
      )}
    </div>
  );
}

function Field({ label, type, value, onChange }: { label: string; type: string; value: number; onChange: (v: string) => void }) {
  return (
    <div>
      <label className="text-sm text-muted-foreground">{label}</label>
      <input type={type} value={value} onChange={(e) => onChange(e.target.value)} className="mt-1 w-full rounded border px-3 py-1.5 text-sm" step={type === "number" ? "any" : undefined} />
    </div>
  );
}
