import { z } from "zod";

// --- Shared ---

export const cachedResponseSchema = z.object({
  content: z.string(),
  model: z.string().default(""),
  tokensUsed: z.record(z.number()).default({}),
  citations: z.array(z.record(z.unknown())).default([]),
});

export const cacheMetadataSchema = z.object({
  createdAt: z.string(),
  hitCount: z.number(),
  lastHitAt: z.string().nullable().default(null),
  ttlRemainingSeconds: z.number().nullable().default(null),
});

export const lookupStagesSchema = z.object({
  exactMatchMs: z.number().nullable().default(null),
  embeddingMs: z.number().nullable().default(null),
  semanticMatchMs: z.number().nullable().default(null),
});

// --- Lookup ---

export const cacheLookupResponseSchema = z.object({
  requestId: z.string().nullable().default(null),
  status: z.string(),
  source: z.string().nullable().default(null),
  cacheEntryId: z.string().nullable().default(null),
  response: cachedResponseSchema.nullable().default(null),
  similarityScore: z.number().nullable().default(null),
  matchedQuery: z.string().nullable().default(null),
  cacheMetadata: cacheMetadataSchema.nullable().default(null),
  lookupLatencyMs: z.number().default(0),
  stages: lookupStagesSchema.nullable().default(null),
});

// --- Write ---

export const cacheWriteResponseSchema = z.object({
  cacheEntryId: z.string(),
  requestId: z.string().nullable().default(null),
  status: z.string().default("written"),
  stores: z.record(z.string()).default({}),
  expiresAt: z.string().nullable().default(null),
  createdAt: z.string().default(""),
});

// --- Invalidation ---

export const cacheInvalidateResponseSchema = z.object({
  requestId: z.string().nullable().default(null),
  entriesInvalidated: z.number(),
  invalidationCriteria: z.record(z.unknown()).default({}),
  createdAt: z.string().default(""),
});

// --- Purge ---

export const cachePurgeResponseSchema = z.object({
  requestId: z.string().nullable().default(null),
  entriesPurged: z.number(),
  scope: z.record(z.string()).default({}),
  createdAt: z.string().default(""),
});

// --- Config ---

export const cacheConfigSchema = z.object({
  enabled: z.boolean().default(true),
  defaultTtlSeconds: z.number().default(86400),
  semanticTtlSeconds: z.number().default(3600),
  similarityThreshold: z.number().default(0.92),
  maxEntrySizeBytes: z.number().default(102400),
  eventDrivenInvalidation: z.boolean().default(true),
  invalidationEvents: z.array(z.string()).default([]),
});

export const cacheConfigResponseSchema = z.object({
  workspaceId: z.string(),
  projectId: z.string(),
  config: cacheConfigSchema,
  updatedAt: z.string().default(""),
  updatedBy: z.string().nullable().default(null),
});

// --- Stats ---

export const tokensSavedSchema = z.object({
  input: z.number().default(0),
  output: z.number().default(0),
});

export const cacheStatsDetailSchema = z.object({
  totalLookups: z.number().default(0),
  exactHits: z.number().default(0),
  semanticHits: z.number().default(0),
  misses: z.number().default(0),
  hitRate: z.number().default(0),
  exactHitRate: z.number().default(0),
  semanticHitRate: z.number().default(0),
  totalEntries: z.number().default(0),
  estimatedCostSavedUsd: z.number().default(0),
  estimatedTokensSaved: tokensSavedSchema.default({}),
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
  status: z.string().default("invalidated"),
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
