import { z } from "zod";

// --- Shared ---

export const cachedResponseSchema = z.object({
  content: z.string(),
  model: z.string(),
  tokensUsed: z.record(z.number()),
  citations: z.array(z.record(z.unknown())),
});

export const cacheMetadataSchema = z.object({
  createdAt: z.string(),
  hitCount: z.number(),
  lastHitAt: z.string().nullable(),
  ttlRemainingSeconds: z.number().nullable(),
});

export const lookupStagesSchema = z.object({
  exactMatchMs: z.number().nullable(),
  embeddingMs: z.number().nullable(),
  semanticMatchMs: z.number().nullable(),
});

// --- Lookup ---

export const cacheLookupResponseSchema = z.object({
  requestId: z.string().nullable(),
  status: z.string(),
  source: z.string().nullable(),
  cacheEntryId: z.string().nullable(),
  response: cachedResponseSchema.nullable(),
  similarityScore: z.number().nullable(),
  matchedQuery: z.string().nullable(),
  cacheMetadata: cacheMetadataSchema.nullable(),
  lookupLatencyMs: z.number(),
  stages: lookupStagesSchema.nullable(),
});

// --- Write ---

export const cacheWriteResponseSchema = z.object({
  cacheEntryId: z.string(),
  requestId: z.string().nullable(),
  status: z.string(),
  stores: z.record(z.string()),
  expiresAt: z.string().nullable(),
  createdAt: z.string(),
});

// --- Invalidation ---

export const cacheInvalidateResponseSchema = z.object({
  requestId: z.string().nullable(),
  entriesInvalidated: z.number(),
  invalidationCriteria: z.record(z.unknown()),
  createdAt: z.string(),
});

// --- Purge ---

export const cachePurgeResponseSchema = z.object({
  requestId: z.string().nullable(),
  entriesPurged: z.number(),
  scope: z.record(z.string()),
  createdAt: z.string(),
});

// --- Config ---

export const cacheConfigSchema = z.object({
  enabled: z.boolean(),
  defaultTtlSeconds: z.number(),
  semanticTtlSeconds: z.number(),
  similarityThreshold: z.number(),
  maxEntrySizeBytes: z.number(),
  eventDrivenInvalidation: z.boolean(),
  invalidationEvents: z.array(z.string()),
});

export const cacheConfigResponseSchema = z.object({
  workspaceId: z.string(),
  projectId: z.string(),
  config: cacheConfigSchema,
  updatedAt: z.string(),
  updatedBy: z.string().nullable(),
});

// --- Stats ---

export const tokensSavedSchema = z.object({
  input: z.number(),
  output: z.number(),
});

export const cacheStatsDetailSchema = z.object({
  totalLookups: z.number(),
  exactHits: z.number(),
  semanticHits: z.number(),
  misses: z.number(),
  hitRate: z.number(),
  exactHitRate: z.number(),
  semanticHitRate: z.number(),
  totalEntries: z.number(),
  estimatedCostSavedUsd: z.number(),
  estimatedTokensSaved: tokensSavedSchema,
});

export const cacheStatsResponseSchema = z.object({
  workspaceId: z.string(),
  projectId: z.string(),
  period: z.string(),
  stats: cacheStatsDetailSchema,
});

// --- Delete ---

export const cacheDeleteResponseSchema = z.object({
  cacheEntryId: z.string(),
  status: z.string(),
});

// --- Inferred types ---

export type CachedResponse = z.infer<typeof cachedResponseSchema>;
export type CacheMetadata = z.infer<typeof cacheMetadataSchema>;
export type CacheLookupResponse = z.infer<typeof cacheLookupResponseSchema>;
export type CacheWriteResponse = z.infer<typeof cacheWriteResponseSchema>;
export type CacheInvalidateResponse = z.infer<typeof cacheInvalidateResponseSchema>;
export type CachePurgeResponse = z.infer<typeof cachePurgeResponseSchema>;
export type CacheConfig = z.infer<typeof cacheConfigSchema>;
export type CacheConfigResponse = z.infer<typeof cacheConfigResponseSchema>;
export type CacheStatsDetail = z.infer<typeof cacheStatsDetailSchema>;
export type CacheStatsResponse = z.infer<typeof cacheStatsResponseSchema>;
export type CacheDeleteResponse = z.infer<typeof cacheDeleteResponseSchema>;
export type TokensSaved = z.infer<typeof tokensSavedSchema>;
