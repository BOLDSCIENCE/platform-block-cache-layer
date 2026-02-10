# Platform Block Cache Layer

## Architecture Planning Document

**Version:** 1.0
**Status:** Draft
**Last Updated:** 2026-02-10

---

## Executive Summary

The Cache Layer is a platform block that provides intelligent response caching for AI applications, sitting directly in front of the Model Gateway to intercept repetitive queries before they incur LLM inference costs. In an enterprise environment where hundreds or thousands of users ask similar questions, the Cache Layer eliminates redundant LLM calls by returning cached responses for identical or semantically equivalent queries.

Without a cache layer, every "How do I reset my password?" from every user triggers a full LLM inference cycle — embedding generation, context retrieval, prompt assembly, and model invocation. With the Cache Layer, the first query is processed normally and its response is cached. The next identical or similar query returns immediately from cache at zero inference cost and near-zero latency.

**Key Design Principles:**
- **Exact match caching** — hash-based lookup for identical queries returns cached responses in < 5ms
- **Semantic similarity caching** — embedding-based lookup finds cached responses for paraphrased or equivalent queries
- **Per-tenant cache isolation** — caches are fully isolated per client, with optional project-level and user-level scoping
- **Cache invalidation policies** — TTL-based, event-driven, and manual invalidation strategies to prevent stale responses
- **Cost attribution** — tracks cache hits/misses per tenant for billing and optimization insights
- **Transparent integration** — callers (Orchestration, Model Gateway) can enable caching with a single flag; no application-level changes required
- **Configurable similarity thresholds** — clients control how "similar" a query must be to trigger a cache hit, balancing freshness vs. cost savings

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Multi-Tenancy & Access Model](#multi-tenancy--access-model)
3. [Core Design Philosophy](#core-design-philosophy)
4. [High-Level Architecture](#high-level-architecture)
5. [Cache Lookup Pipeline](#cache-lookup-pipeline)
6. [Exact Match Caching](#exact-match-caching)
7. [Semantic Similarity Caching](#semantic-similarity-caching)
8. [Cache Write Pipeline](#cache-write-pipeline)
9. [Cache Invalidation](#cache-invalidation)
10. [API Design](#api-design)
11. [Data Model & Storage](#data-model--storage)
12. [Authentication & Authorization](#authentication--authorization)
13. [Platform Integration](#platform-integration)
14. [Infrastructure Components](#infrastructure-components)
15. [Error Handling](#error-handling)
16. [Performance & Scaling](#performance--scaling)
17. [Implementation Phases](#implementation-phases)
18. [Appendix A: DynamoDB Schema](#appendix-a-dynamodb-schema)
19. [Appendix B: Similarity Algorithms & Formulas](#appendix-b-similarity-algorithms--formulas)
20. [Appendix C: Monitoring & Observability](#appendix-c-monitoring--observability)

---

## Multi-Tenancy & Access Model

The Cache Layer follows the Bold Platform's unified tenancy hierarchy. Every operation MUST include `client_id`. Cache entries are fully isolated per tenant — no cross-tenant cache sharing is ever possible. Within a tenant, caches can be further scoped by project for domain-specific isolation.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          TENANCY HIERARCHY                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Client (client_id)                                                          │
│  └── Top-level tenant — full cache isolation                                 │
│      └── Separate DynamoDB partitions for cache entries                     │
│      └── Separate embedding namespaces for semantic cache                  │
│      └── Separate cache metrics and cost attribution                       │
│                                                                              │
│  Project (project_id) — Cache scope boundary                                 │
│  └── Caches are scoped per project by default                               │
│      └── A query cached in "customer-support" will NOT hit for "hr-bot"    │
│      └── Prevents cross-domain cache pollution                              │
│      └── Project-level TTL and threshold overrides                          │
│                                                                              │
│  User (user_id) — Optional scope                                             │
│  └── By default, caches are shared across users within a project            │
│      └── Configurable user-scoped caching for personalized responses        │
│      └── Audit trail tracks which user triggered each cache write           │
│                                                                              │
│  Cache Entry (cache_entry_id)                                                │
│  └── Individual cached response                                              │
│      └── Linked to query hash (exact match) or embedding (semantic)         │
│      └── Immutable once written; new responses create new entries           │
│      └── TTL-governed lifecycle                                              │
│                                                                              │
│  Cache Namespace (namespace)                                                 │
│  └── Logical grouping within a project                                       │
│      └── Default: "default"                                                  │
│      └── Allows fine-grained separation (e.g., "faq", "technical", "hr")   │
│      └── Independent TTL and invalidation policies per namespace            │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Alignment with Other Blocks

| Aspect | Cache Layer | Model Gateway | Orchestration | Retrieval Service | Conversation Manager |
|--------|------------|---------------|---------------|-------------------|---------------------|
| **Top Level** | `client_id` | `client_id` | `client_id` | `client_id` | `client_id` |
| **Organization** | `project_id` | `project_id` (optional) | `project_id` | `project_id` | `project_id` |
| **Resource** | `cache_entry` | `request` | `execution` | `query` / `result` | `session` / `message` |
| **API Key** | `X-API-Key` | `X-API-Key` | `X-API-Key` | `X-API-Key` | `X-API-Key` |

---

## Core Design Philosophy

### Principle 1: Check Cache Before Inference

The Cache Layer sits on the critical path before the Model Gateway. Every query is checked against the cache first. If a hit is found, the cached response is returned immediately, bypassing all downstream processing (retrieval, prompt assembly, LLM inference). This is the primary value proposition — eliminating redundant computation.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       REQUEST LIFECYCLE WITH CACHE                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  User Query                                                                  │
│      │                                                                       │
│      ▼                                                                       │
│  ┌──────────────────┐                                                       │
│  │ CACHE LAYER      │  ← Check exact match, then semantic similarity         │
│  │ (Cache Lookup)   │                                                       │
│  └────────┬─────────┘                                                       │
│           │                                                                  │
│     ┌─────┴──────┐                                                          │
│     │            │                                                          │
│   HIT          MISS                                                         │
│     │            │                                                          │
│     ▼            ▼                                                          │
│  Return       ┌──────────────────┐                                          │
│  cached       │ ORCHESTRATION    │  Retrieval → Prompt → Model Gateway      │
│  response     │ + MODEL GATEWAY  │                                          │
│  (< 5ms)      └────────┬─────────┘                                          │
│                         │                                                    │
│                         ▼                                                    │
│                ┌──────────────────┐                                          │
│                │ CACHE LAYER      │  ← Write response to cache               │
│                │ (Cache Write)    │                                          │
│                └────────┬─────────┘                                          │
│                         │                                                    │
│                         ▼                                                    │
│                Return fresh response                                         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Principle 2: Two-Tier Cache Strategy

Exact match caching is fast and cheap. Semantic caching is slower and more expensive (requires embedding generation + vector similarity search), but catches paraphrased queries that exact match misses. The Cache Layer runs both in sequence: exact match first (< 5ms), then semantic similarity (50-150ms) only if exact match misses.

```
Query: "How do I reset my password?"
  │
  ├── Tier 1: EXACT MATCH (SHA-256 hash lookup)
  │     └── Hash matches "How do I reset my password?" → HIT
  │
  ├── Tier 2: SEMANTIC SIMILARITY (only if Tier 1 misses)
  │     └── Embedding similarity to "What's the process for password reset?"
  │     └── Similarity score: 0.94 → above threshold (0.92) → HIT
  │
  └── MISS → proceed to Model Gateway
```

### Principle 3: Cache Isolation by Design

Enterprise customers require absolute data isolation. A cache entry created by Client A must never be returned to Client B. Within a client, caches are further scoped by project to prevent cross-domain contamination. A FAQ answer cached for a customer support bot should never be returned for an internal HR query, even within the same client.

### Principle 4: Staleness Is Worse Than a Cache Miss

A stale cached response (outdated information, wrong context) is worse than the cost of a fresh LLM call. The Cache Layer enforces aggressive invalidation policies:
- TTL-based expiration (default: 1 hour for semantic, 24 hours for exact match)
- Event-driven invalidation (knowledge base update → invalidate related caches)
- Manual purge API for operators
- Configurable per namespace, per project

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              CALLERS                                              │
│                                                                                  │
│    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│    │ Orchestration│  │ Model Gateway│  │  Chat UIs    │  │  Direct API  │      │
│    │ Block        │  │ (pre-check)  │  │              │  │  Consumers   │      │
│    └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
│           │                 │                  │                  │              │
└───────────┼─────────────────┼──────────────────┼──────────────────┼──────────────┘
            │                 │                  │                  │
            └─────────────────┴────────┬─────────┴──────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         API GATEWAY + LAMBDA                                     │
│                                                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                       Cache Layer API                                      │  │
│  │                                                                            │  │
│  │  POST /v1/cache/lookup         — Check cache for a query (exact+semantic) │  │
│  │  POST /v1/cache/write          — Write a response to cache                │  │
│  │  POST /v1/cache/lookup-or-exec — Lookup, and on miss execute via callback │  │
│  │                                                                            │  │
│  │  DELETE /v1/cache/entries/{id}  — Invalidate a specific cache entry       │  │
│  │  POST /v1/cache/invalidate     — Bulk invalidation by scope/query         │  │
│  │  POST /v1/cache/purge          — Purge all cache for a scope             │  │
│  │                                                                            │  │
│  │  GET  /v1/cache/stats          — Cache hit/miss statistics                │  │
│  │  GET  /v1/cache/config         — Get cache configuration for scope        │  │
│  │  PUT  /v1/cache/config         — Update cache configuration               │  │
│  │                                                                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                       │                                          │
└───────────────────────────────────────┼──────────────────────────────────────────┘
                                        │
          ┌────────────────────────────┬┴────────────────────────────┐
          │                            │                              │
          ▼                            ▼                              ▼
┌──────────────────┐        ┌──────────────────┐          ┌──────────────────┐
│   ElastiCache    │        │    DynamoDB      │          │    Bedrock       │
│   (Redis)        │        │                  │          │                  │
│                  │        │ • Cache entries   │          │ • Query          │
│ • Exact match    │        │   (durable)      │          │   embedding      │
│   hash index     │        │ • Cache config   │          │   generation     │
│ • Hot cache      │        │ • Invalidation   │          │   (Titan Embed)  │
│ • TTL management │        │   events         │          │                  │
│ • Atomic ops     │        │ • Stats / audit  │          │ • Semantic       │
│                  │        │                  │          │   similarity     │
└──────────────────┘        └──────────────────┘          └──────────────────┘
          │
          ▼
┌──────────────────┐
│   OpenSearch     │
│   Serverless     │
│                  │
│ • Semantic cache │
│   embeddings    │
│ • kNN similarity │
│   search         │
│ • Tenant-scoped  │
│   index          │
└──────────────────┘
```

---

## Cache Lookup Pipeline

Every cache lookup flows through a two-tier pipeline: exact match first, then semantic similarity.

### Pipeline Flow

```
Query: "How do I reset my password?"
  │
  ├── Step 1: NORMALIZE QUERY
  │     ├── Lowercase
  │     ├── Strip leading/trailing whitespace
  │     ├── Collapse multiple spaces
  │     └── Normalized: "how do i reset my password?"
  │
  ├── Step 2: COMPUTE CACHE KEY
  │     ├── Build scope prefix: {client_id}:{project_id}:{namespace}
  │     ├── SHA-256 hash of normalized query
  │     └── Cache key: "acme-corp:customer-support:default:sha256_abc123..."
  │
  ├── Step 3: EXACT MATCH LOOKUP (Redis)
  │     ├── GET cache_key from Redis
  │     ├── If found and not expired → EXACT HIT
  │     │     └── Return cached response + metadata
  │     └── If not found → proceed to Step 4
  │
  ├── Step 4: SEMANTIC SIMILARITY LOOKUP (if enabled)
  │     ├── Generate query embedding via Bedrock Titan Embed v2
  │     ├── kNN search in OpenSearch (scoped to client + project + namespace)
  │     ├── Filter results above similarity threshold (default: 0.92)
  │     ├── If match found → SEMANTIC HIT
  │     │     └── Return cached response + similarity score + matched query
  │     └── If no match above threshold → CACHE MISS
  │
  └── Step 5: RETURN RESULT
        ├── HIT: { status: "hit", source: "exact"|"semantic", response: ..., latency_ms: ... }
        └── MISS: { status: "miss", latency_ms: ... }
```

### Lookup Configuration

Each cache lookup request can customize the lookup behavior:

```json
{
  "lookup_config": {
    "enable_exact_match": true,
    "enable_semantic": true,
    "similarity_threshold": 0.92,
    "max_age_seconds": 3600,
    "namespace": "default"
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `enable_exact_match` | `true` | Check exact match cache (Redis) |
| `enable_semantic` | `true` | Check semantic similarity cache (OpenSearch) |
| `similarity_threshold` | `0.92` | Minimum cosine similarity for semantic hit |
| `max_age_seconds` | `null` (use entry TTL) | Override: only return entries younger than this |
| `namespace` | `"default"` | Cache namespace within the project |

---

## Exact Match Caching

### How It Works

Exact match caching uses a deterministic hash of the normalized query string as the cache key. If two queries produce the same hash, they are treated as identical. This is fast (< 5ms), cheap (no embedding cost), and precise (no false positives).

### Cache Key Construction

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       CACHE KEY STRUCTURE                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Components:                                                                 │
│  ┌─────────────┬────────────────────┬───────────────┬──────────────────┐   │
│  │  client_id  │    project_id      │   namespace   │   query_hash     │   │
│  │  (tenant)   │    (scope)         │   (grouping)  │   (SHA-256)      │   │
│  └──────┬──────┴─────────┬──────────┴───────┬───────┴────────┬─────────┘   │
│         │                │                  │                │              │
│         ▼                ▼                  ▼                ▼              │
│  "acme-corp"    "customer-support"     "default"    "a7f3b2c1..."         │
│                                                                              │
│  Full key: "acme-corp:customer-support:default:a7f3b2c1..."                │
│                                                                              │
│  Optional context hash (when context_aware_caching is enabled):            │
│  └── Includes hash of system prompt + retrieval context                    │
│      └── Same query with different context = different cache entry          │
│      └── Key: "acme-corp:customer-support:default:a7f3b2c1...:ctx_d4e5f6" │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Query Normalization

Before hashing, queries are normalized to increase exact match rates:

```python
def normalize_query(query: str) -> str:
    # 1. Strip whitespace
    query = query.strip()
    # 2. Lowercase
    query = query.lower()
    # 3. Collapse multiple spaces
    query = re.sub(r'\s+', ' ', query)
    # 4. Remove trailing punctuation variations
    query = re.sub(r'[?!.]+$', '?', query)
    return query
```

Examples of normalization:
| Original | Normalized | Same Hash? |
|----------|-----------|-----------|
| "How do I reset my password?" | "how do i reset my password?" | Yes |
| "how do I reset my password" | "how do i reset my password?" | Yes |
| " How do I  reset my password?? " | "how do i reset my password?" | Yes |
| "What's the password reset process?" | "what's the password reset process?" | No (different query) |

### Redis Storage Format

```json
{
  "key": "acme-corp:customer-support:default:a7f3b2c1...",
  "value": {
    "cache_entry_id": "ce-01JKX001...",
    "query_normalized": "how do i reset my password?",
    "response": {
      "content": "To reset your password, follow these steps: 1. Go to the login page...",
      "model": "anthropic.claude-sonnet-4-5-20250929",
      "tokens_used": { "input": 245, "output": 180 },
      "citations": [
        {
          "document_id": "doc-uuid-001",
          "document_title": "IT Help Desk FAQ",
          "chunk_id": "chunk-uuid-123"
        }
      ]
    },
    "metadata": {
      "created_at": "2026-02-10T12:00:00Z",
      "created_by_user": "user-abc123",
      "original_request_id": "req-uuid-456",
      "hit_count": 47,
      "last_hit_at": "2026-02-10T14:30:00Z"
    }
  },
  "ttl": 86400
}
```

### Context-Aware Exact Match

For applications where the same query can have different answers depending on context (e.g., different system prompts, different retrieval results), the Cache Layer supports context-aware caching:

```json
{
  "query": "How do I reset my password?",
  "context_hash_inputs": {
    "system_prompt_hash": true,
    "retrieval_context_hash": false,
    "user_role": true
  }
}
```

When `context_hash_inputs` is provided, the relevant context fields are hashed and appended to the cache key. This ensures that the same query with different system prompts produces separate cache entries.

---

## Semantic Similarity Caching

### How It Works

Semantic similarity caching captures paraphrased, reworded, or rephrased versions of the same question. When an exact match misses, the Cache Layer generates an embedding of the query and performs a kNN similarity search against all cached query embeddings within the same scope (client + project + namespace).

If the most similar cached query exceeds the similarity threshold (default: 0.92), the cached response is returned as a semantic hit.

### Why 0.92 Threshold?

The similarity threshold is the critical tuning parameter. Too low → stale or wrong responses served from cache. Too high → cache misses on obvious paraphrases.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    THRESHOLD TUNING GUIDE                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Threshold: 0.98+                                                            │
│  └── Nearly identical queries only                                           │
│      └── "How do I reset my password?" ↔ "How can I reset my password?"     │
│      └── Very conservative — few false positives, many misses               │
│                                                                              │
│  Threshold: 0.92–0.97 (RECOMMENDED)                                         │
│  └── Paraphrases and rewordings                                              │
│      └── "How do I reset my password?" ↔ "password reset process"           │
│      └── Good balance of coverage and accuracy                               │
│                                                                              │
│  Threshold: 0.85–0.91                                                        │
│  └── Broader semantic matching                                               │
│      └── "How do I reset my password?" ↔ "I forgot my login credentials"   │
│      └── Higher coverage but risk of incorrect cache hits                    │
│                                                                              │
│  Threshold: < 0.85                                                           │
│  └── NOT RECOMMENDED — too many false positives                              │
│      └── "How do I reset my password?" ↔ "What's the IT support number?"   │
│      └── Semantically related but different questions                        │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Embedding Generation

Query embeddings are generated using Amazon Bedrock Titan Embed v2 (the same model used by the Retrieval Service for consistency):

```json
{
  "modelId": "amazon.titan-embed-text-v2:0",
  "contentType": "application/json",
  "body": {
    "inputText": "how do i reset my password?",
    "dimensions": 1024,
    "normalize": true
  }
}
```

The resulting 1024-dimensional vector is stored in OpenSearch alongside the cache entry reference.

### OpenSearch Semantic Cache Index

```json
{
  "mappings": {
    "properties": {
      "cache_entry_id": { "type": "keyword" },
      "client_id": { "type": "keyword" },
      "project_id": { "type": "keyword" },
      "namespace": { "type": "keyword" },
      "query_normalized": { "type": "text" },
      "query_embedding": {
        "type": "knn_vector",
        "dimension": 1024,
        "method": {
          "name": "hnsw",
          "space_type": "cosinesimil",
          "engine": "nmslib"
        }
      },
      "created_at": { "type": "date" },
      "expires_at": { "type": "date" },
      "ttl": { "type": "integer" }
    }
  }
}
```

### Semantic Lookup Query

```json
{
  "size": 1,
  "query": {
    "bool": {
      "must": [
        {
          "knn": {
            "query_embedding": {
              "vector": [0.023, -0.114, ...],
              "k": 5
            }
          }
        }
      ],
      "filter": [
        { "term": { "client_id": "acme-corp" } },
        { "term": { "project_id": "customer-support" } },
        { "term": { "namespace": "default" } },
        { "range": { "expires_at": { "gte": "now" } } }
      ]
    }
  },
  "min_score": 0.92
}
```

The `min_score` parameter ensures only results above the similarity threshold are returned.

### Semantic Match Response

When a semantic hit is found, the response includes the matched query for transparency:

```json
{
  "status": "hit",
  "source": "semantic",
  "similarity_score": 0.946,
  "matched_query": "What's the process for resetting my password?",
  "original_query": "How do I reset my password?",
  "response": { ... },
  "cache_entry_id": "ce-01JKX001..."
}
```

This allows callers to inspect what triggered the cache hit and decide whether to accept or bypass it.

---

## Cache Write Pipeline

When a cache miss occurs and the downstream pipeline (Orchestration → Model Gateway) produces a response, the response is written back to the cache for future lookups.

### Write Flow

```
Fresh Response from Model Gateway
  │
  ├── Step 1: CHECK CACHEABILITY
  │     ├── Is caching enabled for this client/project?
  │     ├── Did the caller request caching? (cache_control != "no-store")
  │     ├── Is the response suitable for caching?
  │     │     ├── Not an error response
  │     │     ├── Not a streaming response (stream results are not cached)
  │     │     └── Response size within limits (< 100KB default)
  │     └── If not cacheable → skip, return response without caching
  │
  ├── Step 2: GENERATE CACHE ENTRY
  │     ├── Generate cache_entry_id (ULID)
  │     ├── Compute query hash (SHA-256 of normalized query)
  │     ├── Generate query embedding (Bedrock Titan Embed v2)
  │     └── Compute TTL from namespace config or default
  │
  ├── Step 3: WRITE TO STORES (parallel)
  │     ├── Write to Redis (exact match cache) with TTL
  │     ├── Write to DynamoDB (durable record + metadata)
  │     └── Write to OpenSearch (semantic cache embedding + metadata)
  │
  └── Step 4: RETURN CONFIRMATION
        └── Cache entry written successfully, return cache_entry_id
```

### Cacheability Rules

Not all responses should be cached. The Cache Layer applies these rules:

| Rule | Description | Example |
|------|-------------|---------|
| Error responses | Never cache error/failure responses | 4xx, 5xx responses |
| Streaming responses | Cannot cache partial/streaming output | SSE streams |
| Personalized responses | Skip if response contains user-specific data | "Hi John, your account..." |
| Size limit | Skip if response exceeds max size | > 100KB default |
| Explicit no-cache | Caller sets `cache_control: "no-store"` | One-off queries |
| Low-confidence responses | Skip if guardrail score is below threshold | Hallucination warnings |

### Write Consistency

Writes to the three stores (Redis, DynamoDB, OpenSearch) happen in parallel for performance. If any write fails:
- **Redis write fails**: Log warning, continue. DynamoDB is the durable store; Redis can be repopulated on next read.
- **DynamoDB write fails**: Retry once. If still fails, do not cache this response (data integrity).
- **OpenSearch write fails**: Log warning, continue. Semantic lookup will miss, but exact match still works.

---

## Cache Invalidation

### Invalidation Strategies

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     INVALIDATION STRATEGIES                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Strategy 1: TTL-BASED EXPIRATION                                            │
│  └── Every cache entry has a time-to-live                                    │
│      └── Redis: native TTL expiration (automatic)                           │
│      └── DynamoDB: TTL attribute (automatic, periodic cleanup)              │
│      └── OpenSearch: expires_at filter on lookup (query-time filtering)     │
│      └── Default TTLs:                                                      │
│          └── Exact match: 24 hours                                          │
│          └── Semantic match: 1 hour (more conservative — paraphrase risk)   │
│          └── Configurable per namespace                                      │
│                                                                              │
│  Strategy 2: EVENT-DRIVEN INVALIDATION                                       │
│  └── External events trigger cache invalidation                              │
│      └── Knowledge base update (Doc Ingest publishes event)                 │
│          └── Invalidate all semantic cache entries for affected project      │
│      └── Policy change (Guardrails updates rules)                           │
│          └── Invalidate caches where guardrail verdict may differ           │
│      └── Model change (Model Gateway switches model version)               │
│          └── Invalidate all caches for affected scope                       │
│      └── Events consumed via EventBridge                                    │
│                                                                              │
│  Strategy 3: MANUAL INVALIDATION                                             │
│  └── Operators can manually purge cache entries                              │
│      └── DELETE /v1/cache/entries/{id} — single entry                       │
│      └── POST /v1/cache/invalidate — bulk by query pattern or metadata     │
│      └── POST /v1/cache/purge — full purge for a scope                     │
│                                                                              │
│  Strategy 4: LRU EVICTION (Redis only)                                       │
│  └── When Redis memory is full, least recently used entries are evicted     │
│      └── Redis maxmemory-policy: allkeys-lru                               │
│      └── DynamoDB and OpenSearch retain entries until TTL expires           │
│      └── Evicted Redis entries are repopulated from DynamoDB on next read  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Event-Driven Invalidation via EventBridge

The Cache Layer subscribes to platform events that indicate cached data may be stale:

```json
{
  "source": "bold.doc-ingest",
  "detail-type": "DocumentIngested",
  "detail": {
    "client_id": "acme-corp",
    "project_id": "customer-support",
    "document_id": "doc-uuid-001",
    "action": "updated"
  }
}
```

When this event is received:
1. Look up all cache entries for `client_id=acme-corp, project_id=customer-support`
2. Check if any cached responses referenced `document_id=doc-uuid-001` in their citations
3. Invalidate matching entries (delete from Redis, mark as invalidated in DynamoDB, delete from OpenSearch)

```json
{
  "source": "bold.model-gateway",
  "detail-type": "ModelVersionChanged",
  "detail": {
    "client_id": "acme-corp",
    "project_id": "customer-support",
    "old_model": "anthropic.claude-sonnet-4-5-20250929",
    "new_model": "anthropic.claude-opus-4-6-20250916"
  }
}
```

When this event is received, invalidate all cache entries for the affected scope, since a new model may produce different responses.

### Invalidation Scoping

Invalidation operations support multiple scoping levels:

| Scope | Effect | Use Case |
|-------|--------|----------|
| Single entry | Delete one cache entry by ID | Manual correction |
| By query pattern | Invalidate entries matching a query substring | Topic-specific refresh |
| By namespace | Invalidate all entries in a namespace | Category refresh |
| By project | Invalidate all entries in a project | Knowledge base overhaul |
| By client | Invalidate all entries for a client | Full cache reset |
| By citation | Invalidate entries citing a specific document | Document updated |

---

## API Design

All endpoints are authenticated via `X-API-Key` header and require `client_id`.

### POST /v1/cache/lookup

Check the cache for a query. Returns a hit (with cached response) or a miss.

**Request:**

```json
{
  "client_id": "acme-corp",
  "project_id": "customer-support",
  "query": "How do I reset my password?",
  "request_id": "req-uuid-456",
  "lookup_config": {
    "enable_exact_match": true,
    "enable_semantic": true,
    "similarity_threshold": 0.92,
    "max_age_seconds": null,
    "namespace": "default"
  },
  "context_hash_inputs": null
}
```

**Response (Cache Hit — Exact):**

```json
{
  "request_id": "req-uuid-456",
  "status": "hit",
  "source": "exact",
  "cache_entry_id": "ce-01JKX001...",
  "response": {
    "content": "To reset your password, follow these steps: 1. Go to the login page and click 'Forgot Password'. 2. Enter your email address. 3. Check your inbox for a reset link. 4. Click the link and set a new password.",
    "model": "anthropic.claude-sonnet-4-5-20250929",
    "tokens_saved": {
      "input": 245,
      "output": 180
    },
    "citations": [
      {
        "document_id": "doc-uuid-001",
        "document_title": "IT Help Desk FAQ",
        "chunk_id": "chunk-uuid-123",
        "page_number": 5
      }
    ]
  },
  "cache_metadata": {
    "created_at": "2026-02-10T12:00:00Z",
    "hit_count": 48,
    "last_hit_at": "2026-02-10T14:30:00Z",
    "ttl_remaining_seconds": 34200
  },
  "lookup_latency_ms": 3,
  "cost_saved_estimate_usd": 0.0042
}
```

**Response (Cache Hit — Semantic):**

```json
{
  "request_id": "req-uuid-456",
  "status": "hit",
  "source": "semantic",
  "similarity_score": 0.946,
  "matched_query": "What's the process for resetting my password?",
  "cache_entry_id": "ce-01JKX001...",
  "response": { ... },
  "cache_metadata": { ... },
  "lookup_latency_ms": 87,
  "cost_saved_estimate_usd": 0.0042
}
```

**Response (Cache Miss):**

```json
{
  "request_id": "req-uuid-456",
  "status": "miss",
  "lookup_latency_ms": 92,
  "stages": {
    "exact_match_ms": 2,
    "semantic_ms": 90,
    "semantic_best_score": 0.71,
    "semantic_threshold": 0.92
  }
}
```

### POST /v1/cache/write

Write a response to the cache after a successful LLM invocation.

**Request:**

```json
{
  "client_id": "acme-corp",
  "project_id": "customer-support",
  "request_id": "req-uuid-456",
  "query": "How do I reset my password?",
  "response": {
    "content": "To reset your password, follow these steps...",
    "model": "anthropic.claude-sonnet-4-5-20250929",
    "tokens_used": {
      "input": 245,
      "output": 180
    },
    "citations": [
      {
        "document_id": "doc-uuid-001",
        "document_title": "IT Help Desk FAQ",
        "chunk_id": "chunk-uuid-123",
        "page_number": 5
      }
    ]
  },
  "write_config": {
    "namespace": "default",
    "ttl_seconds": 86400,
    "enable_semantic": true,
    "cache_control": "public"
  }
}
```

**Response:**

```json
{
  "cache_entry_id": "ce-01JKX002...",
  "request_id": "req-uuid-456",
  "status": "written",
  "stores": {
    "redis": "ok",
    "dynamodb": "ok",
    "opensearch": "ok"
  },
  "expires_at": "2026-02-11T12:00:00Z",
  "created_at": "2026-02-10T12:00:00Z"
}
```

### POST /v1/cache/lookup-or-exec

Convenience endpoint that combines lookup and execution. If a cache hit is found, return it. If not, invoke a callback URL (typically the Model Gateway or Orchestration block), cache the result, and return it. This simplifies integration for callers that want transparent caching.

**Request:**

```json
{
  "client_id": "acme-corp",
  "project_id": "customer-support",
  "query": "How do I reset my password?",
  "request_id": "req-uuid-789",
  "lookup_config": {
    "enable_exact_match": true,
    "enable_semantic": true,
    "similarity_threshold": 0.92,
    "namespace": "default"
  },
  "on_miss": {
    "callback_url": "https://api.bold.internal/model-gateway/v1/invoke",
    "callback_method": "POST",
    "callback_body": {
      "client_id": "acme-corp",
      "model": "anthropic.claude-sonnet-4-5-20250929",
      "messages": [
        { "role": "user", "content": "How do I reset my password?" }
      ]
    },
    "callback_headers": {
      "X-API-Key": "{{inherited}}"
    },
    "cache_response": true,
    "ttl_seconds": 86400
  }
}
```

**Response:** Same as `/v1/cache/lookup` on hit, or the callback response (with cache write confirmation) on miss.

### DELETE /v1/cache/entries/{id}

Invalidate a specific cache entry.

**Response:**

```json
{
  "cache_entry_id": "ce-01JKX001...",
  "status": "invalidated",
  "stores": {
    "redis": "deleted",
    "dynamodb": "marked_invalidated",
    "opensearch": "deleted"
  }
}
```

### POST /v1/cache/invalidate

Bulk invalidation by scope, query pattern, or citation reference.

**Request:**

```json
{
  "client_id": "acme-corp",
  "project_id": "customer-support",
  "invalidation_criteria": {
    "namespace": "default",
    "query_contains": "password",
    "cited_document_ids": ["doc-uuid-001"],
    "created_before": "2026-02-09T00:00:00Z"
  }
}
```

**Response:**

```json
{
  "request_id": "req-uuid-999",
  "entries_invalidated": 12,
  "invalidation_criteria": { ... },
  "created_at": "2026-02-10T15:00:00Z"
}
```

### POST /v1/cache/purge

Purge all cache entries for a scope.

**Request:**

```json
{
  "client_id": "acme-corp",
  "project_id": "customer-support",
  "namespace": null,
  "confirm": true
}
```

When `namespace` is null, all namespaces in the project are purged. The `confirm` field must be `true` to prevent accidental purges.

**Response:**

```json
{
  "request_id": "req-uuid-888",
  "entries_purged": 1247,
  "scope": {
    "client_id": "acme-corp",
    "project_id": "customer-support",
    "namespace": "all"
  },
  "created_at": "2026-02-10T15:00:00Z"
}
```

### GET /v1/cache/stats

Get cache statistics for a scope.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `client_id` | string | required | Client identifier |
| `project_id` | string | null | Filter by project |
| `namespace` | string | null | Filter by namespace |
| `period` | string | `"24h"` | Stats period: `"1h"`, `"24h"`, `"7d"`, `"30d"` |

**Response:**

```json
{
  "client_id": "acme-corp",
  "project_id": "customer-support",
  "period": "24h",
  "stats": {
    "total_lookups": 5420,
    "exact_hits": 3150,
    "semantic_hits": 890,
    "misses": 1380,
    "hit_rate": 0.745,
    "exact_hit_rate": 0.581,
    "semantic_hit_rate": 0.164,
    "avg_exact_latency_ms": 3,
    "avg_semantic_latency_ms": 85,
    "avg_miss_latency_ms": 92,
    "total_entries": 342,
    "entries_by_namespace": {
      "default": 280,
      "faq": 62
    },
    "estimated_cost_saved_usd": 22.76,
    "estimated_tokens_saved": {
      "input": 1_234_500,
      "output": 876_200
    }
  }
}
```

### GET /v1/cache/config

Get the cache configuration for a scope.

### PUT /v1/cache/config

Update cache configuration for a scope.

**Request:**

```json
{
  "client_id": "acme-corp",
  "project_id": "customer-support",
  "config": {
    "enabled": true,
    "default_ttl_seconds": 86400,
    "semantic_ttl_seconds": 3600,
    "similarity_threshold": 0.92,
    "max_entry_size_bytes": 102400,
    "namespaces": {
      "faq": {
        "ttl_seconds": 604800,
        "similarity_threshold": 0.90,
        "semantic_enabled": true
      },
      "dynamic": {
        "ttl_seconds": 900,
        "similarity_threshold": 0.96,
        "semantic_enabled": true
      }
    },
    "context_aware_caching": false,
    "event_driven_invalidation": true,
    "invalidation_events": [
      "bold.doc-ingest.DocumentIngested",
      "bold.model-gateway.ModelVersionChanged"
    ]
  }
}
```

### Error Responses

All errors follow the platform standard format:

```json
{
  "error": {
    "code": "CACHE_ERROR",
    "message": "Human-readable description",
    "request_id": "req-uuid-456",
    "details": {}
  }
}
```

| Error Code | HTTP Status | Description |
|-----------|------------|-------------|
| `INVALID_REQUEST` | 400 | Missing or invalid parameters |
| `UNAUTHORIZED` | 401 | Invalid or missing API key |
| `CACHE_ENTRY_NOT_FOUND` | 404 | Referenced cache entry does not exist |
| `CACHE_WRITE_FAILED` | 500 | Failed to write cache entry (DynamoDB failure) |
| `EMBEDDING_ERROR` | 502 | Bedrock embedding generation failed |
| `OPENSEARCH_ERROR` | 502 | OpenSearch unavailable or query failed |
| `REDIS_ERROR` | 502 | Redis unavailable |
| `CALLBACK_ERROR` | 502 | On-miss callback failed (for lookup-or-exec) |
| `PURGE_REQUIRES_CONFIRM` | 400 | Purge request missing `confirm: true` |
| `INTERNAL_ERROR` | 500 | Internal processing error |

---

## Data Model & Storage

### Entity Relationship

```
CacheEntry
  │
  ├── identified by cache_entry_id (ULID)
  ├── scoped to Client + Project + Namespace
  │
  ├── contains:
  │     ├── query_normalized: original normalized query string
  │     ├── query_hash: SHA-256 hash for exact match
  │     ├── response: full cached response payload
  │     ├── citations: referenced document/chunk IDs
  │     └── metadata: hit count, timestamps, original request info
  │
  ├── stored in:
  │     ├── Redis (hot cache — exact match, fast TTL expiration)
  │     ├── DynamoDB (durable store — full entry, audit trail)
  │     └── OpenSearch (semantic cache — embedding + metadata)
  │
  └── lifecycle:
        ├── created → active (serving hits)
        ├── active → expired (TTL reached)
        ├── active → invalidated (manual or event-driven)
        └── expired/invalidated → deleted (cleanup)

CacheConfig
  │
  ├── scoped to Client + Project
  ├── contains: TTLs, thresholds, namespace configs, event subscriptions
  └── stored in: DynamoDB

InvalidationEvent
  │
  ├── scoped to Client + Project
  ├── contains: criteria, entries affected, timestamp, source
  └── stored in: DynamoDB (audit trail)
```

### DynamoDB Table: `bold-cache-layer`

Single-table design following platform conventions.

#### Cache Entry Entity

```
PK: CLIENT#{client_id}
SK: CACHE#{project_id}#{namespace}#{cache_entry_id}

Attributes:
- cache_entry_id: string (ULID)
- project_id: string
- namespace: string
- query_normalized: string
- query_hash: string (SHA-256)
- response: Map (full cached response payload)
- citations: List<Map> (referenced document/chunk IDs)
- model: string (model that generated the response)
- tokens_used: Map { input, output }
- hit_count: integer
- last_hit_at: string (ISO 8601)
- created_at: string (ISO 8601)
- created_by_user: string (user_id)
- original_request_id: string
- status: "active" | "invalidated" | "expired"
- ttl: integer (epoch seconds — DynamoDB TTL)
```

#### Cache Config Entity

```
PK: CLIENT#{client_id}
SK: CONFIG#{project_id}

Attributes:
- project_id: string
- enabled: boolean
- default_ttl_seconds: integer
- semantic_ttl_seconds: integer
- similarity_threshold: number (0.0 – 1.0)
- max_entry_size_bytes: integer
- namespaces: Map<string, NamespaceConfig>
- context_aware_caching: boolean
- event_driven_invalidation: boolean
- invalidation_events: List<string>
- updated_at: string (ISO 8601)
- updated_by: string (user_id)
```

#### Invalidation Event Entity

```
PK: CLIENT#{client_id}
SK: INVAL#{timestamp}#{event_id}

Attributes:
- event_id: string (ULID)
- project_id: string
- source: "manual" | "event" | "ttl" | "purge"
- criteria: Map (invalidation criteria)
- entries_affected: integer
- triggered_by: string (user_id or event source)
- created_at: string (ISO 8601)
- ttl: integer (audit retention — 90 days default)
```

### Global Secondary Indexes

| GSI Name | PK | SK | Purpose |
|----------|----|----|---------|
| `GSI-QueryHash` | `CLIENT#{client_id}#HASH#{query_hash}` | `CACHE#{cache_entry_id}` | Fast exact match lookup (fallback when Redis misses) |
| `GSI-ProjectNamespace` | `CLIENT#{client_id}#PROJECT#{project_id}` | `NAMESPACE#{namespace}#CREATED#{created_at}` | List entries by project + namespace |
| `GSI-Citation` | `CLIENT#{client_id}#DOC#{document_id}` | `CACHE#{cache_entry_id}` | Find cache entries citing a specific document (for invalidation) |
| `GSI-Stats` | `CLIENT#{client_id}#PROJECT#{project_id}` | `STATS#{period}#{timestamp}` | Cache statistics aggregation |

---

## Authentication & Authorization

### API Key Authentication

Follows the Bold Platform standard:

```
Request Header: X-API-Key: <api-key-value>

Validation:
1. Look up key in shared `bold-api-keys` DynamoDB table
2. Resolve client_id from key record
3. Verify key status is "active"
4. Verify client_id in request matches key's client_id
5. Check key's allowed_services includes "cache-layer"
```

### Cache Scope Authorization

The caller's API key determines which projects' caches they can access. A cache lookup for `project_id=hr-bot` will fail if the API key doesn't have access to that project.

```
1. API key resolves to client_id + access_scope
2. access_scope contains: allowed_project_ids
3. Verify requested project_id is in allowed_project_ids
4. If not → 403 Forbidden
```

### Admin Operations

Purge and bulk invalidation operations require elevated permissions:

```json
{
  "api_key_permissions": {
    "cache-layer:read": "lookup and stats",
    "cache-layer:write": "write cache entries",
    "cache-layer:invalidate": "single entry invalidation",
    "cache-layer:admin": "bulk invalidation, purge, config changes"
  }
}
```

---

## Platform Integration

### Integration with Orchestration Block

The primary integration pattern. The Orchestration block calls the Cache Layer before and after the Model Gateway:

```
Orchestration Block:
  1. Receive user request
  2. Call Cache Layer POST /v1/cache/lookup
  3. If status == "hit" → return cached response to user
  4. If status == "miss" → proceed with full pipeline:
     a. Call Retrieval Service (get context)
     b. Call Prompt Library (assemble prompt)
     c. Call Model Gateway (LLM inference)
     d. Call Guardrails (output validation)
  5. Call Cache Layer POST /v1/cache/write (cache the fresh response)
  6. Return response to user
```

### Integration with Model Gateway

The Model Gateway can optionally call the Cache Layer as inline middleware:

```
Model Gateway:
  1. Receive LLM request
  2. (Optional) Call Cache Layer lookup
  3. If hit → return cached response (skip LLM)
  4. If miss → invoke LLM provider
  5. (Optional) Call Cache Layer write (cache response)
  6. Return response
```

### Integration with Doc Ingest (Event-Driven)

The Cache Layer subscribes to Doc Ingest events to invalidate stale caches:

```
Doc Ingest publishes:
  EventBridge → "DocumentIngested" / "DocumentUpdated" / "DocumentDeleted"

Cache Layer consumes:
  1. Receive event
  2. Look up cache entries citing the affected document
  3. Invalidate matching entries
```

### Integration with Guardrails Block

The Cache Layer respects guardrail verdicts:
- Responses that received a "warn" or "block" verdict from Guardrails are NOT cached
- If a cached response is later found to violate a new guardrail policy, the event-driven invalidation system purges it

### Cache Layer Does NOT Call These Blocks

The Cache Layer is intentionally minimal in its downstream dependencies:

| Block | Relationship |
|-------|-------------|
| **Model Gateway** | Cache Layer sits *in front of* — does not call |
| **Retrieval Service** | Not called — cache stores full responses including citations |
| **Conversation Manager** | Not called — caching is stateless, not session-aware |
| **Prompt Library** | Not called — cache stores rendered responses, not prompts |

The only external calls the Cache Layer makes are:
1. **Bedrock** — for query embedding generation (semantic cache)
2. **Redis** — for exact match cache operations
3. **OpenSearch** — for semantic similarity search
4. **DynamoDB** — for durable storage and configuration
5. **EventBridge** — for consuming invalidation events

---

## Infrastructure Components

### AWS Services

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       INFRASTRUCTURE                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Compute                                                                     │
│  └── AWS Lambda (Python 3.12, arm64)                                         │
│      └── Main API: FastAPI + Mangum adapter                                  │
│      └── Memory: 512 MB                                                      │
│      └── Timeout: 30 seconds                                                 │
│      └── Provisioned concurrency: 10 (latency-critical — on the hot path)   │
│                                                                              │
│  └── Event Handler Lambda                                                    │
│      └── Consumes EventBridge invalidation events                           │
│      └── Memory: 256 MB                                                      │
│      └── Timeout: 60 seconds (bulk invalidation can be slow)                │
│                                                                              │
│  API                                                                         │
│  └── API Gateway (REST)                                                      │
│      └── Routes mapped to Lambda                                             │
│      └── Request validation at gateway level                                 │
│      └── WAF integration for DDoS protection                                │
│                                                                              │
│  Caching                                                                     │
│  └── Amazon ElastiCache (Redis 7.x, cluster mode)                           │
│      └── Node type: cache.r7g.large (13 GB memory)                          │
│      └── Cluster mode: enabled (for horizontal scaling)                      │
│      └── Encryption at rest + in transit                                     │
│      └── Multi-AZ with automatic failover                                   │
│      └── maxmemory-policy: allkeys-lru                                      │
│      └── VPC deployment (same VPC as Lambda)                                │
│                                                                              │
│  Storage                                                                     │
│  └── DynamoDB                                                                │
│      └── Table: bold-cache-layer                                             │
│      └── On-demand capacity (burst-friendly)                                 │
│      └── Point-in-time recovery enabled                                      │
│      └── TTL enabled (cache entry and audit retention)                      │
│                                                                              │
│  Search                                                                      │
│  └── OpenSearch Serverless                                                   │
│      └── Collection: bold-semantic-cache                                     │
│      └── Index: semantic-cache-{env}                                        │
│      └── Vector engine: HNSW with cosine similarity                         │
│      └── Used exclusively for semantic cache kNN lookups                    │
│                                                                              │
│  AI/ML                                                                       │
│  └── AWS Bedrock                                                             │
│      └── Titan Embed v2 (query embedding for semantic cache)                │
│      └── Dimension: 1024                                                     │
│                                                                              │
│  Events                                                                      │
│  └── Amazon EventBridge                                                      │
│      └── Subscribes to: bold.doc-ingest, bold.model-gateway events         │
│      └── Triggers invalidation Lambda                                        │
│                                                                              │
│  Networking                                                                  │
│  └── VPC                                                                     │
│      └── Lambda functions deployed in VPC (required for ElastiCache)        │
│      └── VPC endpoints for DynamoDB, Bedrock, OpenSearch                    │
│      └── NAT Gateway for EventBridge, API Gateway                           │
│                                                                              │
│  Monitoring                                                                  │
│  └── CloudWatch                                                              │
│      └── Metrics: hit rate, latency, invalidation rate, Redis memory        │
│      └── Alarms: low hit rate, high miss rate, Redis memory pressure        │
│      └── Logs: structured JSON logging                                       │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Lambda Functions

| Function | Memory | Timeout | Purpose |
|----------|--------|---------|---------|
| cache-api | 512 MB | 30s | Main API (lookup, write, invalidate, stats) |
| cache-event-handler | 256 MB | 60s | EventBridge invalidation event consumer |
| cache-stats-aggregator | 256 MB | 120s | Periodic stats aggregation (CloudWatch scheduled) |

### SAM Template Structure

```
platform-block-cache-layer/
├── ARCHITECTURE.md
├── template.yaml                    # SAM template
├── samconfig.toml                   # Deploy configuration
├── pyproject.toml                   # Poetry dependencies
├── src/
│   ├── handlers/
│   │   ├── lookup.py                # POST /v1/cache/lookup
│   │   ├── write.py                 # POST /v1/cache/write
│   │   ├── lookup_or_exec.py        # POST /v1/cache/lookup-or-exec
│   │   ├── invalidate.py            # DELETE + POST invalidation endpoints
│   │   ├── purge.py                 # POST /v1/cache/purge
│   │   ├── stats.py                 # GET /v1/cache/stats
│   │   ├── config.py                # GET + PUT /v1/cache/config
│   │   └── event_handler.py         # EventBridge event consumer
│   ├── core/
│   │   ├── pipeline.py              # Cache lookup pipeline orchestration
│   │   ├── exact_match.py           # Exact match caching logic
│   │   ├── semantic_match.py        # Semantic similarity caching logic
│   │   ├── normalizer.py            # Query normalization
│   │   ├── cache_writer.py          # Write pipeline (Redis + DynamoDB + OpenSearch)
│   │   ├── invalidator.py           # Invalidation logic
│   │   └── stats_engine.py          # Statistics aggregation
│   ├── models/
│   │   ├── request.py               # Request schemas (Pydantic)
│   │   ├── response.py              # Response schemas
│   │   ├── cache_entry.py           # Cache entry data model
│   │   └── config.py                # Cache config data model
│   ├── auth/
│   │   ├── api_key.py               # API key validation
│   │   └── access_scope.py          # Permission resolution
│   └── clients/
│       ├── redis_client.py          # ElastiCache Redis connection + operations
│       ├── opensearch_client.py     # OpenSearch connection + kNN queries
│       ├── bedrock_client.py        # Bedrock embedding generation
│       ├── dynamodb_client.py       # DynamoDB operations
│       └── eventbridge_client.py    # EventBridge event consumption
└── tests/
    ├── unit/
    │   ├── test_normalizer.py
    │   ├── test_exact_match.py
    │   ├── test_semantic_match.py
    │   ├── test_pipeline.py
    │   ├── test_invalidator.py
    │   └── test_cache_writer.py
    ├── integration/
    │   ├── test_redis_operations.py
    │   ├── test_opensearch_operations.py
    │   └── test_end_to_end.py
    └── fixtures/
        ├── sample_queries.json
        └── sample_responses.json
```

---

## Error Handling

### Graceful Degradation

The Cache Layer is on the critical path for every AI request. If it fails, the request should still succeed — it just won't benefit from caching. Graceful degradation ensures the Cache Layer never blocks a valid request.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      DEGRADATION STRATEGY                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Scenario: Redis unavailable                                                 │
│  └── Strategy: Skip exact match, fall through to semantic                    │
│      └── If semantic also fails → return cache miss                          │
│      └── Caller proceeds to Model Gateway normally                           │
│      └── Log degraded mode for monitoring                                    │
│                                                                              │
│  Scenario: OpenSearch unavailable                                            │
│  └── Strategy: Skip semantic lookup, rely on exact match only               │
│      └── Exact match (Redis) still provides value                           │
│      └── Semantic cache writes queued for retry                              │
│      └── Log degraded mode                                                   │
│                                                                              │
│  Scenario: Bedrock unavailable (embedding generation)                       │
│  └── Strategy: Skip semantic lookup and write                               │
│      └── Exact match still works                                             │
│      └── Log embedding failure                                               │
│                                                                              │
│  Scenario: DynamoDB unavailable                                              │
│  └── Strategy: Serve from Redis (stale cache better than no cache)          │
│      └── Cache writes fail — responses not persisted                         │
│      └── Log critical degradation                                            │
│                                                                              │
│  Scenario: Full Cache Layer outage                                           │
│  └── Strategy: Caller treats as cache miss                                   │
│      └── All requests proceed to Model Gateway                               │
│      └── No data loss — just higher latency and cost                        │
│      └── Orchestration block handles the fallback                           │
│                                                                              │
│  CRITICAL: Cache Layer failures NEVER block requests.                       │
│  A cache miss is always a valid response.                                    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Circuit Breaker Pattern

External dependencies (Redis, OpenSearch, Bedrock) are wrapped with circuit breakers:

- **Closed** (normal): Requests pass through
- **Open** (after 5 consecutive failures): Requests fail fast, return cache miss immediately
- **Half-open** (after 30 seconds): Allow one test request through

---

## Performance & Scaling

### Latency Targets

| Operation | Target P50 | Target P99 | Notes |
|-----------|-----------|-----------|-------|
| Exact match hit | < 3ms | < 10ms | Redis GET, single operation |
| Exact match miss | < 5ms | < 15ms | Redis GET (not found) |
| Semantic hit | < 80ms | < 200ms | Embedding + OpenSearch kNN |
| Semantic miss | < 100ms | < 250ms | Embedding + OpenSearch kNN (no result) |
| Full lookup (exact miss + semantic miss) | < 100ms | < 250ms | Both tiers, no hit |
| Cache write | < 50ms | < 150ms | Parallel Redis + DynamoDB + OpenSearch |
| Lookup-or-exec (hit) | < 100ms | < 250ms | Same as lookup |
| Lookup-or-exec (miss) | Depends on callback | — | Dominated by LLM latency |

### Optimization Strategies

1. **Redis first, always**: Exact match in Redis is O(1) lookup — always check first
2. **Parallel writes**: Redis, DynamoDB, and OpenSearch writes happen concurrently via `asyncio.gather()`
3. **Embedding cache**: Cache query embeddings in Redis for 15 minutes to avoid re-embedding identical queries
4. **Connection pooling**: Persistent connections to Redis, OpenSearch, and DynamoDB across Lambda invocations
5. **VPC endpoint routing**: DynamoDB, Bedrock, and OpenSearch traffic stays within AWS network (no internet hop)
6. **Provisioned concurrency**: 10 warm Lambda instances to eliminate cold starts on the hot path
7. **Redis pipelining**: Batch multiple Redis operations in a single round-trip when possible
8. **OpenSearch pre-filtering**: Tenant and scope filters applied at the engine level, not post-filter

### Scaling Characteristics

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       SCALING MODEL                                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Lambda Concurrency                                                          │
│  └── Scales automatically with request volume                                │
│      └── Provisioned concurrency: 10 (hot path)                             │
│      └── Reserved concurrency: 200 (protect downstream)                     │
│                                                                              │
│  ElastiCache Redis                                                           │
│  └── Cluster mode with read replicas                                         │
│      └── Reads scale horizontally via replicas                               │
│      └── Writes scale via sharding (cluster mode)                           │
│      └── Memory: monitor and scale node type as cache grows                 │
│                                                                              │
│  DynamoDB                                                                    │
│  └── On-demand capacity — scales with read/write volume                      │
│      └── Cache writes: one per cache miss                                    │
│      └── Cache reads: only on Redis miss (fallback)                         │
│      └── Config reads: cached in Lambda memory                               │
│                                                                              │
│  OpenSearch Serverless                                                       │
│  └── Automatic scaling based on query volume and index size                 │
│      └── OCU min: 2 (cost optimization)                                     │
│      └── OCU max: 10 (scale ceiling)                                        │
│                                                                              │
│  Bedrock (Titan Embed)                                                       │
│  └── Managed service — scales automatically                                  │
│      └── Rate limit: 50 TPS (request increase if needed)                    │
│      └── Only called on semantic lookups (not exact matches)                │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Cost Model

The Cache Layer is a cost optimization block — its value is measured by the inference costs it prevents:

```
Cost Saved per Cache Hit:
  = (avg_input_tokens × input_price_per_token)
  + (avg_output_tokens × output_price_per_token)
  + (retrieval_cost_per_query)
  + (embedding_cost_per_query)

Example (Claude Sonnet):
  = (500 × $0.003/1K) + (300 × $0.015/1K) + ($0.001) + ($0.0001)
  = $0.0015 + $0.0045 + $0.001 + $0.0001
  = ~$0.0071 per cache hit

At 1000 cache hits/day:
  = ~$7.10/day saved
  = ~$213/month saved

Minus cache infrastructure cost:
  Redis (cache.r7g.large): ~$200/month
  OpenSearch Serverless (2 OCU): ~$350/month
  Lambda + DynamoDB: ~$50/month
  Total cache cost: ~$600/month

Break-even: ~2,800 cache hits/day
  (After break-even, every additional hit is pure savings)
```

---

## Implementation Phases

### Phase 1: Exact Match Caching (MVP)

**Goal:** Fast, hash-based caching for identical queries.

- Project scaffolding (SAM template, Poetry, project structure)
- Query normalization logic
- SHA-256 hash-based cache key construction
- Redis client with connection pooling (VPC deployment)
- POST /v1/cache/lookup (exact match only)
- POST /v1/cache/write
- DynamoDB table setup (cache entries, config)
- API key authentication via shared `bold-api-keys` table
- TTL-based expiration (Redis native + DynamoDB TTL)
- Basic DELETE /v1/cache/entries/{id}
- Request/response Pydantic models
- Unit tests with moto (DynamoDB) and fakeredis

### Phase 2: Semantic Similarity Caching

**Goal:** Embedding-based lookup for paraphrased queries.

- Bedrock Titan Embed v2 integration for query embedding
- OpenSearch Serverless setup (semantic cache index)
- kNN similarity search with tenant-scoped filtering
- Configurable similarity threshold
- Semantic cache write (embedding stored in OpenSearch)
- POST /v1/cache/lookup updated for two-tier pipeline (exact → semantic)
- Embedding caching in Redis (avoid re-embedding)
- Graceful degradation (skip semantic when Bedrock/OpenSearch unavailable)
- Integration tests against OpenSearch

### Phase 3: Cache Invalidation & Configuration

**Goal:** Comprehensive invalidation strategies and per-tenant configuration.

- POST /v1/cache/invalidate (bulk invalidation by criteria)
- POST /v1/cache/purge (full scope purge)
- EventBridge integration (subscribe to Doc Ingest, Model Gateway events)
- Event-driven invalidation Lambda
- Citation-based invalidation (invalidate entries citing updated documents)
- GET/PUT /v1/cache/config (per-project configuration)
- Namespace support (independent TTL and thresholds per namespace)
- Circuit breaker for external dependencies

### Phase 4: Advanced Features

**Goal:** Convenience endpoints, statistics, and cost attribution.

- POST /v1/cache/lookup-or-exec (lookup + callback on miss)
- GET /v1/cache/stats (hit/miss statistics)
- Stats aggregation Lambda (periodic CloudWatch-scheduled)
- Cost savings estimation (tokens saved × model pricing)
- Context-aware caching (hash system prompt + retrieval context)
- Admin permissions for purge/bulk operations
- DynamoDB GSI for citation-based lookups

### Phase 5: Optimization & Observability

**Goal:** Performance tuning, monitoring, and production readiness.

- Provisioned concurrency tuning
- Redis cluster mode configuration
- Redis pipelining for batch operations
- CloudWatch metrics and dashboards
- Alerting (low hit rate, Redis memory pressure, high latency)
- Latency breakdown logging (per-stage timing)
- Cost attribution per client
- Load testing and P99 optimization
- VPC endpoint configuration for all AWS services

---

## Appendix A: DynamoDB Schema

### Table: `bold-cache-layer`

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ PK                                    │ SK                                    │
├───────────────────────────────────────┼───────────────────────────────────────┤
│ CLIENT#acme-corp                      │ CACHE#customer-support#default#01JKX..│
│ CLIENT#acme-corp                      │ CACHE#customer-support#faq#01JKX...   │
│ CLIENT#acme-corp                      │ CACHE#hr-bot#default#01JKX...         │
│ CLIENT#acme-corp                      │ CONFIG#customer-support               │
│ CLIENT#acme-corp                      │ CONFIG#hr-bot                         │
│ CLIENT#acme-corp                      │ INVAL#2026-02-10T15:00:00Z#01JKX...  │
│ CLIENT#beta-inc                       │ CACHE#support#default#01JKX...        │
│ CLIENT#beta-inc                       │ CONFIG#support                        │
└──────────────────────────────────────────────────────────────────────────────┘
```

### GSI Access Patterns

| Access Pattern | GSI | Query |
|---------------|-----|-------|
| Exact match lookup (DynamoDB fallback) | `GSI-QueryHash` | PK=`CLIENT#X#HASH#Y` |
| List entries by project + namespace | `GSI-ProjectNamespace` | PK=`CLIENT#X#PROJECT#Y`, SK begins_with `NAMESPACE#Z` |
| Find entries citing a document | `GSI-Citation` | PK=`CLIENT#X#DOC#Y` |
| Cache statistics | `GSI-Stats` | PK=`CLIENT#X#PROJECT#Y`, SK begins_with `STATS#` |

---

## Appendix B: Similarity Algorithms & Formulas

### Query Normalization

```python
import re
import hashlib

def normalize_query(query: str) -> str:
    query = query.strip()
    query = query.lower()
    query = re.sub(r'\s+', ' ', query)
    query = re.sub(r'[?!.]+$', '?', query)
    return query

def compute_cache_key(client_id: str, project_id: str, namespace: str, query: str) -> str:
    normalized = normalize_query(query)
    query_hash = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    return f"{client_id}:{project_id}:{namespace}:{query_hash}"
```

### Cosine Similarity

```python
from numpy import dot
from numpy.linalg import norm

def cosine_similarity(a: list[float], b: list[float]) -> float:
    return dot(a, b) / (norm(a) * norm(b))

# Threshold: 0.92 default (configurable per client/project/namespace)
# Above threshold → semantic cache hit
# Below threshold → cache miss
```

### Cost Savings Estimation

```python
# Per-hit cost saved (estimated)
def estimate_cost_saved(tokens_input: int, tokens_output: int, model: str) -> float:
    pricing = {
        "anthropic.claude-sonnet-4-5-20250929": {
            "input": 3.00 / 1_000_000,   # $3.00 per 1M input tokens
            "output": 15.00 / 1_000_000   # $15.00 per 1M output tokens
        },
        "anthropic.claude-haiku-4-5-20251001": {
            "input": 0.80 / 1_000_000,
            "output": 4.00 / 1_000_000
        }
    }
    rates = pricing.get(model, pricing["anthropic.claude-sonnet-4-5-20250929"])
    return (tokens_input * rates["input"]) + (tokens_output * rates["output"])
```

### Cache Hit Rate Calculation

```
hit_rate = (exact_hits + semantic_hits) / total_lookups
exact_hit_rate = exact_hits / total_lookups
semantic_hit_rate = semantic_hits / total_lookups
semantic_contribution = semantic_hits / (exact_hits + semantic_hits)
```

---

## Appendix C: Monitoring & Observability

### Key Metrics

| Metric | Description | Alarm Threshold |
|--------|------------|----------------|
| `cache.lookup.latency` | Total lookup latency (p50, p99) | p99 > 300ms |
| `cache.lookup.hit_rate` | Overall cache hit rate | < 30% (may indicate config issue) |
| `cache.lookup.exact_hits` | Exact match hits per minute | Trend monitoring |
| `cache.lookup.semantic_hits` | Semantic hits per minute | Trend monitoring |
| `cache.lookup.misses` | Cache misses per minute | Baseline deviation |
| `cache.write.latency` | Cache write latency | p99 > 200ms |
| `cache.write.failures` | Failed cache writes per minute | > 0 |
| `cache.invalidation.count` | Entries invalidated per minute | Spike monitoring |
| `cache.entries.total` | Total active cache entries | Capacity monitoring |
| `cache.redis.memory_usage` | Redis memory utilization | > 80% |
| `cache.redis.evictions` | LRU evictions per minute | > 100/min |
| `cache.cost_saved_usd` | Estimated cost savings per hour | Trend monitoring |
| `cache.tokens_saved` | Tokens saved per hour (input + output) | Trend monitoring |
| `cache.embedding.latency` | Bedrock embedding generation latency | p99 > 200ms |
| `cache.opensearch.latency` | OpenSearch kNN query latency | p99 > 150ms |

### Structured Logging

All log entries follow the platform standard JSON format:

```json
{
  "timestamp": "2026-02-10T12:00:00.123Z",
  "level": "INFO",
  "service": "cache-layer",
  "request_id": "req-uuid-456",
  "client_id": "acme-corp",
  "project_id": "customer-support",
  "event": "cache_lookup",
  "status": "hit",
  "source": "semantic",
  "similarity_score": 0.946,
  "matched_query": "What's the process for resetting my password?",
  "exact_match_ms": 3,
  "semantic_ms": 84,
  "total_latency_ms": 87,
  "cache_entry_id": "ce-01JKX001...",
  "tokens_saved_input": 245,
  "tokens_saved_output": 180,
  "cost_saved_estimate_usd": 0.0042
}
```

### CloudWatch Dashboard

The Cache Layer publishes a pre-configured CloudWatch dashboard with:

1. **Cache Performance**: Hit rate over time (exact + semantic breakdown), miss rate
2. **Latency**: Lookup latency percentiles, write latency, per-stage breakdown
3. **Cost Savings**: Estimated USD saved, tokens saved, trending over time
4. **Infrastructure Health**: Redis memory usage, eviction rate, OpenSearch OCU utilization
5. **Invalidation Activity**: Invalidation events, entries purged, event sources
6. **Per-Client View**: Filterable by client_id for tenant-specific monitoring

### Alerting

| Alert | Condition | Action |
|-------|-----------|--------|
| Low hit rate | Hit rate < 20% for 1 hour | Investigate — threshold too high or cache not populated |
| High miss latency | p99 miss latency > 500ms | Investigate — Bedrock or OpenSearch slow |
| Redis memory pressure | Memory > 85% | Scale Redis node type or review TTLs |
| High eviction rate | > 500 evictions/min | Redis undersized — scale up |
| Cache write failures | Any write failure count > 0 | Investigate — DynamoDB or Redis issue |
| Invalidation spike | > 1000 invalidations in 5 min | Investigate — mass update event or misconfigured trigger |
| Embedding generation failures | > 0 failures | Investigate — Bedrock quota or availability |
