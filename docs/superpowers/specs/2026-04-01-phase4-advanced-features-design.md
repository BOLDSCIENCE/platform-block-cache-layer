# Phase 4: Advanced Features — Design Spec

**Date:** 2026-04-01
**Status:** Approved
**Scope:** Stats pipeline, lookup-or-exec, context-aware caching, cost savings estimation, OpenAPI polish

---

## Overview

Phase 4 adds advanced features to the Cache Layer: real-time statistics with cost savings estimation, a cache-aside lookup-or-exec convenience endpoint, and context-aware caching. These build on the Phase 1-3 foundation (exact match, semantic similarity, invalidation/purge/config).

---

## 1. Stats Pipeline

### Architecture

Stats use DynamoDB atomic counters accumulated on every lookup, with a CloudWatch-scheduled aggregator Lambda that rolls counters into pre-aggregated period stats every 15 minutes.

```
Lookup request
    │
    ├─ (hot path) Atomic DynamoDB counter increment
    │   PK: APP#{app}#CLIENT#{client}
    │   SK: STATS_LIVE#WS#{ws}#PROJ#{proj}#BUCKET#{15min_bucket}
    │   Counters: exact_hits, semantic_hits, misses,
    │             tokens_saved_input, tokens_saved_output
    │
    └─ (async, every 15 min) Aggregator Lambda
        │   Reads live buckets, rolls up into period stats
        │   Writes pre-aggregated items under GSI-Stats
        │   PK: APP#{app}#CLIENT#{client}
        │   SK: STATS#{period}#{timestamp}
        │   GSI-Stats PK: APP#{app}#CLIENT#{client}#WS#{ws}#PROJ#{proj}
        │   GSI-Stats SK: STATS#{period}#{timestamp}
        │
        └─ GET /v1/cache/stats reads pre-aggregated items
```

### DynamoDB Items

**Live stats bucket (written on every lookup):**

| Field | Value |
|-------|-------|
| PK | `APP#{app}#CLIENT#{client}` |
| SK | `STATS_LIVE#WS#{ws}#PROJ#{proj}#BUCKET#{15min_bucket}` |
| exact_hits | atomic counter |
| semantic_hits | atomic counter |
| misses | atomic counter |
| tokens_saved_input | atomic counter |
| tokens_saved_output | atomic counter |
| ttl | 48 hours from bucket start |

The 15-minute bucket key is derived from the current timestamp truncated to the nearest 15-minute boundary (e.g., `2026-04-01T14:15`).

**Pre-aggregated period stats (written by aggregator):**

| Field | Value |
|-------|-------|
| PK | `APP#{app}#CLIENT#{client}` |
| SK | `STATS#{period}#{timestamp}` |
| GSI4PK | `APP#{app}#CLIENT#{client}#WS#{ws}#PROJ#{proj}` |
| GSI4SK | `STATS#{period}#{timestamp}` |
| period | `1h`, `24h`, `7d`, `30d` |
| exact_hits | aggregated count |
| semantic_hits | aggregated count |
| misses | aggregated count |
| total_lookups | sum of hits + misses |
| hit_rate | float |
| exact_hit_rate | float |
| semantic_hit_rate | float |
| tokens_saved_input | aggregated count |
| tokens_saved_output | aggregated count |
| estimated_cost_saved_usd | float |
| total_entries | count of active entries at snapshot time |
| ttl | retention: 48h for 1h, 30d for 24h, 90d for 7d, 365d for 30d |

### GSI-Stats (GSI4)

A new GSI for querying pre-aggregated stats by scope + period:

- Hash key: `GSI4PK` = `APP#{app}#CLIENT#{client}#WS#{ws}#PROJ#{proj}`
- Range key: `GSI4SK` = `STATS#{period}#{timestamp}`
- Projection: ALL

### Aggregator Lambda

- Triggered by CloudWatch EventBridge rule every 15 minutes
- Discovers active scopes by scanning the table for `SK begins_with("STATS_LIVE#")` items from the current and recent windows. Each live bucket's SK contains the workspace and project IDs, so the aggregator naturally discovers all active scopes.
- Rolls up into period stats: 1h (last 4 buckets), 24h (last 96), 7d (last 672), 30d (last 2880)
- Computes cost savings using model pricing table
- Counts active entries per scope (via GSI2 query)
- Writes pre-aggregated items with appropriate TTLs
- Separate Lambda function (like the event handler), not FastAPI/Mangum
- Receives `application_id` and `client_id` as environment variables (same as API and event handler Lambdas) — scoped to a single tenant deployment

