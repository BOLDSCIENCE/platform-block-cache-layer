# Platform Block Cache Layer

## Architecture Planning Document

**Version:** 2.0
**Status:** Draft
**Last Updated:** 2026-04-01

---

## Executive Summary

The Cache Layer is a platform block that provides intelligent response caching for AI applications, sitting directly in front of the Model Gateway to intercept repetitive queries before they incur LLM inference costs. In an enterprise environment where hundreds or thousands of users ask similar questions, the Cache Layer eliminates redundant LLM calls by returning cached responses for identical or semantically equivalent queries.

Without a cache layer, every "How do I reset my password?" from every user triggers a full LLM inference cycle — embedding generation, context retrieval, prompt assembly, and model invocation. With the Cache Layer, the first query is processed normally and its response is cached. The next identical or similar query returns immediately from cache at zero inference cost and near-zero latency.

The Cache Layer operates in the **Platform Context** (`APP#{application_id}#CLIENT#{client_id}`) per `constitution/multi-tenancy.md`. As a documented exception among platform blocks, it also supports `workspace_id` and `project_id` scoping for cache entries, since cache isolation at the workspace and project level is essential to prevent cross-domain cache pollution.

**Key Design Principles:**
- **Exact match caching** — hash-based DynamoDB lookup for identical queries returns cached responses in 5-10ms
- **Semantic similarity caching** — embedding-based lookup finds cached responses for paraphrased or equivalent queries
- **Per-tenant cache isolation** — caches are fully isolated per application + client, with workspace, project, and optional user scoping
- **Cache invalidation policies** — TTL-based, event-driven, and manual invalidation strategies to prevent stale responses
- **Cost attribution** — tracks cache hits/misses per tenant for billing and optimization insights
- **Transparent integration** — callers (Orchestration, Model Gateway) can enable caching with a single flag; no application-level changes required
- **Configurable similarity thresholds** — clients control how "similar" a query must be to trigger a cache hit, balancing freshness vs. cost savings

