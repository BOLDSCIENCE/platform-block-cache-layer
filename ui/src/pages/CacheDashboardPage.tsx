import { useState } from "react";
import { useCacheStats } from "../hooks/use-cache-stats";

interface Props {
  workspaceId: string;
  projectId: string;
}

export function CacheDashboardPage({ workspaceId, projectId }: Props) {
  const [period, setPeriod] = useState("24h");
  const { data, isLoading, error } = useCacheStats(workspaceId, projectId, period);

  if (isLoading) return <div className="p-6 text-sm text-muted-foreground">Loading stats...</div>;
  if (error) return <div className="p-6 text-sm text-destructive">Error: {error.message}</div>;
  if (!data) return null;

  const { stats } = data;

  return (
    <div className="space-y-6 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Cache Dashboard</h1>
        <select
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
          className="rounded border px-3 py-1.5 text-sm"
        >
          <option value="1h">Last Hour</option>
          <option value="24h">Last 24 Hours</option>
          <option value="7d">Last 7 Days</option>
          <option value="30d">Last 30 Days</option>
        </select>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Hit Rate" value={`${(stats.hitRate * 100).toFixed(1)}%`} />
        <StatCard label="Total Lookups" value={stats.totalLookups.toLocaleString()} />
        <StatCard label="Total Entries" value={stats.totalEntries.toLocaleString()} />
        <StatCard label="Cost Saved" value={`$${stats.estimatedCostSavedUsd.toFixed(2)}`} />
      </div>

      <div className="grid grid-cols-3 gap-4">
        <StatCard label="Exact Hits" value={stats.exactHits.toLocaleString()} sub={`${(stats.exactHitRate * 100).toFixed(1)}%`} />
        <StatCard label="Semantic Hits" value={stats.semanticHits.toLocaleString()} sub={`${(stats.semanticHitRate * 100).toFixed(1)}%`} />
        <StatCard label="Misses" value={stats.misses.toLocaleString()} />
      </div>

      <div className="rounded border p-4">
        <h2 className="mb-2 font-medium">Tokens Saved</h2>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-muted-foreground">Input: </span>
            {stats.estimatedTokensSaved.input.toLocaleString()}
          </div>
          <div>
            <span className="text-muted-foreground">Output: </span>
            {stats.estimatedTokensSaved.output.toLocaleString()}
          </div>
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded border p-4">
      <div className="text-sm text-muted-foreground">{label}</div>
      <div className="text-2xl font-semibold">{value}</div>
      {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}