### GET /v1/cache/stats Endpoint

**Query parameters:** `workspace_id`, `project_id`, `period` (default `"24h"`)

**Response:**
```json
{
  "workspace_id": "ws_01",
  "project_id": "proj_01",
  "period": "24h",
  "stats": {
    "total_lookups": 5420,
    "exact_hits": 3150,
    "semantic_hits": 890,
    "misses": 1380,
    "hit_rate": 0.745,
    "exact_hit_rate": 0.581,
    "semantic_hit_rate": 0.164,
    "total_entries": 342,
    "estimated_cost_saved_usd": 22.76,
    "estimated_tokens_saved": {
      "input": 1234500,
      "output": 876200
    }
  }
}
```

Requires `cache:read` scope.

---

## 2. Lookup-or-Exec

### Overview

`POST /v1/cache/lookup-or-exec` implements the cache-aside pattern: lookup first, on miss call Model Gateway SDK to execute the LLM call, cache the result, return it.

### Request Schema

```python
class OnMissConfig(ApiModel):
    model: str                    # e.g. "anthropic.claude-sonnet-4-5-20250929"
    messages: list[dict]          # messages array for the LLM call
    cache_response: bool = True
    ttl_seconds: int = 86400

class LookupOrExecRequest(ApiModel):
    workspace_id: str
    project_id: str
    query: str
    request_id: str | None = None
    context_hash: str | None = None
    lookup_config: LookupConfig | None = None
    on_miss: OnMissConfig
```

### Flow

1. Run normal lookup pipeline (exact match -> semantic match)
2. On hit -> return cached response (same as lookup endpoint)
3. On miss -> call `GatewayClient.invoke(model=on_miss.model, messages=on_miss.messages)`
4. Cache the response via existing `write()` path (if `cache_response=True`)
5. Return response with `match_type: "miss_executed"`

### Design Decisions

- Uses `GatewayClient.invoke()` from `boldsci-model-gateway` SDK for block-to-block communication. No raw HTTP callbacks.
- `on_miss.messages` contains the full messages array. The caller assembles this (including system prompt, RAG context, etc.) before calling lookup-or-exec.
- No `callback_url` or `callback_headers` — SDK handles service discovery, auth, retries.
- Requires `cache:write` scope (creates entries on miss).
- Gateway SDK dependency is optional. If not configured, endpoint returns 503 explaining lookup-or-exec requires Model Gateway integration.
- Response reuses `CacheLookupResponse` with `match_type` extended to include `"miss_executed"`.

---

## 3. Context-Aware Caching

### Overview

Solves the problem where the same query produces different answers depending on context (different system prompts, different RAG results, different user roles).

### How It Works

The caller provides an optional `context_hash` string on lookup and write requests. This hash is incorporated into the GSI1 partition key:

```
GSI1PK = APP#{app}#CLIENT#{client}#HASH#{query_hash}#CTX#{context_hash}
```

When `context_hash` is `None`, the key format is unchanged from today (no `#CTX#` suffix). Full backward compatibility.

### Why Caller-Computed

- The Cache Layer doesn't know what "context" means for each application
- Keeps the Cache Layer simple — no need to parse arbitrary context structures
- The caller already has the context at hand
- Avoids shipping large payloads (system prompts, retrieval chunks) just for hashing

### Schema Changes

- `CacheLookupRequest` gains `context_hash: str | None = None`
- `CacheWriteRequest` gains `context_hash: str | None = None`
- `LookupOrExecRequest` includes `context_hash` (shown above)
- `CacheEntryModel` gains `context_hash: str | None = None`

### Storage Changes

- `context_hash` stored on the entry model
- GSI1PK includes `#CTX#{context_hash}` suffix when provided
- Same query + different context = separate GSI1 partitions = separate cache entries

---

## 4. Cost Savings Estimation

### How It Works

Each cache entry stores `tokens_used: { input, output }` and `model`. On every cache hit, the live stats bucket increments `tokens_saved_input` and `tokens_saved_output` atomic counters. The aggregator Lambda computes the USD estimate during rollup.