**Four Required Artifacts** (per `constitution/platform-blocks.md`):
1. **API** — FastAPI + Mangum behind API Gateway with custom domain
2. **SDK** — `boldsci-cache-layer` Python client for block-to-block consumption
3. **Admin UI Package** — `@boldscience/admin-cache-layer` React component library for `platform-block-admin`
4. **MCP Server** — `bold-cache-layer-mcp` agent-accessible interface

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
14. [Custom Domain & Service Discovery](#custom-domain--service-discovery)
15. [Infrastructure Components](#infrastructure-components)
16. [Error Handling](#error-handling)
17. [Performance & Scaling](#performance--scaling)
18. [SDK (`boldsci-cache-layer`)](#sdk-boldsci-cache-layer)
19. [Admin UI Package (`@boldscience/admin-cache-layer`)](#admin-ui-package-boldscienceadmin-cache-layer)
20. [MCP Server (`bold-cache-layer-mcp`)](#mcp-server-bold-cache-layer-mcp)
21. [Implementation Phases](#implementation-phases)
22. [Appendix A: DynamoDB Schema](#appendix-a-dynamodb-schema)
23. [Appendix B: Similarity Algorithms & Formulas](#appendix-b-similarity-algorithms--formulas)
24. [Appendix C: Monitoring & Observability](#appendix-c-monitoring--observability)

---

## Multi-Tenancy & Access Model

The Cache Layer operates in the **Platform Context** per `constitution/multi-tenancy.md`. Every operation MUST include `application_id` and `client_id`, resolved from the `AuthContext` provided by the `boldsci-auth` SDK. Cache entries are fully isolated per application + client — no cross-tenant cache sharing is ever possible.

### Platform Context Exception: Workspace & Project Scoping

Unlike most platform blocks (which only scope to `application_id` + `client_id`), the Cache Layer additionally supports `workspace_id` and `project_id` scoping. This is a documented exception required because:

- Cache entries are inherently domain-specific — a FAQ answer cached for a customer support project should never be returned for an HR bot query
- Workspace-level isolation prevents cross-organizational cache pollution within a client
- Project-level scoping aligns cache entries with the knowledge bases and configurations they were generated from

The `workspace_id` and `project_id` are provided as request parameters (not resolved from `AuthContext`, which only provides platform identity).

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          TENANCY HIERARCHY                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Application (application_id)                                                │
│  └── Product scope — e.g., "scicoms", "boldpubs", "maxiq"                  │
│      └── Platform-level isolation for API keys, config, rate limits        │
│      └── DynamoDB PK prefix: APP#{application_id}#CLIENT#{client_id}       │
│                                                                              │
│  Client (client_id)                                                          │
│  └── Top-level tenant — full cache isolation                                 │
│      └── Resolved from AuthContext via boldsci-auth SDK                     │
│      └── All rate limits and quotas bind to client_id                       │
│                                                                              │
│  Workspace (workspace_id) — Cache scope boundary [EXCEPTION]                │
│  └── Organizational container within a client                                │
│      └── Provided as request parameter, not from AuthContext                │
│      └── Cache entries scoped per workspace                                 │
│      └── Workspace-level TTL and threshold overrides                        │
│                                                                              │
│  Project (project_id) — Cache scope boundary [EXCEPTION]                     │
│  └── Caches are scoped per project within a workspace                       │
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
└─────────────────────────────────────────────────────────────────────────────┘
```

### Alignment with Other Blocks

| Aspect | Cache Layer | Model Gateway | Orchestration | Retrieval Service | Conversation Manager |
|--------|------------|---------------|---------------|-------------------|---------------------|
| **Context** | Platform (with exception) | Platform | Platform | Platform | Platform |
| **Top Level** | `application_id` + `client_id` | `application_id` + `client_id` | `application_id` + `client_id` | `application_id` + `client_id` | `application_id` + `client_id` |
| **Additional Scoping** | `workspace_id` + `project_id` (exception) | None | None | None | None |
| **Auth SDK** | `boldsci-auth` | `boldsci-auth` | `boldsci-auth` | `boldsci-auth` | `boldsci-auth` |
| **API Key** | Shared Lambda Authorizer | Shared Lambda Authorizer | Shared Lambda Authorizer | Shared Lambda Authorizer | Shared Lambda Authorizer |

---

## Core Design Philosophy

### Principle 1: Check Cache Before Inference (After Input Guardrails)

The Cache Layer sits on the critical path after Guardrails input scan but before the Model Gateway. Every query is checked against the cache after it has been sanitized by Guardrails. If a hit is found (and the guardrail policy version is current), the cached response is returned immediately, bypassing all downstream processing. This is the primary value proposition — eliminating redundant computation.

> See `platform-block-orchestration/ARCHITECTURE.md` § Canonical Pipeline Ordering for the authoritative 8-step pipeline that defines the relationship between Guardrails input scan (Step 1), Cache lookup (Step 2), and Guardrails output scan (Step 7).

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       REQUEST LIFECYCLE WITH CACHE                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  User Query                                                                  │
│      │                                                                       │
│      ▼                                                                       │
│  ┌──────────────────┐                                                       │
│  │ GUARDRAILS       │  ← PII redaction, injection detection                  │
│  │ (Input Scan)     │    MUST run before cache — query may contain PII       │
│  └────────┬─────────┘                                                       │
│           │ sanitized_query + guardrail_policy_version                       │
│           ▼                                                                  │
│  ┌──────────────────┐                                                       │
│  │ CACHE LAYER      │  ← Check exact match, then semantic similarity         │
│  │ (Cache Lookup)   │    on the sanitized query                              │
│  └────────┬─────────┘                                                       │
│           │                                                                  │
│     ┌─────┴──────┐                                                          │
│     │            │                                                          │
│   HIT          MISS (or stale policy version)                               │
│     │            │                                                          │
│     ▼            ▼                                                          │
│  Return       ┌──────────────────┐                                          │
│  cached       │ ORCHESTRATION    │  Retrieval → Prompt → Model Gateway      │
│  response     │ + MODEL GATEWAY  │  → Guardrails Output Scan                │
│  (5-10ms)     └────────┬─────────┘                                          │
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

Exact match caching is fast and cheap. Semantic caching is slower and more expensive (requires embedding generation + vector similarity search), but catches paraphrased queries that exact match misses. The Cache Layer runs both in sequence: exact match first (5-10ms via DynamoDB GetItem), then semantic similarity (50-150ms) only if exact match misses.

```
Query: "How do I reset my password?"
  │
  ├── Tier 1: EXACT MATCH (SHA-256 hash lookup via DynamoDB GetItem)
  │     └── Hash matches "How do I reset my password?" → HIT
  │
  ├── Tier 2: SEMANTIC SIMILARITY (only if Tier 1 misses)
  │     └── Embedding similarity to "What's the process for password reset?"
  │     └── Similarity score: 0.94 → above threshold (0.92) → HIT
  │
  └── MISS → proceed to Model Gateway
```

### Principle 3: Cache Isolation by Design

Enterprise customers require absolute data isolation. A cache entry created by Client A in Application X must never be returned to Client B or Application Y. Within a client, caches are further scoped by workspace and project to prevent cross-domain contamination. A FAQ answer cached for a customer support project should never be returned for an internal HR query, even within the same client.

### Principle 4: Staleness Is Worse Than a Cache Miss

A stale cached response (outdated information, wrong context) is worse than the cost of a fresh LLM call. The Cache Layer enforces aggressive invalidation policies:
- TTL-based expiration (default: 1 hour for semantic, 24 hours for exact match)
- Event-driven invalidation (knowledge base update → invalidate related caches)
- Manual purge API for operators
- Configurable per workspace, per project

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              CALLERS                                              │
│                                                                                  │
│    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│    │ Orchestration│  │ Model Gateway│  │  Chat UIs    │  │  Direct API  │      │
│    │ Block        │  │ (pre-check)  │  │              │  │  Consumers   │      │
│    │ (via SDK)    │  │ (via SDK)    │  │              │  │              │      │
│    └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
│           │                 │                  │                  │              │
└───────────┼─────────────────┼──────────────────┼──────────────────┼──────────────┘
            │                 │                  │                  │
            └─────────────────┴────────┬─────────┴──────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│        API GATEWAY + LAMBDA (cache-layer-api.{env}.boldquantum.com)             │
│                                                                                  │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                       Cache Layer API                                      │  │
│  │                                                                            │  │
│  │  GET  /health                     — Health check (no auth)                │  │
│  │  POST /v1/cache/lookup            — Check cache (exact + semantic)        │  │
│  │  POST /v1/cache/write             — Write a response to cache             │  │
│  │  POST /v1/cache/lookup-or-exec    — Lookup, and on miss execute callback  │  │
│  │                                                                            │  │
│  │  DELETE /v1/cache/entries/{id}    — Invalidate a specific cache entry     │  │
│  │  POST /v1/cache/invalidate        — Bulk invalidation by scope/query      │  │
│  │  POST /v1/cache/purge             — Purge all cache for a scope           │  │
│  │                                                                            │  │
│  │  GET  /v1/cache/stats             — Cache hit/miss statistics             │  │
│  │  GET  /v1/cache/config            — Get cache configuration               │  │
│  │  PUT  /v1/cache/config            — Update cache configuration            │  │
│  │                                                                            │  │
│  └───────────────────────────────────────────────────────────────────────────┘  │
│                                       │                                          │
└───────────────────────────────────────┼──────────────────────────────────────────┘
                                        │
          ┌─────────────────────────────┴──────────────────────────┐
          │                                                        │
          ▼                                                        ▼
┌──────────────────┐                                    ┌──────────────────┐
│    DynamoDB      │                                    │  Model Gateway   │
│                  │                                    │  (via boldsci-   │
│ • Cache entries  │                                    │   model-gateway  │
│   (exact match + │                                    │   SDK)           │
│   durable store) │                                    │                  │
│ • Cache config   │                                    │ • Query          │
│ • Invalidation   │                                    │   embedding      │
│   events         │                                    │   generation     │
│ • Stats / audit  │                                    │   (POST /v1/     │
│                  │                                    │   embed)         │
└──────────────────┘                                    └──────────────────┘
          │
          ▼
┌──────────────────┐
│   OpenSearch     │
│   (Provisioned)  │
│                  │
│ • Cache Layer    │
│   provisions the │
│   shared domain  │
│ • Index:         │
│   bold-semantic- │
│   cache          │
│ • kNN similarity │
│   search         │
│ • Tenant-scoped  │
│   queries        │
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
  │     ├── Build scope: {application_id}:{client_id}:{workspace_id}:{project_id}
  │     ├── SHA-256 hash of normalized query
  │     └── Cache key components for DynamoDB GetItem
  │
  ├── Step 3: EXACT MATCH LOOKUP (DynamoDB GetItem)
  │     ├── GetItem with PK + SK (query hash lookup via GSI)
  │     ├── If found and not expired → EXACT HIT
  │     │     └── Return cached response + metadata
  │     └── If not found → proceed to Step 4
  │
  ├── Step 4: SEMANTIC SIMILARITY LOOKUP (if enabled)
  │     ├── Generate query embedding via boldsci-model-gateway SDK
  │     ├── kNN search in OpenSearch (scoped to application + client + workspace + project)
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
    "workspace_id": "ws_01JKX...",
    "project_id": "customer-support"
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `enable_exact_match` | `true` | Check exact match cache (DynamoDB GetItem) |
| `enable_semantic` | `true` | Check semantic similarity cache (OpenSearch) |
| `similarity_threshold` | `0.92` | Minimum cosine similarity for semantic hit |
| `max_age_seconds` | `null` (use entry TTL) | Override: only return entries younger than this |
| `workspace_id` | required | Workspace scope for cache lookup |
| `project_id` | required | Project scope within the workspace |

---

## Exact Match Caching

### How It Works

Exact match caching uses a deterministic hash of the normalized query string as the cache key. If two queries produce the same hash, they are treated as identical. This is fast (5-10ms via DynamoDB GetItem), cheap (no embedding cost), and precise (no false positives).

### Cache Key Construction

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       CACHE KEY STRUCTURE                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Components:                                                                 │
│  ┌───────────────┬────────────┬──────────────┬────────────┬──────────────┐ │
│  │application_id │ client_id  │ workspace_id │ project_id │ query_hash   │ │
│  │ (platform)    │ (tenant)   │ (scope)      │ (scope)    │ (SHA-256)    │ │
│  └──────┬────────┴─────┬──────┴──────┬───────┴─────┬──────┴──────┬───────┘ │
│         │              │             │             │             │          │
│         ▼              ▼             ▼             ▼             ▼          │
│    "scicoms"     "acme-corp"   "ws_01JKX..."  "cust-supp"  "a7f3b2c1..."  │
│                                                                              │
│  DynamoDB PK: APP#scicoms#CLIENT#acme-corp                                  │
│  DynamoDB SK: CACHE#ws_01JKX...#cust-supp#a7f3b2c1...                      │
│                                                                              │
│  Optional context hash (when context_aware_caching is enabled):            │
│  └── Includes hash of system prompt + retrieval context                    │
│      └── Same query with different context = different cache entry          │
│      └── Appended to SK: ...#ctx_d4e5f6                                    │
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

### DynamoDB Storage Format

Exact match entries are stored directly in DynamoDB. A GetItem on the GSI-QueryHash index provides O(1) lookup:

```json
{
  "PK": "APP#scicoms#CLIENT#acme-corp",
  "SK": "CACHE#ws_01JKX...#customer-support#ce-01JKX001...",
  "cache_entry_id": "ce-01JKX001...",
  "workspace_id": "ws_01JKX...",
  "project_id": "customer-support",
  "query_normalized": "how do i reset my password?",
  "query_hash": "a7f3b2c1...",
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
  "guardrail_policy_version": "v3",
  "hit_count": 47,
  "last_hit_at": "2026-02-10T14:30:00Z",
  "created_at": "2026-02-10T12:00:00Z",
  "created_by_user": "user-abc123",
  "original_request_id": "req-uuid-456",
  "status": "active",
  "ttl": 1707696000
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

Semantic similarity caching captures paraphrased, reworded, or rephrased versions of the same question. When an exact match misses, the Cache Layer generates an embedding of the query and performs a kNN similarity search against all cached query embeddings within the same scope (application + client + workspace + project).

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

Query embeddings are generated via the `boldsci-model-gateway` SDK (the standard typed Python client for Model Gateway, per `constitution/platform-blocks.md`):

```python
from boldsci.model_gateway import ModelGatewayClient

mg_client = ModelGatewayClient()
embedding_response = mg_client.embed(
    model="titan-embed-text",
    input=normalized_query,
    dimensions=1024,
    normalize=True,
    application_id=auth.application_id,
    client_id=auth.client_id,
)
query_embedding = embedding_response.embeddings[0].embedding
```

The SDK handles:
- Service discovery via SSM (`/bold/model-gateway/api-url`)
- Auth header injection (`X-Service-Key` + `X-Forwarded-Client-Id`)
- Retry and error handling

The resulting 1024-dimensional vector is stored in OpenSearch alongside the cache entry reference.

> Prior to SDK consolidation, the Cache Layer called Model Gateway HTTP endpoints directly. All block-to-block communication now uses the target block's SDK per `constitution/platform-blocks.md` § Section 7.

### OpenSearch Semantic Cache Index

The Cache Layer provisions the shared OpenSearch domain (it is the first platform block to require OpenSearch). The domain endpoint is registered in SSM at `/bold/opensearch/domain-endpoint` for other blocks to discover.

```json
{
  "mappings": {
    "properties": {
      "cache_entry_id": { "type": "keyword" },
      "application_id": { "type": "keyword" },
      "client_id": { "type": "keyword" },
      "workspace_id": { "type": "keyword" },
      "project_id": { "type": "keyword" },
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
        { "term": { "application_id": "scicoms" } },
        { "term": { "client_id": "acme-corp" } },
        { "term": { "workspace_id": "ws_01JKX..." } },
        { "term": { "project_id": "customer-support" } },
        { "range": { "expires_at": { "gte": "now" } } }
      ]
    }
  },
  "min_score": 0.92
}
```

The `min_score` parameter ensures only results above the similarity threshold are returned. The `application_id` and `client_id` filters enforce tenant isolation at the query level per `constitution/multi-tenancy.md` Layer 3.

### Semantic Match Response

When a semantic hit is found, the response includes the matched query for transparency:

```json
{
  "status": "hit",
  "source": "semantic",
  "similarity_score": 0.946,
  "matched_query": "What's the process for resetting my password?",
  "original_query": "How do I reset my password?",
  "response": { "..." },
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
  │     ├── Generate query embedding (boldsci-model-gateway SDK)
  │     └── Compute TTL from project config or default
  │
  ├── Step 3: WRITE TO STORES (parallel)
  │     ├── Write to DynamoDB (exact match cache + durable record + metadata)
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

Writes to the two stores (DynamoDB, OpenSearch) happen in parallel for performance. If any write fails:
- **DynamoDB write fails**: Retry once. If still fails, do not cache this response (data integrity). DynamoDB is the primary store.
- **OpenSearch write fails**: Log warning, continue. Semantic lookup will miss, but exact match still works via DynamoDB.

### Guardrail Policy Versioning

Cache entries store a `guardrail_policy_version` field that records which version of the Guardrails safety policies were in effect when the response was generated and validated.

**On cache write (Step 8 of canonical pipeline):**
- The Orchestration block passes the current `guardrail_policy_version` (obtained from the `X-Guardrail-Policy-Version` response header on the Guardrails scan calls)
- The Cache Layer stores this version alongside the cache entry in both stores (DynamoDB, OpenSearch)

**On cache hit (Step 2 of canonical pipeline):**
- The Cache Layer returns the cached `guardrail_policy_version` in the lookup response
- The Orchestration block compares it against the current policy version
- If versions match → valid cache hit, return cached response
- If versions differ → stale cache hit, treat as miss and re-execute the full pipeline

**On policy change:**
- When the Guardrails block publishes a `bold.guardrails.PolicyVersionChanged` event to EventBridge, the Cache Layer receives it
- The event handler can optionally bulk-invalidate entries with the old policy version for proactive cleanup
- Even without proactive cleanup, stale entries are detected at lookup time via version comparison

```json
{
  "source": "bold.guardrails",
  "detail-type": "PolicyVersionChanged",
  "detail": {
    "client_id": "acme-corp",
    "application_id": "scicoms",
    "previous_version": "v3",
    "new_version": "v4",
    "changed_policies": ["content-safety", "pii-detection"],
    "timestamp": "2026-02-10T14:00:00.000Z"
  }
}
```

> See `platform-block-orchestration/ARCHITECTURE.md` § Canonical Pipeline Ordering for where this fits in the end-to-end flow.

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
│      └── DynamoDB: TTL attribute (automatic, periodic cleanup)              │
│      └── OpenSearch: expires_at filter on lookup (query-time filtering)     │
│      └── Default TTLs:                                                      │
│          └── Exact match: 24 hours                                          │
│          └── Semantic match: 1 hour (more conservative — paraphrase risk)   │
│          └── Configurable per project                                        │
│                                                                              │
│  Strategy 2: EVENT-DRIVEN INVALIDATION                                       │
│  └── External events trigger cache invalidation                              │
│      └── Knowledge base update (Doc Ingest publishes event)                 │
│          └── Invalidate all semantic cache entries for affected project      │
│      └── Policy change (Guardrails publishes PolicyVersionChanged)           │
│          └── Cache entries with stale guardrail_policy_version are          │
│              detected at lookup time (version mismatch → treat as miss)    │
│          └── Optional: bulk-invalidate entries with old policy version     │
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
└─────────────────────────────────────────────────────────────────────────────┘
```

### Event-Driven Invalidation via EventBridge

The Cache Layer subscribes to platform events that indicate cached data may be stale:

```json
{
  "source": "bold.doc-ingest",
  "detail-type": "DocumentIngested",
  "detail": {
    "application_id": "scicoms",
    "client_id": "acme-corp",
    "workspace_id": "ws_01JKX...",
    "project_id": "customer-support",
    "document_id": "doc-uuid-001",
    "action": "updated"
  }
}
```

When this event is received:
1. Look up all cache entries for `application_id=scicoms, client_id=acme-corp, workspace_id=ws_01JKX..., project_id=customer-support`
2. Check if any cached responses referenced `document_id=doc-uuid-001` in their citations
3. Invalidate matching entries (mark as invalidated in DynamoDB, delete from OpenSearch)

```json
{
  "source": "bold.model-gateway",
  "detail-type": "ModelVersionChanged",
  "detail": {
    "application_id": "scicoms",
    "client_id": "acme-corp",
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
| By project | Invalidate all entries in a project | Knowledge base overhaul |
| By workspace | Invalidate all entries in a workspace | Workspace-wide refresh |
| By client | Invalidate all entries for a client | Full cache reset |
| By citation | Invalidate entries citing a specific document | Document updated |

---

## API Design

All endpoints are authenticated via the shared Lambda Authorizer (resolved from SSM at `/bold/auth/authorizer-arn`) except `GET /health`. The `AuthContext` provides `application_id` and `client_id` via the `boldsci-auth` SDK.

### GET /health

Health check endpoint. No auth required. Used for service discovery and monitoring.

**Response:**

```json
{
  "status": "healthy",
  "service": "cache-layer",
  "version": "2.0.0",
  "timestamp": "2026-04-01T12:00:00Z"
}
```

### POST /v1/cache/lookup

Check the cache for a query. Returns a hit (with cached response) or a miss.

**Request:**

```json
{
  "workspace_id": "ws_01JKX...",
  "project_id": "customer-support",
  "query": "How do I reset my password?",
  "request_id": "req-uuid-456",
  "lookup_config": {
    "enable_exact_match": true,
    "enable_semantic": true,
    "similarity_threshold": 0.92,
    "max_age_seconds": null
  },
  "context_hash_inputs": null
}
```

> `application_id` and `client_id` are resolved from `AuthContext` (injected by the shared Lambda Authorizer) — not provided in the request body.

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
  "lookup_latency_ms": 7,
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
  "response": { "..." },
  "cache_metadata": { "..." },
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
    "exact_match_ms": 7,
    "semantic_ms": 85,
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
  "workspace_id": "ws_01JKX...",
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
  "workspace_id": "ws_01JKX...",
  "project_id": "customer-support",
  "query": "How do I reset my password?",
  "request_id": "req-uuid-789",
  "lookup_config": {
    "enable_exact_match": true,
    "enable_semantic": true,
    "similarity_threshold": 0.92
  },
  "on_miss": {
    "callback_url": "https://model-api.dev.boldquantum.com/v1/invoke",
    "callback_method": "POST",
    "callback_body": {
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
  "workspace_id": "ws_01JKX...",
  "project_id": "customer-support",
  "invalidation_criteria": {
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
  "invalidation_criteria": { "..." },
  "created_at": "2026-02-10T15:00:00Z"
}
```

### POST /v1/cache/purge

Purge all cache entries for a scope.

**Request:**

```json
{
  "workspace_id": "ws_01JKX...",
  "project_id": "customer-support",
  "confirm": true
}
```

When `project_id` is null, all projects in the workspace are purged. The `confirm` field must be `true` to prevent accidental purges.

**Response:**

```json
{
  "request_id": "req-uuid-888",
  "entries_purged": 1247,
  "scope": {
    "application_id": "scicoms",
    "client_id": "acme-corp",
    "workspace_id": "ws_01JKX...",
    "project_id": "customer-support"
  },
  "created_at": "2026-02-10T15:00:00Z"
}
```

### GET /v1/cache/stats

Get cache statistics for a scope.

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `workspace_id` | string | null | Filter by workspace |
| `project_id` | string | null | Filter by project |
| `period` | string | `"24h"` | Stats period: `"1h"`, `"24h"`, `"7d"`, `"30d"` |

> `application_id` and `client_id` are resolved from `AuthContext`.

**Response:**

```json
{
  "application_id": "scicoms",
  "client_id": "acme-corp",
  "workspace_id": "ws_01JKX...",
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
    "avg_exact_latency_ms": 7,
    "avg_semantic_latency_ms": 85,
    "avg_miss_latency_ms": 92,
    "total_entries": 342,
    "estimated_cost_saved_usd": 22.76,
    "estimated_tokens_saved": {
      "input": 1234500,
      "output": 876200
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
  "workspace_id": "ws_01JKX...",
  "project_id": "customer-support",
  "config": {
    "enabled": true,
    "default_ttl_seconds": 86400,
    "semantic_ttl_seconds": 3600,
    "similarity_threshold": 0.92,
    "max_entry_size_bytes": 102400,
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
| `UNAUTHORIZED` | 401 | Invalid or missing credentials |
| `FORBIDDEN` | 403 | Insufficient scopes for requested operation |
| `CACHE_ENTRY_NOT_FOUND` | 404 | Referenced cache entry does not exist |
| `CACHE_WRITE_FAILED` | 500 | Failed to write cache entry (DynamoDB failure) |
| `EMBEDDING_ERROR` | 502 | Model Gateway embedding generation failed |
| `OPENSEARCH_ERROR` | 502 | OpenSearch unavailable or query failed |
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
  ├── scoped to Application + Client + Workspace + Project
  │
  ├── contains:
  │     ├── query_normalized: original normalized query string
  │     ├── query_hash: SHA-256 hash for exact match
  │     ├── response: full cached response payload
  │     ├── citations: referenced document/chunk IDs
  │     ├── guardrail_policy_version: string (policy version at cache write time)
  │     └── metadata: hit count, timestamps, original request info
  │
  ├── stored in:
  │     ├── DynamoDB (primary store — exact match cache + durable record + audit trail)
  │     └── OpenSearch (semantic cache — embedding + metadata)
  │
  └── lifecycle:
        ├── created → active (serving hits)
        ├── active → expired (TTL reached)
        ├── active → invalidated (manual or event-driven)
        └── expired/invalidated → deleted (cleanup)

CacheConfig
  │
  ├── scoped to Application + Client + Workspace + Project
  ├── contains: TTLs, thresholds, project configs, event subscriptions
  └── stored in: DynamoDB

InvalidationEvent
  │
  ├── scoped to Application + Client
  ├── contains: criteria, entries affected, timestamp, source
  └── stored in: DynamoDB (audit trail)
```

### DynamoDB Table: `bold-cache-layer`

Single-table design following platform conventions. Uses the Platform Context PK pattern.

#### Cache Entry Entity

```
PK: APP#{application_id}#CLIENT#{client_id}
SK: CACHE#WS#{workspace_id}#PROJ#{project_id}#{cache_entry_id}

Attributes:
- cache_entry_id: string (ULID)
- application_id: string
- client_id: string
- workspace_id: string
- project_id: string
- query_normalized: string
- query_hash: string (SHA-256)
- response: Map (full cached response payload)
- citations: List<Map> (referenced document/chunk IDs)
- model: string (model that generated the response)
- tokens_used: Map { input, output }
- guardrail_policy_version: string
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
PK: APP#{application_id}#CLIENT#{client_id}
SK: CONFIG#WS#{workspace_id}#PROJ#{project_id}

Attributes:
- workspace_id: string
- project_id: string
- enabled: boolean
- default_ttl_seconds: integer
- semantic_ttl_seconds: integer
- similarity_threshold: number (0.0 – 1.0)
- max_entry_size_bytes: integer
- context_aware_caching: boolean
- event_driven_invalidation: boolean
- invalidation_events: List<string>
- updated_at: string (ISO 8601)
- updated_by: string (user_id)
```

#### Invalidation Event Entity

```
PK: APP#{application_id}#CLIENT#{client_id}
SK: INVAL#{timestamp}#{event_id}

Attributes:
- event_id: string (ULID)
- workspace_id: string
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
| `GSI-QueryHash` | `APP#{application_id}#CLIENT#{client_id}#HASH#{query_hash}` | `CACHE#{cache_entry_id}` | Fast exact match lookup by query hash |
| `GSI-ProjectEntries` | `APP#{application_id}#CLIENT#{client_id}#WS#{workspace_id}#PROJ#{project_id}` | `CREATED#{created_at}` | List entries by workspace + project |
| `GSI-Citation` | `APP#{application_id}#CLIENT#{client_id}#DOC#{document_id}` | `CACHE#{cache_entry_id}` | Find cache entries citing a specific document (for invalidation) |
| `GSI-Stats` | `APP#{application_id}#CLIENT#{client_id}#WS#{workspace_id}#PROJ#{project_id}` | `STATS#{period}#{timestamp}` | Cache statistics aggregation |

---

## Authentication & Authorization

Authentication is handled by the shared Lambda Authorizer from the **Auth Block** (`boldsci-auth`). The Cache Layer consumes the authorizer and SDK per `constitution/platform-blocks.md`.

### Auth Block SDK Integration

```python
from boldsci.auth import get_auth_context, require_scope, AuthContext

async def cache_lookup_handler(event, context):
    # Extract AuthContext from shared Lambda Authorizer
    auth: AuthContext = get_auth_context(event)
    # auth.application_id → "scicoms"
    # auth.client_id → "acme-corp"
    # auth.scopes → ["cache:read", "cache:write"]
    # auth.auth_method → "api_key" | "cognito_jwt" | "service_key"
    # auth.key_id → "key_01JKX..."

    # Enforce required scope
    require_scope(auth, "cache:read")

    # workspace_id and project_id from request body (not AuthContext)
    body = json.loads(event["body"])
    workspace_id = body["workspace_id"]
    project_id = body["project_id"]
```

### Authorizer Attachment

The shared Lambda Authorizer ARN is resolved via SSM at deploy time:

```
/bold/auth/authorizer-arn
```

The Cache Layer's Terraform module references this parameter to attach the authorizer to its API Gateway. The Cache Layer does NOT deploy its own authorizer.

### Supported Auth Methods

| Method | Header | Resolves | Use Case |
|--------|--------|----------|----------|
| API Key | `X-API-Key` | `application_id`, `client_id`, scopes | External API consumers |
| Cognito JWT | `Authorization: Bearer <token>` | `client_id`, `user_id`, scopes | Web/mobile users (Admin UI) |
| Service Key | `X-Service-Key` | calling service identity | Block-to-block calls (via SDK) |

### Required Scopes

| Endpoint | Required Scope |
|----------|---------------|
| `GET /health` | None (no auth) |
| `POST /v1/cache/lookup` | `cache:read` |
| `GET /v1/cache/stats` | `cache:read` |
| `GET /v1/cache/config` | `cache:read` |
| `POST /v1/cache/write` | `cache:write` |
| `DELETE /v1/cache/entries/{id}` | `cache:write` |
| `POST /v1/cache/invalidate` | `cache:write` |
| `POST /v1/cache/purge` | `cache:admin` |
| `PUT /v1/cache/config` | `cache:admin` |

### Internal Calls

When this block calls Model Gateway (for embedding generation), it uses the `boldsci-model-gateway` SDK, which handles:
- `X-Service-Key` header for authentication
- `X-Forwarded-Client-Id` header for quota attribution
- Service discovery via SSM (`/bold/model-gateway/api-url`)

---

## Platform Integration

### Integration with Orchestration Block

The primary integration pattern. The Cache Layer operates at Steps 2 and 8 of the canonical pipeline (see `platform-block-orchestration/ARCHITECTURE.md` § Canonical Pipeline Ordering):

```
Canonical Pipeline (Cache Layer's role):
  Step 1. Guardrails Input Scan — PII redaction, injection detection
          → produces sanitized_query + guardrail_policy_version
  Step 2. Cache Layer lookup (via boldsci-cache-layer SDK)
          → If HIT and policy version current → return cached response (skip 3-7)
          → If HIT but policy version stale → treat as MISS
          → If MISS → continue
  Steps 3-6. [Retrieval → Prompt → Context → Model Gateway]
  Step 7. Guardrails Output Scan
          → If blocked → return safe fallback, do NOT cache
  Step 8. Cache Layer write (via boldsci-cache-layer SDK)
          → Store response + guardrail_policy_version
```

> **Key**: Guardrails input scan (Step 1) MUST run before cache lookup (Step 2) because the user's query may contain PII that must be redacted before entering any cache index.

### Integration with Model Gateway

The Cache Layer uses the `boldsci-model-gateway` SDK for embedding generation:

```python
from boldsci.model_gateway import ModelGatewayClient

# SDK handles SSM discovery, auth headers, retry
mg_client = ModelGatewayClient()
response = mg_client.embed(
    model="titan-embed-text",
    input=normalized_query,
    dimensions=1024,
    normalize=True,
    application_id=auth.application_id,
    client_id=auth.client_id,
)
```

The Model Gateway can also optionally call the Cache Layer (via `boldsci-cache-layer` SDK) as inline middleware for LLM request deduplication.

### Integration with Doc Ingest (Event-Driven)

The Cache Layer subscribes to Doc Ingest events to invalidate stale caches:

```
Doc Ingest publishes:
  EventBridge → "DocumentIngested" / "DocumentUpdated" / "DocumentDeleted"

Cache Layer consumes:
  1. Receive event
  2. Look up cache entries citing the affected document
  3. Invalidate matching entries (mark as invalidated in DynamoDB, delete from OpenSearch)
```

### Integration with Guardrails Block

The Cache Layer respects guardrail verdicts:
- Responses that received a "warn" or "block" verdict from Guardrails are NOT cached
- If a cached response is later found to violate a new guardrail policy, the event-driven invalidation system purges it

### Cache Layer External Dependencies

The Cache Layer is intentionally minimal in its downstream dependencies:

| Dependency | How Consumed | Purpose |
|-----------|-------------|---------|
| **Model Gateway** | `boldsci-model-gateway` SDK | Query embedding generation via `embed()` |
| **DynamoDB** | `boto3` via repository layer | Primary store — exact match cache, config, audit |
| **OpenSearch** | `opensearch-py` client | Semantic similarity kNN search |
| **EventBridge** | Lambda event source | Consuming invalidation events |

---

## Custom Domain & Service Discovery

Per `constitution/platform-blocks.md` § Section 6, the Cache Layer is accessible via a stable custom domain.

### Domain

| Environment | Domain |
|-------------|--------|
| **dev** | `cache-layer-api.dev.boldquantum.com` |
| **staging** | `cache-layer-api.staging.boldquantum.com` |
| **prod** | `cache-layer-api.boldquantum.com` |

### SSM Registration

The Cache Layer registers its API URL at:

```
/bold/cache-layer/api-url
```

This allows the `boldsci-cache-layer` SDK and other blocks to discover the API at deploy time.

### Shared Infrastructure References

The Terraform module references shared DNS infrastructure via SSM:

| SSM Parameter | Purpose |
|--------------|---------|
| `/bold/dns/{env}/hosted-zone-id` | Route 53 hosted zone ID |
| `/bold/dns/{env}/wildcard-cert-arn` | ACM wildcard certificate ARN |
| `/bold/auth/authorizer-arn` | Shared Lambda Authorizer ARN |

### CORS Configuration

Both API Gateway and FastAPI CORSMiddleware must be configured with identical origins:

- **Dev:** `https://admin.dev.boldquantum.com` + `http://localhost:5173`
- **Staging:** `https://admin.staging.boldquantum.com`
- **Prod:** `https://admin.boldquantum.com`

> **Important:** FastAPI + API Gateway deployments have two independent CORS layers that must stay in sync. Updating one without the other will cause preflight failures or missing CORS headers on responses.

### OpenAPI Spec

The FastAPI app generates an OpenAPI spec (`openapi.json`) that serves as the contract for the SDK and MCP Server. This spec is available at the `/openapi.json` endpoint.

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
│  └── API Gateway (HTTP API)                                                  │
│      └── Custom domain: cache-layer-api.{env}.boldquantum.com              │
│      └── Routes mapped to Lambda                                             │
│      └── Shared Lambda Authorizer attached (SSM: /bold/auth/authorizer-arn) │
│      └── CORS configured for admin UI domains                               │
│                                                                              │
│  Storage                                                                     │
│  └── DynamoDB                                                                │
│      └── Table: bold-cache-layer                                             │
│      └── PK: APP#{application_id}#CLIENT#{client_id}                        │
│      └── On-demand capacity (burst-friendly)                                 │
│      └── Point-in-time recovery enabled                                      │
│      └── TTL enabled (cache entry and audit retention)                      │
│                                                                              │
│  Search                                                                      │
│  └── OpenSearch (Provisioned)                                                │
│      └── Instance: t3.small.search (~$25/month)                             │
│      └── Cache Layer provisions the shared domain                            │
│      └── Endpoint registered at SSM: /bold/opensearch/domain-endpoint       │
│      └── Index: bold-semantic-cache (owned by Cache Layer)                   │
│      └── Vector engine: HNSW with cosine similarity, 1024 dimensions        │
│      └── Used exclusively for semantic cache kNN lookups                    │
│                                                                              │
│  AI/ML                                                                       │
│  └── Model Gateway (via boldsci-model-gateway SDK)                          │
│      └── Query embedding for semantic cache                                 │
│      └── Routes to Titan Embed v2, Dimension: 1024                          │
│                                                                              │
│  Events                                                                      │
│  └── Amazon EventBridge                                                      │
│      └── Subscribes to: bold.doc-ingest, bold.model-gateway events         │
│      └── Triggers invalidation Lambda                                        │
│                                                                              │
│  DNS                                                                         │
│  └── Route 53                                                                │
│      └── A (alias) record: cache-layer-api.{env}.boldquantum.com           │
│      └── Points to API Gateway custom domain                                │
│      └── Uses shared hosted zone from SSM                                   │
│                                                                              │
│  Monitoring                                                                  │
│  └── CloudWatch                                                              │
│      └── Metrics: hit rate, latency, invalidation rate                      │
│      └── Alarms: low hit rate, high miss rate, high latency                 │
│      └── Logs: structured JSON logging (structlog)                          │
│  └── ADOT (AWS Distro for OpenTelemetry)                                    │
│      └── X-Ray tracing on all Lambda functions                              │
│      └── FastAPIInstrumentor, BotocoreInstrumentor                          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Lambda Functions

| Function | Memory | Timeout | Purpose |
|----------|--------|---------|---------|
| cache-api | 512 MB | 30s | Main API (lookup, write, invalidate, stats, health) |
| cache-event-handler | 256 MB | 60s | EventBridge invalidation event consumer |
| cache-stats-aggregator | 256 MB | 120s | Periodic stats aggregation (CloudWatch scheduled) |

### Project Structure (Terraform + uv)

```
platform-block-cache-layer/
├── ARCHITECTURE.md
├── pyproject.toml                    # uv (package management)
├── uv.lock                           # Locked dependencies (committed)
├── terraform/                        # Terraform (infrastructure)
│   ├── main.tf                       # Provider, backend, data sources
│   ├── variables.tf                  # Input variables (env, etc.)
│   ├── outputs.tf                    # Output values
│   ├── lambda.tf                     # Lambda functions + layers
│   ├── dynamodb.tf                   # DynamoDB table + GSIs
│   ├── opensearch.tf                 # Provisioned OpenSearch domain
│   ├── api-gateway.tf                # API Gateway + custom domain + CORS
│   ├── eventbridge.tf                # EventBridge rules + targets
│   └── ssm.tf                        # SSM parameter registration
├── src/                              # API code
│   ├── main.py                       # FastAPI app, router registration, Mangum handler
│   ├── common/                       # Shared utilities, base classes
│   │   ├── exceptions.py             # Domain exception hierarchy (AppError → ...)
│   │   ├── dependencies.py           # Shared FastAPI dependencies (auth, DynamoDB, settings)
│   │   ├── middleware.py             # Cross-cutting middleware
│   │   └── base_models.py           # Pydantic ApiModel base with camelCase
│   ├── cache/                        # Cache domain module
│   │   ├── router.py                 # FastAPI router — thin, delegates to service
│   │   ├── dependencies.py           # get_cache_service, get_cache_repository
│   │   ├── service.py                # Cache lookup/write pipeline orchestration
│   │   ├── repository.py             # DynamoDB operations (exact match, config, audit)
│   │   ├── schemas.py                # Request/response Pydantic models
│   │   ├── models.py                 # CacheEntryModel, CacheConfigModel
│   │   └── exceptions.py             # Cache-specific exceptions
│   ├── semantic/                     # Semantic similarity domain module
│   │   ├── service.py                # Semantic lookup/write logic
│   │   ├── repository.py             # OpenSearch kNN operations
│   │   └── schemas.py                # Semantic-specific models
│   ├── invalidation/                 # Invalidation domain module
│   │   ├── router.py                 # Invalidation/purge endpoints
│   │   ├── service.py                # Invalidation logic
│   │   ├── event_handler.py          # EventBridge event consumer (Lambda entry point)
│   │   └── schemas.py                # Invalidation request/response models
│   ├── stats/                        # Statistics domain module
│   │   ├── router.py                 # Stats/config endpoints
│   │   ├── service.py                # Stats aggregation logic
│   │   └── schemas.py                # Stats models
│   └── clients/                      # External service clients
│       ├── opensearch_client.py      # OpenSearch connection + kNN queries
│       └── model_gateway_client.py   # Wrapper around boldsci-model-gateway SDK
├── sdk/                              # boldsci-cache-layer (Python SDK)
│   ├── pyproject.toml
│   ├── src/
│   │   └── boldsci/
│   │       └── cache_layer/
│   │           ├── __init__.py
│   │           ├── client.py         # CacheLayerClient — typed API wrapper
│   │           ├── models.py         # Pydantic request/response models
│   │           └── exceptions.py     # SDK exceptions
│   └── tests/
├── ui/                               # @boldscience/admin-cache-layer (Admin UI Package)
│   ├── src/
│   │   ├── components/               # Block-specific components
│   │   ├── pages/                    # Page components for platform-block-admin
│   │   ├── hooks/                    # TanStack Query hooks for cache API
│   │   ├── types/                    # TypeScript interfaces + Zod schemas
│   │   ├── routes.ts                 # Route definitions for shell
│   │   ├── nav.ts                    # Navigation metadata
│   │   └── index.ts                  # Package entry point
│   ├── .storybook/
│   ├── package.json
│   ├── tsconfig.json
│   └── tsup.config.ts
├── mcp/                              # bold-cache-layer-mcp (MCP Server)
│   ├── src/
│   │   ├── server.py                 # MCP server definition
│   │   ├── tools.py                  # Tools mapping to cache API operations
│   │   └── resources.py              # Resources for inspecting cache state
│   └── pyproject.toml
└── tests/
    ├── cache/
    │   ├── test_service.py
    │   ├── test_repository.py
    │   └── test_router.py
    ├── semantic/
    │   ├── test_service.py
    │   └── test_repository.py
    ├── invalidation/
    │   ├── test_service.py
    │   └── test_event_handler.py
    ├── stats/
    │   └── test_service.py
    ├── integration/
    │   ├── test_opensearch_operations.py
    │   └── test_end_to_end.py
    ├── fixtures/
    │   ├── sample_queries.json
    │   └── sample_responses.json
    └── conftest.py
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
│  Scenario: DynamoDB unavailable                                              │
│  └── Strategy: Return cache miss                                             │
│      └── Both exact match and durable writes fail                           │
│      └── Caller proceeds to Model Gateway normally                           │
│      └── Log critical degradation                                            │
│                                                                              │
│  Scenario: OpenSearch unavailable                                            │
│  └── Strategy: Skip semantic lookup, rely on exact match only               │
│      └── Exact match (DynamoDB) still provides value                        │
│      └── Semantic cache writes queued for retry                              │
│      └── Log degraded mode                                                   │
│                                                                              │
│  Scenario: Model Gateway unavailable (embedding generation)                 │
│  └── Strategy: Skip semantic lookup and write                               │
│      └── Exact match still works                                             │
│      └── Log embedding failure                                               │
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

### Domain Exception Hierarchy

Per `constitution/coding-standards.md`, the Cache Layer uses a domain exception hierarchy:

```python
class AppError(Exception):
    """Base application error."""
    status_code: int = 500
    code: str = "INTERNAL_ERROR"

class NotFoundError(AppError):
    status_code = 404
    code = "CACHE_ENTRY_NOT_FOUND"

class ValidationError(AppError):
    status_code = 400
    code = "INVALID_REQUEST"

class AuthorizationError(AppError):
    status_code = 403
    code = "FORBIDDEN"

class ExternalServiceError(AppError):
    status_code = 502
    # code varies: EMBEDDING_ERROR, OPENSEARCH_ERROR, CALLBACK_ERROR
```

### Circuit Breaker Pattern

External dependencies (OpenSearch, Model Gateway) are wrapped with circuit breakers:

- **Closed** (normal): Requests pass through
- **Open** (after 5 consecutive failures): Requests fail fast, return cache miss immediately
- **Half-open** (after 30 seconds): Allow one test request through

---

## Performance & Scaling

### Latency Targets

| Operation | Target P50 | Target P99 | Notes |
|-----------|-----------|-----------|-------|
| Exact match hit | < 7ms | < 15ms | DynamoDB GetItem, single operation |
| Exact match miss | < 10ms | < 20ms | DynamoDB GetItem (not found) |
| Semantic hit | < 80ms | < 200ms | Embedding + OpenSearch kNN |
| Semantic miss | < 100ms | < 250ms | Embedding + OpenSearch kNN (no result) |
| Full lookup (exact miss + semantic miss) | < 100ms | < 250ms | Both tiers, no hit |
| Cache write | < 50ms | < 150ms | Parallel DynamoDB + OpenSearch |
| Lookup-or-exec (hit) | < 100ms | < 250ms | Same as lookup |
| Lookup-or-exec (miss) | Depends on callback | — | Dominated by LLM latency |

### Optimization Strategies

1. **DynamoDB GetItem first, always**: Exact match is O(1) lookup via GSI — always check first
2. **Parallel writes**: DynamoDB and OpenSearch writes happen concurrently via `asyncio.gather()`
3. **Connection pooling**: Persistent connections to OpenSearch and DynamoDB across Lambda invocations
4. **Provisioned concurrency**: 10 warm Lambda instances to eliminate cold starts on the hot path
5. **OpenSearch pre-filtering**: Tenant and scope filters applied at the engine level, not post-filter
6. **DynamoDB DAX** (future): If exact match latency becomes critical, add DAX for sub-millisecond reads

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
│  DynamoDB                                                                    │
│  └── On-demand capacity — scales with read/write volume                      │
│      └── Cache reads: GetItem on every exact match lookup                   │
│      └── Cache writes: one per cache miss                                    │
│      └── Config reads: cached in Lambda memory                               │
│                                                                              │
│  OpenSearch (Provisioned)                                                    │
│  └── t3.small.search for initial deployment                                  │
│      └── Scale to t3.medium.search or larger as index grows                 │
│      └── Monitor JVM memory and CPU utilization                             │
│      └── Only used for semantic lookups (not exact matches)                 │
│                                                                              │
│  Model Gateway (via boldsci-model-gateway SDK)                              │
│  └── Embedding generation scales via Model Gateway rate limits              │
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

Cache infrastructure cost:
  OpenSearch (t3.small.search): ~$25/month
  Lambda + DynamoDB: ~$50/month
  Total cache cost: ~$75/month

Break-even: ~350 cache hits/day
  (After break-even, every additional hit is pure savings)
```

---

## SDK (`boldsci-cache-layer`)

Per `constitution/platform-blocks.md` § Section 3, the Cache Layer ships a typed Python SDK for block-to-block and application-to-block consumption.

### Package

- **PyPI package:** `boldsci-cache-layer`
- **Python import:** `from boldsci.cache_layer import CacheLayerClient`
- **Published to:** AWS CodeArtifact (private PyPI registry)

### Client Interface

```python
from boldsci.cache_layer import CacheLayerClient, LookupConfig

client = CacheLayerClient()

# Cache lookup
result = client.lookup(
    workspace_id="ws_01JKX...",
    project_id="customer-support",
    query="How do I reset my password?",
    lookup_config=LookupConfig(
        enable_exact_match=True,
        enable_semantic=True,
        similarity_threshold=0.92,
    ),
    application_id=auth.application_id,
    client_id=auth.client_id,
)

if result.status == "hit":
    return result.response

# Cache write
client.write(
    workspace_id="ws_01JKX...",
    project_id="customer-support",
    query="How do I reset my password?",
    response=llm_response,
    application_id=auth.application_id,
    client_id=auth.client_id,
)
```

### What the SDK Provides

- Typed `CacheLayerClient` wrapping all API endpoints
- Pydantic request/response models
- Service discovery via SSM (`/bold/cache-layer/api-url`)
- Auth header injection (`X-Service-Key` + `X-Forwarded-Client-Id`)
- Retry and error handling

### What the SDK Does NOT Do

- No business logic
- No direct DynamoDB, OpenSearch, or any other data store access
- The SDK is a thin HTTP client over the Cache Layer API

---

## Admin UI Package (`@boldscience/admin-cache-layer`)

Per `constitution/platform-blocks.md` § Section 4, the Cache Layer ships an admin UI package consumed by `platform-block-admin`.

### Package

- **npm package:** `@boldscience/admin-cache-layer`
- **Import:** `import { CacheDashboardPage, useCacheStats } from '@boldscience/admin-cache-layer'`
- **Published to:** GitHub Packages (`npm.pkg.github.com`)

### Location

Lives in the `ui/` directory of this repo (see Project Structure above).

### Exported Surfaces

| Export | Description |
|--------|-------------|
| `CacheDashboardPage` | Overview dashboard: hit rate, latency, cost savings, entry counts |
| `CacheEntriesPage` | Browse and search cache entries by scope |
| `CacheConfigPage` | View and edit per-project cache configuration |
| `CacheInvalidationPage` | Manual invalidation and purge operations |
| `useCacheStats` | TanStack Query hook for `GET /v1/cache/stats` |
| `useCacheEntries` | TanStack Query hook for listing cache entries |
| `useCacheConfig` / `useUpdateCacheConfig` | Query/mutation hooks for config |
| `useInvalidateCache` / `usePurgeCache` | Mutation hooks for invalidation |
| Route definitions | Route config for shell mounting at `/cache-layer/*` |
| Navigation metadata | Label: "Cache Layer", icon, ordering |

### Tech Stack

React, TypeScript, Tailwind, `@boldscience/ui` (shadcn/ui components), TanStack Query, Zod. Follows `constitution/frontend-standards.md`.

### Peer Dependencies

```json
{
  "peerDependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "@tanstack/react-query": "^5.0.0",
    "@tanstack/react-router": "^1.0.0",
    "zod": "^3.0.0",
    "tailwindcss": "^4.0.0",
    "@boldscience/ui": "^1.0.0"
  }
}
```

---

## MCP Server (`bold-cache-layer-mcp`)

Per `constitution/platform-blocks.md` § Section 5, the Cache Layer exposes an MCP server for AI agent interaction.

### What It Exposes

**Tools:**

| Tool | Maps To | Description |
|------|---------|-------------|
| `cache_lookup` | `POST /v1/cache/lookup` | Check cache for a query |
| `cache_write` | `POST /v1/cache/write` | Write a response to cache |
| `cache_invalidate` | `POST /v1/cache/invalidate` | Bulk invalidate cache entries |
| `cache_purge` | `POST /v1/cache/purge` | Purge all cache for a scope |
| `cache_stats` | `GET /v1/cache/stats` | Get cache statistics |
| `cache_config_get` | `GET /v1/cache/config` | Get cache configuration |
| `cache_config_update` | `PUT /v1/cache/config` | Update cache configuration |

**Resources:**

| Resource | Description |
|----------|-------------|
| `cache://stats/{workspace_id}/{project_id}` | Cache statistics for a project |
| `cache://config/{workspace_id}/{project_id}` | Cache configuration for a project |
| `cache://health` | Service health status |

### Backend

The MCP server consumes the Cache Layer API via the `boldsci-cache-layer` SDK. No direct database access.

### Auth

Authenticates to the Cache Layer API using a service key. Tenant context is forwarded from the agent's session.

### Registration

Documented in `bold-spec/mcp/`. The Cache Layer repo maintains the MCP server implementation.

---

## Implementation Phases

### Phase 1: Exact Match Caching (MVP)

**Goal:** Fast, hash-based caching for identical queries.

- Project scaffolding (Terraform, uv, domain-driven project structure)
- Query normalization logic
- SHA-256 hash-based cache key construction with `application_id` + `client_id` + `workspace_id` + `project_id`
- DynamoDB table setup (`bold-cache-layer`) with Platform Context PK pattern
- DynamoDB repository: exact match GetItem via GSI-QueryHash
- POST /v1/cache/lookup (exact match only)
- POST /v1/cache/write (DynamoDB only)
- GET /health endpoint (no auth)
- `boldsci-auth` SDK integration for AuthContext resolution
- Shared Lambda Authorizer attachment (SSM: `/bold/auth/authorizer-arn`)
- TTL-based expiration (DynamoDB TTL)
- Basic DELETE /v1/cache/entries/{id}
- Request/response Pydantic models (`ApiModel` base with camelCase aliases)
- Domain exception hierarchy (`AppError` → `NotFoundError`, etc.)
- Custom domain setup (`cache-layer-api.{env}.boldquantum.com`)
- SSM registration (`/bold/cache-layer/api-url`)
- CORS configuration for admin UI domains
- structlog JSON logging with required fields
- Unit tests with moto (DynamoDB)

### Phase 2: Semantic Similarity Caching

**Goal:** Embedding-based lookup for paraphrased queries.

- `boldsci-model-gateway` SDK integration for embedding generation
- Provisioned OpenSearch domain setup (t3.small.search)
- OpenSearch domain endpoint registration in SSM (`/bold/opensearch/domain-endpoint`)
- Semantic cache index (`bold-semantic-cache`) with kNN mapping
- kNN similarity search with tenant-scoped filtering (`application_id` + `client_id` + `workspace_id` + `project_id`)
- Configurable similarity threshold
- Semantic cache write (embedding stored in OpenSearch)
- POST /v1/cache/lookup updated for two-tier pipeline (exact → semantic)
- Graceful degradation (skip semantic when Model Gateway/OpenSearch unavailable)
- Circuit breaker for external dependencies
- ADOT instrumentation (FastAPIInstrumentor, BotocoreInstrumentor)
- Integration tests against OpenSearch

### Phase 3: Cache Invalidation & Configuration

**Goal:** Comprehensive invalidation strategies and per-tenant configuration.

- POST /v1/cache/invalidate (bulk invalidation by criteria)
- POST /v1/cache/purge (full scope purge)
- EventBridge integration (subscribe to Doc Ingest, Model Gateway, Guardrails events)
- Event-driven invalidation Lambda
- Citation-based invalidation (invalidate entries citing updated documents)
- GET/PUT /v1/cache/config (per-project configuration)
- Guardrail policy version tracking on cache entries

### Phase 4: Advanced Features

**Goal:** Convenience endpoints, statistics, and cost attribution.

- POST /v1/cache/lookup-or-exec (lookup + callback on miss)
- GET /v1/cache/stats (hit/miss statistics)
- Stats aggregation Lambda (periodic CloudWatch-scheduled)
- Cost savings estimation (tokens saved × model pricing)
- Context-aware caching (hash system prompt + retrieval context)
- Admin permissions for purge/bulk operations (`cache:admin` scope)
- DynamoDB GSI for citation-based lookups
- OpenAPI spec generation and publishing

### Phase 5: Artifacts (SDK, Admin UI, MCP Server)

**Goal:** Ship all four required artifacts per `constitution/platform-blocks.md`.

- **SDK** (`boldsci-cache-layer`): Typed Python client, SSM-based service discovery, published to CodeArtifact
- **Admin UI Package** (`@boldscience/admin-cache-layer`): React components, TanStack Query hooks, Storybook, published to GitHub Packages
- **MCP Server** (`bold-cache-layer-mcp`): Tools and resources mapping to cache API, documented in `bold-spec/mcp/`

### Phase 6: Optimization & Observability

**Goal:** Performance tuning, monitoring, and production readiness.

- Provisioned concurrency tuning
- CloudWatch metrics and dashboards
- Alerting (low hit rate, high latency, write failures)
- Latency breakdown logging (per-stage timing)
- Cost attribution per application + client
- Load testing and P99 optimization

---

## Appendix A: DynamoDB Schema

### Table: `bold-cache-layer`

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ PK                                    │ SK                                    │
├───────────────────────────────────────┼───────────────────────────────────────┤
│ APP#scicoms#CLIENT#acme-corp          │ CACHE#WS#ws_01JK..#PROJ#cust-sup#01..│
│ APP#scicoms#CLIENT#acme-corp          │ CACHE#WS#ws_01JK..#PROJ#hr-bot#01..  │
│ APP#scicoms#CLIENT#acme-corp          │ CONFIG#WS#ws_01JK..#PROJ#cust-sup    │
│ APP#scicoms#CLIENT#acme-corp          │ CONFIG#WS#ws_01JK..#PROJ#hr-bot      │
│ APP#scicoms#CLIENT#acme-corp          │ INVAL#2026-02-10T15:00:00Z#01JKX...  │
│ APP#boldpubs#CLIENT#beta-inc          │ CACHE#WS#ws_02AB..#PROJ#support#01.. │
│ APP#boldpubs#CLIENT#beta-inc          │ CONFIG#WS#ws_02AB..#PROJ#support     │
└──────────────────────────────────────────────────────────────────────────────┘
```

### GSI Access Patterns

| Access Pattern | GSI | Query |
|---------------|-----|-------|
| Exact match lookup (query hash) | `GSI-QueryHash` | PK=`APP#X#CLIENT#Y#HASH#Z` |
| List entries by workspace + project | `GSI-ProjectEntries` | PK=`APP#X#CLIENT#Y#WS#W#PROJ#P`, SK begins_with `CREATED#` |
| Find entries citing a document | `GSI-Citation` | PK=`APP#X#CLIENT#Y#DOC#D` |
| Cache statistics | `GSI-Stats` | PK=`APP#X#CLIENT#Y#WS#W#PROJ#P`, SK begins_with `STATS#` |

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

def compute_cache_key(
    application_id: str,
    client_id: str,
    workspace_id: str,
    project_id: str,
    query: str,
) -> str:
    normalized = normalize_query(query)
    query_hash = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    return f"{application_id}:{client_id}:{workspace_id}:{project_id}:{query_hash}"
```

### Cosine Similarity

```python
from numpy import dot
from numpy.linalg import norm

def cosine_similarity(a: list[float], b: list[float]) -> float:
    return dot(a, b) / (norm(a) * norm(b))

# Threshold: 0.92 default (configurable per client/project)
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
| `cache.cost_saved_usd` | Estimated cost savings per hour | Trend monitoring |
| `cache.tokens_saved` | Tokens saved per hour (input + output) | Trend monitoring |
| `cache.embedding.latency` | Model Gateway embedding latency | p99 > 200ms |
| `cache.opensearch.latency` | OpenSearch kNN query latency | p99 > 150ms |
| `cache.dynamodb.getitem_latency` | DynamoDB GetItem latency for exact match | p99 > 20ms |

### Structured Logging

All log entries follow the platform standard JSON format using `structlog`:

```json
{
  "timestamp": "2026-02-10T12:00:00.123Z",
  "level": "INFO",
  "message": "cache_lookup_completed",
  "service": "cache-layer",
  "function_name": "cache-api",
  "request_id": "req-uuid-456",
  "trace_id": "1-abc123-def456",
  "application_id": "scicoms",
  "client_id": "acme-corp",
  "workspace_id": "ws_01JKX...",
  "project_id": "customer-support",
  "status": "hit",
  "source": "semantic",
  "similarity_score": 0.946,
  "exact_match_ms": 7,
  "semantic_ms": 84,
  "total_latency_ms": 91,
  "cache_entry_id": "ce-01JKX001...",
  "tokens_saved_input": 245,
  "tokens_saved_output": 180,
  "cost_saved_estimate_usd": 0.0042
}
```

Required fields per `constitution/coding-standards.md`: `timestamp`, `level`, `message`, `request_id`, `trace_id`, `service`, `function_name`.

### CloudWatch Dashboard

The Cache Layer publishes a pre-configured CloudWatch dashboard with:

1. **Cache Performance**: Hit rate over time (exact + semantic breakdown), miss rate
2. **Latency**: Lookup latency percentiles, write latency, per-stage breakdown (DynamoDB GetItem, OpenSearch kNN, embedding generation)
3. **Cost Savings**: Estimated USD saved, tokens saved, trending over time
4. **Infrastructure Health**: DynamoDB consumed capacity, OpenSearch CPU/memory utilization
5. **Invalidation Activity**: Invalidation events, entries purged, event sources
6. **Per-Client View**: Filterable by `application_id` + `client_id` for tenant-specific monitoring

### Alerting

| Alert | Condition | Action |
|-------|-----------|--------|
| Low hit rate | Hit rate < 20% for 1 hour | Investigate — threshold too high or cache not populated |
| High miss latency | p99 miss latency > 500ms | Investigate — Model Gateway or OpenSearch slow |
| Cache write failures | Any write failure count > 0 | Investigate — DynamoDB issue |
| Invalidation spike | > 1000 invalidations in 5 min | Investigate — mass update event or misconfigured trigger |
| Embedding generation failures | > 0 failures | Investigate — Model Gateway quota or availability |
| DynamoDB throttling | Any throttled requests | Review on-demand capacity or access patterns |
| OpenSearch high CPU | CPU > 80% for 15 min | Consider scaling to larger instance type |