### Pricing Table

```python
MODEL_PRICING = {
    "anthropic.claude-sonnet-4-5-20250929": {
        "input": 3.00 / 1_000_000,
        "output": 15.00 / 1_000_000,
    },
    "anthropic.claude-haiku-4-5-20251001": {
        "input": 0.80 / 1_000_000,
        "output": 4.00 / 1_000_000,
    },
}
```

- Stored as a dict in the stats service module
- Unknown models fall back to Sonnet pricing (conservative estimate)
- Easy to update when model pricing changes

### Where Cost Calc Lives

In the aggregator Lambda, not the hot lookup path. Lookups increment token counters; dollar conversion happens during aggregation. This keeps lookups fast.

### Stats Response Fields

- `estimated_cost_saved_usd: float` — USD value for the period
- `estimated_tokens_saved: { input: int, output: int }` — raw token counts

---

## 5. OpenAPI Polish

FastAPI auto-generates the OpenAPI schema at `/openapi.json`. Phase 4 ensures completeness:

- All new endpoints get proper docstrings and `response_model` annotations
- Key schema fields use `Field(description="...")` for documentation
- FastAPI app metadata (title, version, description) set in the app factory
- No manual OpenAPI YAML — rely on FastAPI auto-generation
- This is a polish pass applied alongside the other features, not a separate implementation step

---

## New Infrastructure

### DynamoDB

- **GSI4 (Stats):** Hash key `GSI4PK`, range key `GSI4SK`, ALL projection
- New attribute definitions for GSI4PK and GSI4SK
- Live stats bucket items with 48h TTL
- Period stats items with variable TTLs (48h to 365d)

### Lambda Functions

- **Stats Aggregator Lambda:** CloudWatch-scheduled every 15 minutes, reads live buckets, writes period stats. Raw handler (no FastAPI/Mangum), similar pattern to existing event handler Lambda.

### CloudWatch EventBridge

- New scheduled rule: `rate(15 minutes)` targeting the stats aggregator Lambda

### Model Gateway SDK

- `GatewayClient` dependency added for lookup-or-exec
- Optional — only needed if lookup-or-exec is used
- Configured via `MODEL_GATEWAY_API_URL` environment variable (or SSM discovery)

---

## Files Changed / Created (Estimated)

| File | Action | Purpose |
|------|--------|---------|
| `api/src/cache/schemas.py` | MODIFY | Add LookupOrExecRequest, OnMissConfig, StatsResponse, context_hash fields |
| `api/src/cache/models.py` | MODIFY | Add StatsLiveBucketModel, StatsPeriodModel, context_hash to CacheEntryModel |
| `api/src/cache/service.py` | MODIFY | Add lookup_or_exec(), increment_stats(), context_hash in lookup/write |
| `api/src/cache/repository.py` | MODIFY | Add stats bucket increment, stats period read/write, context_hash in GSI1PK |
| `api/src/cache/normalizer.py` | MODIFY | Update build_gsi_query_hash_pk for context_hash, add stats key builders |
| `api/src/cache/router.py` | MODIFY | Add POST /lookup-or-exec, GET /stats endpoints |
| `api/src/cache/dependencies.py` | MODIFY | Add GatewayClient dependency (optional) |
| `api/src/stats_aggregator.py` | CREATE | Aggregator Lambda handler |
| `api/src/cache/pricing.py` | CREATE | Model pricing table + cost calculation |
| `terraform/dynamodb.tf` | MODIFY | Add GSI4 attributes and index |
| `terraform/lambda.tf` | MODIFY | Add stats aggregator Lambda |
| `terraform/eventbridge.tf` | MODIFY | Add scheduled rule for aggregator |
| `terraform/variables.tf` | MODIFY | Add aggregator Lambda variables |
| `api/tests/cache/test_service.py` | MODIFY | Add lookup-or-exec, stats, context-hash tests |
| `api/tests/cache/test_repository.py` | MODIFY | Add stats bucket tests |
| `api/tests/cache/test_router.py` | MODIFY | Add endpoint integration tests |
| `api/tests/test_stats_aggregator.py` | CREATE | Aggregator Lambda tests |
| `api/src/main.py` | MODIFY | App metadata for OpenAPI |
