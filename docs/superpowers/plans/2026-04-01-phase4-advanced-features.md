# Phase 4: Advanced Features — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add stats pipeline with cost savings, lookup-or-exec cache-aside endpoint, and context-aware caching to the Cache Layer.

**Architecture:** DynamoDB atomic counters accumulate stats per 15-minute bucket on every lookup. A scheduled aggregator Lambda rolls up counters into pre-aggregated period stats (1h/24h/7d/30d). Lookup-or-exec calls Model Gateway SDK on cache miss. Context-aware caching uses a caller-provided `context_hash` appended to the GSI1 partition key.

**Tech Stack:** Python 3.12, FastAPI, DynamoDB, `boldsci-model-gateway` SDK (`GatewayClient.invoke()`), CloudWatch EventBridge scheduled rules, Terraform

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `api/src/cache/normalizer.py` | MODIFY | Add `build_stats_live_sk`, `build_stats_period_sk`, `build_gsi_stats_pk`, update `build_gsi_query_hash_pk` for context_hash |
| `api/src/cache/models.py` | MODIFY | Add `StatsLiveBucketModel`, `StatsPeriodModel`, add `context_hash` to `CacheEntryModel` |
| `api/src/cache/schemas.py` | MODIFY | Add `OnMissConfig`, `LookupOrExecRequest`, `LookupOrExecResponse`, `CacheStatsResponse`, `TokensSaved`; add `context_hash` to `CacheLookupRequest` and `CacheWriteRequest` |
| `api/src/cache/pricing.py` | CREATE | Model pricing table + `estimate_cost_saved()` function |
| `api/src/cache/repository.py` | MODIFY | Add `increment_stats_bucket`, `query_stats_live_buckets`, `put_stats_period`, `query_stats_period`; update `put()` for context_hash in GSI1PK |
| `api/src/cache/service.py` | MODIFY | Add `lookup_or_exec()`, `_increment_stats()`, `get_stats()`; update `lookup()` and `write()` for context_hash |
| `api/src/cache/dependencies.py` | MODIFY | Add `get_gateway_client()` public getter, wire into `CacheService` |
| `api/src/cache/router.py` | MODIFY | Add `POST /lookup-or-exec`, `GET /stats` endpoints |
| `api/src/stats_aggregator.py` | CREATE | Scheduled Lambda handler for stats rollup |
| `api/src/config.py` | MODIFY | Add `application_id`, `client_id` settings for aggregator Lambda |
| `api/src/main.py` | MODIFY | Update app metadata version |
| `terraform/dynamodb.tf` | MODIFY | Add GSI4 (Stats) attributes and index |
| `terraform/lambda.tf` | MODIFY | Add stats aggregator Lambda + IAM role |
| `terraform/eventbridge.tf` | MODIFY | Add scheduled rule for aggregator |
| `terraform/variables.tf` | MODIFY | Add aggregator Lambda variables |
| `api/tests/conftest.py` | MODIFY | Add GSI4 to mock table |
| `api/tests/cache/test_service.py` | MODIFY | Add context-hash, lookup-or-exec, stats increment tests |
| `api/tests/cache/test_repository.py` | MODIFY | Add stats bucket CRUD tests |
| `api/tests/cache/test_router.py` | MODIFY | Add endpoint integration tests |
| `api/tests/test_stats_aggregator.py` | CREATE | Aggregator Lambda unit tests |

---

## Task 1: Context-Aware Caching — Normalizer + Model Changes

**Files:**
- Modify: `api/src/cache/normalizer.py`
- Modify: `api/src/cache/models.py`
- Test: `api/tests/cache/test_normalizer.py` (new)

- [ ] **Step 1: Write the failing test for context-aware GSI1PK**

Create `api/tests/cache/test_normalizer.py`:

```python
"""Tests for normalizer key builders with context_hash support."""

from src.cache.normalizer import build_gsi_query_hash_pk


class TestBuildGsiQueryHashPk:
    def test_without_context_hash(self):
        result = build_gsi_query_hash_pk("app1", "client1", "abc123")
        assert result == "APP#app1#CLIENT#client1#HASH#abc123"

    def test_with_context_hash(self):
        result = build_gsi_query_hash_pk("app1", "client1", "abc123", context_hash="ctx_99")
        assert result == "APP#app1#CLIENT#client1#HASH#abc123#CTX#ctx_99"

    def test_with_none_context_hash(self):
        result = build_gsi_query_hash_pk("app1", "client1", "abc123", context_hash=None)
        assert result == "APP#app1#CLIENT#client1#HASH#abc123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest tests/cache/test_normalizer.py -v`

Expected: FAIL — `build_gsi_query_hash_pk` doesn't accept `context_hash` parameter yet.

- [ ] **Step 3: Update `build_gsi_query_hash_pk` to accept optional context_hash**

In `api/src/cache/normalizer.py`, replace the existing function:

```python
def build_gsi_query_hash_pk(
    application_id: str, client_id: str, query_hash: str, context_hash: str | None = None
) -> str:
    """Build GSI1 (QueryHash) partition key for exact match lookup.

    Format: APP#{application_id}#CLIENT#{client_id}#HASH#{query_hash}
    With context: APP#{application_id}#CLIENT#{client_id}#HASH#{query_hash}#CTX#{context_hash}
    """
    base = f"APP#{application_id}#CLIENT#{client_id}#HASH#{query_hash}"
    if context_hash:
        return f"{base}#CTX#{context_hash}"
    return base
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest tests/cache/test_normalizer.py -v`

Expected: PASS

- [ ] **Step 5: Add `context_hash` field to `CacheEntryModel`**

In `api/src/cache/models.py`, add after `embedding_model`:

```python
    context_hash: str | None = None
```

- [ ] **Step 6: Run full test suite to confirm no regressions**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest -v`

Expected: All 125 existing tests pass + 3 new tests pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer
git add api/src/cache/normalizer.py api/src/cache/models.py api/tests/cache/test_normalizer.py
git commit -m "feat: add context_hash support to normalizer and entry model"
```

---

## Task 2: Context-Aware Caching — Schema + Service + Repository Changes

**Files:**
- Modify: `api/src/cache/schemas.py`
- Modify: `api/src/cache/service.py`
- Modify: `api/src/cache/repository.py`
- Test: `api/tests/cache/test_service.py`
- Test: `api/tests/cache/test_router.py`

- [ ] **Step 1: Add `context_hash` to request schemas**

In `api/src/cache/schemas.py`, add `context_hash` field to `CacheLookupRequest` (after `request_id`):

```python
    context_hash: str | None = None
```

And to `CacheWriteRequest` (after `request_id`):

```python
    context_hash: str | None = None
```

- [ ] **Step 2: Write the failing test for context-aware lookup**

Add to `api/tests/cache/test_service.py`:

```python
class TestContextAwareLookup:
    """Tests for context-aware caching via context_hash."""

    def test_same_query_different_context_are_separate(self, cache_service, cache_repo):
        """Two writes with same query but different context_hash produce separate entries."""
        from src.cache.schemas import CachedResponse, CacheWriteRequest, CacheLookupRequest

        base_write = {
            "workspace_id": "ws_01",
            "project_id": "proj_01",
            "query": "How do I reset my password?",
            "response": CachedResponse(content="Answer A", model="m", tokens_used={"input": 10, "output": 20}),
        }

        # Write with context_hash "ctx_A"
        req_a = CacheWriteRequest(**base_write, context_hash="ctx_A")
        cache_service.write(req_a)

        # Write with context_hash "ctx_B"
        req_b = CacheWriteRequest(**{**base_write, "response": CachedResponse(content="Answer B", model="m", tokens_used={"input": 10, "output": 20})}, context_hash="ctx_B")
        cache_service.write(req_b)

        # Lookup with ctx_A should get Answer A
        lookup_a = CacheLookupRequest(
            workspace_id="ws_01", project_id="proj_01",
            query="How do I reset my password?", context_hash="ctx_A",
        )
        result_a = cache_service.lookup(lookup_a)
        assert result_a.status == "hit"
        assert result_a.response.content == "Answer A"

        # Lookup with ctx_B should get Answer B
        lookup_b = CacheLookupRequest(
            workspace_id="ws_01", project_id="proj_01",
            query="How do I reset my password?", context_hash="ctx_B",
        )
        result_b = cache_service.lookup(lookup_b)
        assert result_b.status == "hit"
        assert result_b.response.content == "Answer B"

    def test_no_context_hash_backward_compatible(self, cache_service):
        """Lookup without context_hash works the same as before."""
        from src.cache.schemas import CachedResponse, CacheWriteRequest, CacheLookupRequest

        req = CacheWriteRequest(
            workspace_id="ws_01", project_id="proj_01",
            query="test query?",
            response=CachedResponse(content="answer", model="m", tokens_used={}),
        )
        cache_service.write(req)

        lookup = CacheLookupRequest(
            workspace_id="ws_01", project_id="proj_01", query="test query?",
        )
        result = cache_service.lookup(lookup)
        assert result.status == "hit"
        assert result.response.content == "answer"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest tests/cache/test_service.py::TestContextAwareLookup -v`

Expected: FAIL — service doesn't pass `context_hash` through yet.

- [ ] **Step 4: Update repository `put()` to include context_hash in GSI1PK**

In `api/src/cache/repository.py`, update the `put()` method. Change the `gsi1pk` line from:

```python
        gsi1pk = build_gsi_query_hash_pk(self.application_id, self.client_id, entry.query_hash)
```

to:

```python
        gsi1pk = build_gsi_query_hash_pk(
            self.application_id, self.client_id, entry.query_hash, entry.context_hash
        )
```

Also add `context_hash` to the DynamoDB item dict (after `"ttl": entry.ttl`):

```python
        if entry.context_hash:
            item["context_hash"] = entry.context_hash
```

- [ ] **Step 5: Update repository `get_by_hash()` to accept context_hash**

In `api/src/cache/repository.py`, update `get_by_hash()`:

```python
    def get_by_hash(
        self,
        workspace_id: str,
        project_id: str,
        query_hash: str,
        context_hash: str | None = None,
    ) -> CacheEntryModel | None:
```

And update the GSI query inside:

```python
        gsi_pk = build_gsi_query_hash_pk(
            self.application_id, self.client_id, query_hash, context_hash
        )
```

Update `_item_to_model` to read `context_hash`:

Add after the `ttl` line:

```python
            context_hash=item.get("context_hash"),
```

- [ ] **Step 6: Update service `lookup()` to pass context_hash**

In `api/src/cache/service.py`, in the `lookup()` method, update the `get_by_hash` call:

```python
            entry = self.repository.get_by_hash(
                request.workspace_id, request.project_id, query_hash, request.context_hash
            )
```

- [ ] **Step 7: Update service `write()` to store context_hash**

In `api/src/cache/service.py`, in the `write()` method, add `context_hash` to the `CacheEntryModel` constructor (after `ttl=ttl_epoch,`):

```python
            context_hash=request.context_hash,
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest tests/cache/test_service.py::TestContextAwareLookup -v`

Expected: PASS

- [ ] **Step 9: Run full test suite**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest -v`

Expected: All tests pass.

- [ ] **Step 10: Lint + format**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run ruff check src tests && uv run ruff format --check src tests`

- [ ] **Step 11: Commit**

```bash
cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer
git add api/src/cache/schemas.py api/src/cache/service.py api/src/cache/repository.py api/tests/cache/test_service.py
git commit -m "feat: wire context_hash through lookup and write pipelines"
```

---

## Task 3: Stats — Normalizer Key Builders + Models

**Files:**
- Modify: `api/src/cache/normalizer.py`
- Modify: `api/src/cache/models.py`
- Test: `api/tests/cache/test_normalizer.py`

- [ ] **Step 1: Write failing tests for stats key builders**

Add to `api/tests/cache/test_normalizer.py`:

```python
from src.cache.normalizer import (
    build_gsi_query_hash_pk,
    build_stats_live_sk,
    build_stats_period_sk,
    build_gsi_stats_pk,
)


class TestStatsKeyBuilders:
    def test_build_stats_live_sk(self):
        result = build_stats_live_sk("ws_01", "proj_01", "2026-04-01T14:15")
        assert result == "STATS_LIVE#WS#ws_01#PROJ#proj_01#BUCKET#2026-04-01T14:15"

    def test_build_stats_period_sk(self):
        result = build_stats_period_sk("24h", "2026-04-01T14:00")
        assert result == "STATS#24h#2026-04-01T14:00"

    def test_build_gsi_stats_pk(self):
        result = build_gsi_stats_pk("app1", "client1", "ws_01", "proj_01")
        assert result == "APP#app1#CLIENT#client1#WS#ws_01#PROJ#proj_01"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest tests/cache/test_normalizer.py::TestStatsKeyBuilders -v`

Expected: FAIL — functions not defined yet.

- [ ] **Step 3: Implement stats key builders**

Add to `api/src/cache/normalizer.py`:

```python
def build_stats_live_sk(workspace_id: str, project_id: str, bucket: str) -> str:
    """Build SK for a live stats bucket.

    Format: STATS_LIVE#WS#{workspace_id}#PROJ#{project_id}#BUCKET#{bucket}
    """
    return f"STATS_LIVE#WS#{workspace_id}#PROJ#{project_id}#BUCKET#{bucket}"


def build_stats_period_sk(period: str, timestamp: str) -> str:
    """Build SK for a pre-aggregated stats period item.

    Format: STATS#{period}#{timestamp}
    """
    return f"STATS#{period}#{timestamp}"


def build_gsi_stats_pk(
    application_id: str, client_id: str, workspace_id: str, project_id: str
) -> str:
    """Build GSI4 (Stats) partition key.

    Format: APP#{application_id}#CLIENT#{client_id}#WS#{workspace_id}#PROJ#{project_id}
    """
    return f"APP#{application_id}#CLIENT#{client_id}#WS#{workspace_id}#PROJ#{project_id}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest tests/cache/test_normalizer.py -v`

Expected: All normalizer tests pass.

- [ ] **Step 5: Add stats dataclass models**

Add to `api/src/cache/models.py`:

```python
@dataclass
class StatsLiveBucketModel:
    """Live stats bucket — atomic counters incremented on each lookup."""

    workspace_id: str
    project_id: str
    bucket: str  # 15-min bucket key, e.g. "2026-04-01T14:15"
    exact_hits: int = 0
    semantic_hits: int = 0
    misses: int = 0
    tokens_saved_input: int = 0
    tokens_saved_output: int = 0


@dataclass
class StatsPeriodModel:
    """Pre-aggregated stats for a time period."""

    workspace_id: str
    project_id: str
    period: str  # "1h", "24h", "7d", "30d"
    timestamp: str  # ISO format of the period end
    exact_hits: int = 0
    semantic_hits: int = 0
    misses: int = 0
    total_lookups: int = 0
    hit_rate: float = 0.0
    exact_hit_rate: float = 0.0
    semantic_hit_rate: float = 0.0
    tokens_saved_input: int = 0
    tokens_saved_output: int = 0
    estimated_cost_saved_usd: float = 0.0
    total_entries: int = 0
    ttl: int = 0
```

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest -v`

Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer
git add api/src/cache/normalizer.py api/src/cache/models.py api/tests/cache/test_normalizer.py
git commit -m "feat: add stats key builders and data models"
```

---

## Task 4: Stats — Repository Operations

**Files:**
- Modify: `api/src/cache/repository.py`
- Test: `api/tests/cache/test_repository.py`

- [ ] **Step 1: Write failing tests for stats bucket increment and read**

Add to `api/tests/cache/test_repository.py`:

```python
class TestStatsBucket:
    """Tests for stats bucket DynamoDB operations."""

    def test_increment_stats_creates_bucket(self, repo):
        """First increment creates the bucket item."""
        repo.increment_stats_bucket("ws_01", "proj_01", "2026-04-01T14:15", "exact_hits", 10, 5)

        from src.cache.normalizer import build_pk, build_stats_live_sk
        pk = build_pk("test-app", "test-client")
        sk = build_stats_live_sk("ws_01", "proj_01", "2026-04-01T14:15")

        response = repo.table.get_item(Key={"PK": pk, "SK": sk})
        item = response["Item"]
        assert item["exact_hits"] == 1
        assert item["tokens_saved_input"] == 10
        assert item["tokens_saved_output"] == 5

    def test_increment_stats_accumulates(self, repo):
        """Multiple increments accumulate atomically."""
        repo.increment_stats_bucket("ws_01", "proj_01", "2026-04-01T14:15", "exact_hits", 10, 5)
        repo.increment_stats_bucket("ws_01", "proj_01", "2026-04-01T14:15", "misses", 0, 0)
        repo.increment_stats_bucket("ws_01", "proj_01", "2026-04-01T14:15", "exact_hits", 20, 10)

        from src.cache.normalizer import build_pk, build_stats_live_sk
        pk = build_pk("test-app", "test-client")
        sk = build_stats_live_sk("ws_01", "proj_01", "2026-04-01T14:15")

        response = repo.table.get_item(Key={"PK": pk, "SK": sk})
        item = response["Item"]
        assert item["exact_hits"] == 2
        assert item["misses"] == 1
        assert item["tokens_saved_input"] == 30
        assert item["tokens_saved_output"] == 15

    def test_query_stats_live_buckets(self, repo):
        """Query returns all live buckets for a scope."""
        repo.increment_stats_bucket("ws_01", "proj_01", "2026-04-01T14:00", "exact_hits", 0, 0)
        repo.increment_stats_bucket("ws_01", "proj_01", "2026-04-01T14:15", "misses", 0, 0)

        buckets = repo.query_stats_live_buckets("ws_01", "proj_01")
        assert len(buckets) == 2

    def test_put_and_query_stats_period(self, repo):
        """Write and read a pre-aggregated stats period."""
        from src.cache.models import StatsPeriodModel
        period = StatsPeriodModel(
            workspace_id="ws_01", project_id="proj_01",
            period="24h", timestamp="2026-04-01T14:00",
            exact_hits=100, semantic_hits=30, misses=50,
            total_lookups=180, hit_rate=0.722,
            exact_hit_rate=0.556, semantic_hit_rate=0.167,
            tokens_saved_input=50000, tokens_saved_output=30000,
            estimated_cost_saved_usd=1.23, total_entries=42,
            ttl=9999999999,
        )
        repo.put_stats_period(period)

        result = repo.query_stats_period("ws_01", "proj_01", "24h")
        assert result is not None
        assert result.exact_hits == 100
        assert result.hit_rate == 0.722
        assert result.estimated_cost_saved_usd == 1.23
```

Note: the `repo` fixture in the existing test_repository.py creates a `CacheRepository(table, "test-app", "test-client")`. Verify this fixture exists, or add it.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest tests/cache/test_repository.py::TestStatsBucket -v`

Expected: FAIL — methods not defined.

- [ ] **Step 3: Implement `increment_stats_bucket`**

Add to `api/src/cache/repository.py` (import `build_stats_live_sk` from normalizer):

```python
    def increment_stats_bucket(
        self,
        workspace_id: str,
        project_id: str,
        bucket: str,
        hit_type: str,
        tokens_saved_input: int,
        tokens_saved_output: int,
    ) -> None:
        """Atomically increment a live stats bucket counter."""
        from src.cache.normalizer import build_stats_live_sk

        pk = build_pk(self.application_id, self.client_id)
        sk = build_stats_live_sk(workspace_id, project_id, bucket)

        now_plus_48h = int(time.time()) + 48 * 3600

        self.table.update_item(
            Key={"PK": pk, "SK": sk},
            UpdateExpression=(
                "SET workspace_id = if_not_exists(workspace_id, :ws), "
                "project_id = if_not_exists(project_id, :proj), "
                "bucket = if_not_exists(bucket, :bkt), "
                "#ttl = if_not_exists(#ttl, :ttl_val) "
                "ADD #hit_type :one, "
                "tokens_saved_input :tsi, "
                "tokens_saved_output :tso"
            ),
            ExpressionAttributeNames={
                "#hit_type": hit_type,
                "#ttl": "ttl",
            },
            ExpressionAttributeValues={
                ":ws": workspace_id,
                ":proj": project_id,
                ":bkt": bucket,
                ":one": 1,
                ":tsi": tokens_saved_input,
                ":tso": tokens_saved_output,
                ":ttl_val": now_plus_48h,
            },
        )
```

Add `import time` at the top of `repository.py` if not already present.

- [ ] **Step 4: Implement `query_stats_live_buckets`**

Add to `api/src/cache/repository.py`:

```python
    def query_stats_live_buckets(
        self, workspace_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        """Query all live stats buckets for a scope."""
        pk = build_pk(self.application_id, self.client_id)
        sk_prefix = f"STATS_LIVE#WS#{workspace_id}#PROJ#{project_id}#"

        all_items: list[dict[str, Any]] = []
        last_key: dict | None = None
        first = True

        while first or last_key is not None:
            first = False
            kwargs: dict[str, Any] = {
                "KeyConditionExpression": (Key("PK").eq(pk) & Key("SK").begins_with(sk_prefix)),
            }
            if last_key is not None:
                kwargs["ExclusiveStartKey"] = last_key

            response = self.table.query(**kwargs)
            all_items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")

        return all_items
```

- [ ] **Step 5: Implement `put_stats_period`**

Add to `api/src/cache/repository.py`:

```python
    def put_stats_period(self, period: "StatsPeriodModel") -> None:
        """Write a pre-aggregated stats period item."""
        from src.cache.normalizer import build_gsi_stats_pk, build_stats_period_sk

        pk = build_pk(self.application_id, self.client_id)
        sk = build_stats_period_sk(period.period, period.timestamp)
        gsi4pk = build_gsi_stats_pk(
            self.application_id, self.client_id, period.workspace_id, period.project_id
        )
        gsi4sk = f"STATS#{period.period}#{period.timestamp}"

        item: dict[str, Any] = {
            "PK": pk,
            "SK": sk,
            "GSI4PK": gsi4pk,
            "GSI4SK": gsi4sk,
            "workspace_id": period.workspace_id,
            "project_id": period.project_id,
            "period": period.period,
            "timestamp": period.timestamp,
            "exact_hits": period.exact_hits,
            "semantic_hits": period.semantic_hits,
            "misses": period.misses,
            "total_lookups": period.total_lookups,
            "hit_rate": str(period.hit_rate),
            "exact_hit_rate": str(period.exact_hit_rate),
            "semantic_hit_rate": str(period.semantic_hit_rate),
            "tokens_saved_input": period.tokens_saved_input,
            "tokens_saved_output": period.tokens_saved_output,
            "estimated_cost_saved_usd": str(period.estimated_cost_saved_usd),
            "total_entries": period.total_entries,
            "ttl": period.ttl,
        }

        self.table.put_item(Item=item)
```

- [ ] **Step 6: Implement `query_stats_period`**

Add to `api/src/cache/repository.py`:

```python
    def query_stats_period(
        self, workspace_id: str, project_id: str, period: str
    ) -> "StatsPeriodModel | None":
        """Query the most recent pre-aggregated stats for a period via GSI4."""
        from src.cache.models import StatsPeriodModel
        from src.cache.normalizer import build_gsi_stats_pk

        gsi4pk = build_gsi_stats_pk(
            self.application_id, self.client_id, workspace_id, project_id
        )

        response = self.table.query(
            IndexName="GSI4",
            KeyConditionExpression=(
                Key("GSI4PK").eq(gsi4pk) & Key("GSI4SK").begins_with(f"STATS#{period}#")
            ),
            ScanIndexForward=False,
            Limit=1,
        )

        items = response.get("Items", [])
        if not items:
            return None

        item = items[0]
        return StatsPeriodModel(
            workspace_id=item.get("workspace_id", workspace_id),
            project_id=item.get("project_id", project_id),
            period=item.get("period", period),
            timestamp=item.get("timestamp", ""),
            exact_hits=int(item.get("exact_hits", 0)),
            semantic_hits=int(item.get("semantic_hits", 0)),
            misses=int(item.get("misses", 0)),
            total_lookups=int(item.get("total_lookups", 0)),
            hit_rate=float(item.get("hit_rate", 0)),
            exact_hit_rate=float(item.get("exact_hit_rate", 0)),
            semantic_hit_rate=float(item.get("semantic_hit_rate", 0)),
            tokens_saved_input=int(item.get("tokens_saved_input", 0)),
            tokens_saved_output=int(item.get("tokens_saved_output", 0)),
            estimated_cost_saved_usd=float(item.get("estimated_cost_saved_usd", 0)),
            total_entries=int(item.get("total_entries", 0)),
            ttl=int(item.get("ttl", 0)),
        )
```

- [ ] **Step 7: Add GSI4 to test conftest**

In `api/tests/conftest.py`, add GSI4 attribute definitions and index to `dynamodb_tables` fixture. In the `AttributeDefinitions` list, add:

```python
                {"AttributeName": "GSI4PK", "AttributeType": "S"},
                {"AttributeName": "GSI4SK", "AttributeType": "S"},
```

In the `GlobalSecondaryIndexes` list, add:

```python
                {
                    "IndexName": "GSI4",
                    "KeySchema": [
                        {"AttributeName": "GSI4PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI4SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest tests/cache/test_repository.py::TestStatsBucket -v`

Expected: PASS

- [ ] **Step 9: Run full test suite**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest -v`

Expected: All tests pass.

- [ ] **Step 10: Commit**

```bash
cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer
git add api/src/cache/repository.py api/tests/cache/test_repository.py api/tests/conftest.py
git commit -m "feat: add stats bucket increment and period CRUD to repository"
```

---

## Task 5: Stats — Pricing Module + Schemas + Service + Endpoint

**Files:**
- Create: `api/src/cache/pricing.py`
- Modify: `api/src/cache/schemas.py`
- Modify: `api/src/cache/service.py`
- Modify: `api/src/cache/router.py`
- Test: `api/tests/cache/test_service.py`
- Test: `api/tests/cache/test_router.py`

- [ ] **Step 1: Create pricing module**

Create `api/src/cache/pricing.py`:

```python
"""Model pricing table for cost savings estimation."""

MODEL_PRICING: dict[str, dict[str, float]] = {
    "anthropic.claude-sonnet-4-5-20250929": {
        "input": 3.00 / 1_000_000,
        "output": 15.00 / 1_000_000,
    },
    "anthropic.claude-haiku-4-5-20251001": {
        "input": 0.80 / 1_000_000,
        "output": 4.00 / 1_000_000,
    },
}

DEFAULT_MODEL = "anthropic.claude-sonnet-4-5-20250929"


def estimate_cost_saved(tokens_input: int, tokens_output: int, model: str = "") -> float:
    """Estimate USD saved by serving from cache instead of invoking the model."""
    rates = MODEL_PRICING.get(model, MODEL_PRICING[DEFAULT_MODEL])
    return (tokens_input * rates["input"]) + (tokens_output * rates["output"])
```

- [ ] **Step 2: Add stats response schemas**

Add to `api/src/cache/schemas.py`:

```python
class TokensSaved(ApiModel):
    """Token counts saved by cache hits."""

    input: int = 0
    output: int = 0


class CacheStatsDetail(ApiModel):
    """Detailed cache statistics for a period."""

    total_lookups: int = 0
    exact_hits: int = 0
    semantic_hits: int = 0
    misses: int = 0
    hit_rate: float = 0.0
    exact_hit_rate: float = 0.0
    semantic_hit_rate: float = 0.0
    total_entries: int = 0
    estimated_cost_saved_usd: float = 0.0
    estimated_tokens_saved: TokensSaved = Field(default_factory=TokensSaved)


class CacheStatsResponse(ApiModel):
    """GET /v1/cache/stats response body."""

    workspace_id: str
    project_id: str
    period: str
    stats: CacheStatsDetail
```

- [ ] **Step 3: Write failing test for stats service + endpoint**

Add to `api/tests/cache/test_service.py`:

```python
class TestGetStats:
    """Tests for GET /v1/cache/stats."""

    def test_get_stats_returns_defaults_when_no_data(self, cache_service):
        """Returns zeroed stats when no data exists."""
        result = cache_service.get_stats("ws_01", "proj_01", "24h")
        assert result.period == "24h"
        assert result.stats.total_lookups == 0
        assert result.stats.hit_rate == 0.0
        assert result.stats.estimated_cost_saved_usd == 0.0

    def test_get_stats_returns_aggregated_data(self, cache_service):
        """Returns pre-aggregated stats when data exists."""
        from src.cache.models import StatsPeriodModel
        period = StatsPeriodModel(
            workspace_id="ws_01", project_id="proj_01",
            period="24h", timestamp="2026-04-01T14:00",
            exact_hits=100, semantic_hits=30, misses=50,
            total_lookups=180, hit_rate=0.722,
            exact_hit_rate=0.556, semantic_hit_rate=0.167,
            tokens_saved_input=50000, tokens_saved_output=30000,
            estimated_cost_saved_usd=1.23, total_entries=42,
            ttl=9999999999,
        )
        cache_service.repository.put_stats_period(period)

        result = cache_service.get_stats("ws_01", "proj_01", "24h")
        assert result.stats.total_lookups == 180
        assert result.stats.hit_rate == 0.722
        assert result.stats.estimated_cost_saved_usd == 1.23
        assert result.stats.estimated_tokens_saved.input == 50000
```

Add to `api/tests/cache/test_router.py`:

```python
class TestCacheStats:
    def test_get_stats_returns_defaults(self, client):
        response = client.get(
            "/v1/cache/stats",
            params={"workspace_id": "ws_01", "project_id": "proj_01"},
        )
        assert response.status_code == 200
        data = unwrap(response)
        assert data["period"] == "24h"
        assert data["stats"]["totalLookups"] == 0

    def test_get_stats_with_period(self, client):
        response = client.get(
            "/v1/cache/stats",
            params={"workspace_id": "ws_01", "project_id": "proj_01", "period": "7d"},
        )
        assert response.status_code == 200
        data = unwrap(response)
        assert data["period"] == "7d"

    def test_get_stats_requires_auth(self, unauth_client):
        response = unauth_client.get(
            "/v1/cache/stats",
            params={"workspace_id": "ws_01", "project_id": "proj_01"},
        )
        assert response.status_code == 401
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest tests/cache/test_service.py::TestGetStats tests/cache/test_router.py::TestCacheStats -v`

Expected: FAIL — `get_stats()` method doesn't exist.

- [ ] **Step 5: Implement `get_stats()` in service**

Add to `api/src/cache/service.py`:

```python
    def get_stats(
        self, workspace_id: str, project_id: str, period: str = "24h"
    ) -> "CacheStatsResponse":
        """Get pre-aggregated stats for a scope and period."""
        from src.cache.schemas import CacheStatsDetail, CacheStatsResponse, TokensSaved

        result = self.repository.query_stats_period(workspace_id, project_id, period)

        if result is None:
            return CacheStatsResponse(
                workspace_id=workspace_id,
                project_id=project_id,
                period=period,
                stats=CacheStatsDetail(),
            )

        return CacheStatsResponse(
            workspace_id=workspace_id,
            project_id=project_id,
            period=period,
            stats=CacheStatsDetail(
                total_lookups=result.total_lookups,
                exact_hits=result.exact_hits,
                semantic_hits=result.semantic_hits,
                misses=result.misses,
                hit_rate=result.hit_rate,
                exact_hit_rate=result.exact_hit_rate,
                semantic_hit_rate=result.semantic_hit_rate,
                total_entries=result.total_entries,
                estimated_cost_saved_usd=result.estimated_cost_saved_usd,
                estimated_tokens_saved=TokensSaved(
                    input=result.tokens_saved_input,
                    output=result.tokens_saved_output,
                ),
            ),
        )
```

- [ ] **Step 6: Add the stats endpoint to the router**

Add to `api/src/cache/router.py`:

```python
from src.cache.schemas import CacheStatsResponse

@router.get("/stats", response_model=CacheStatsResponse)
def cache_stats(
    auth: Auth,
    service: CacheServiceDep,
    workspace_id: str = Query(..., description="Workspace ID"),
    project_id: str = Query(..., description="Project ID"),
    period: str = Query("24h", description="Stats period: 1h, 24h, 7d, 30d"),
) -> CacheStatsResponse:
    """Get cache statistics for a scope."""
    require_read(auth)
    return service.get_stats(workspace_id, project_id, period)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest tests/cache/test_service.py::TestGetStats tests/cache/test_router.py::TestCacheStats -v`

Expected: PASS

- [ ] **Step 8: Write test for stats increment on lookup hit**

Add to `api/tests/cache/test_service.py`:

```python
class TestStatsIncrement:
    """Tests for stats counter increment during lookup."""

    def test_exact_hit_increments_stats(self, cache_service):
        """An exact hit increments the exact_hits counter."""
        from src.cache.schemas import CachedResponse, CacheWriteRequest, CacheLookupRequest

        # Write an entry
        write_req = CacheWriteRequest(
            workspace_id="ws_01", project_id="proj_01",
            query="stats test query?",
            response=CachedResponse(content="answer", model="test-model", tokens_used={"input": 100, "output": 50}),
        )
        cache_service.write(write_req)

        # Lookup triggers stats increment
        lookup_req = CacheLookupRequest(
            workspace_id="ws_01", project_id="proj_01", query="stats test query?",
        )
        result = cache_service.lookup(lookup_req)
        assert result.status == "hit"

        # Verify stats bucket was incremented
        buckets = cache_service.repository.query_stats_live_buckets("ws_01", "proj_01")
        assert len(buckets) >= 1
        bucket = buckets[0]
        assert int(bucket.get("exact_hits", 0)) == 1
        assert int(bucket.get("tokens_saved_input", 0)) == 100
        assert int(bucket.get("tokens_saved_output", 0)) == 50

    def test_miss_increments_stats(self, cache_service):
        """A miss increments the misses counter."""
        from src.cache.schemas import CacheLookupRequest

        lookup_req = CacheLookupRequest(
            workspace_id="ws_01", project_id="proj_01", query="nonexistent query?",
        )
        result = cache_service.lookup(lookup_req)
        assert result.status == "miss"

        buckets = cache_service.repository.query_stats_live_buckets("ws_01", "proj_01")
        assert len(buckets) >= 1
        bucket = buckets[0]
        assert int(bucket.get("misses", 0)) == 1
```

- [ ] **Step 9: Implement `_increment_stats` in service and wire into lookup**

Add to `api/src/cache/service.py`:

```python
    def _increment_stats(
        self,
        workspace_id: str,
        project_id: str,
        hit_type: str,
        tokens_input: int = 0,
        tokens_output: int = 0,
    ) -> None:
        """Best-effort increment of the live stats bucket."""
        now = datetime.now(UTC)
        bucket = now.strftime("%Y-%m-%dT%H:") + f"{(now.minute // 15) * 15:02d}"
        try:
            self.repository.increment_stats_bucket(
                workspace_id, project_id, bucket, hit_type, tokens_input, tokens_output
            )
        except Exception:
            logger.warning("stats.increment_failed", hit_type=hit_type)
```

In the `lookup()` method, after calling `self._build_hit_response(entry, "exact", ...)` for exact hits (around line 91), add a stats increment call. The cleanest approach: add `_increment_stats` calls inside `_build_hit_response()` and in the miss return path.

In `_build_hit_response()`, add after the `increment_hit_count` try/except block (around line 188):

```python
        self._increment_stats(
            entry.workspace_id,
            entry.project_id,
            f"{source}_hits",
            tokens_input=entry.tokens_used.get("input", 0),
            tokens_output=entry.tokens_used.get("output", 0),
        )
```

In `lookup()`, right before each miss return (there are 3 — lines ~84, ~129, and ~158), add:

```python
        self._increment_stats(request.workspace_id, request.project_id, "misses")
```

- [ ] **Step 10: Run tests to verify they pass**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest tests/cache/test_service.py::TestStatsIncrement -v`

Expected: PASS

- [ ] **Step 11: Run full suite + lint + format**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest -v && uv run ruff check src tests && uv run ruff format --check src tests`

Expected: All pass.

- [ ] **Step 12: Commit**

```bash
cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer
git add api/src/cache/pricing.py api/src/cache/schemas.py api/src/cache/service.py api/src/cache/router.py api/tests/cache/test_service.py api/tests/cache/test_router.py
git commit -m "feat: add stats endpoint with live counter increment on lookup"
```

---

## Task 6: Lookup-or-Exec — Schema + Service + Endpoint

**Files:**
- Modify: `api/src/cache/schemas.py`
- Modify: `api/src/cache/service.py`
- Modify: `api/src/cache/dependencies.py`
- Modify: `api/src/cache/router.py`
- Test: `api/tests/cache/test_service.py`
- Test: `api/tests/cache/test_router.py`

- [ ] **Step 1: Add lookup-or-exec schemas**

Add to `api/src/cache/schemas.py`:

```python
class OnMissConfig(ApiModel):
    """Configuration for what to do on a cache miss in lookup-or-exec."""

    model: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    cache_response: bool = True
    ttl_seconds: int = 86400


class LookupOrExecRequest(ApiModel):
    """POST /v1/cache/lookup-or-exec request body."""

    workspace_id: str
    project_id: str
    query: str
    request_id: str | None = None
    context_hash: str | None = None
    lookup_config: LookupConfig = Field(default_factory=LookupConfig)
    on_miss: OnMissConfig


class LookupOrExecResponse(ApiModel):
    """POST /v1/cache/lookup-or-exec response body."""

    request_id: str | None = None
    status: str  # "hit" or "miss_executed"
    source: str | None = None  # "exact", "semantic", or "model_gateway"
    cache_entry_id: str | None = None
    response: CachedResponse | None = None
    similarity_score: float | None = None
    matched_query: str | None = None
    cache_metadata: CacheMetadata | None = None
    lookup_latency_ms: float = 0
    stages: LookupStages | None = None
```

- [ ] **Step 2: Write failing tests for lookup-or-exec**

Add to `api/tests/cache/test_service.py`:

```python
from unittest.mock import MagicMock, patch


class TestLookupOrExec:
    """Tests for the lookup-or-exec cache-aside pattern."""

    def test_hit_returns_cached(self, cache_service):
        """On cache hit, returns the cached response without invoking Model Gateway."""
        from src.cache.schemas import (
            CachedResponse, CacheWriteRequest,
            LookupOrExecRequest, OnMissConfig,
        )

        # Pre-populate cache
        cache_service.write(CacheWriteRequest(
            workspace_id="ws_01", project_id="proj_01",
            query="cached question?",
            response=CachedResponse(content="cached answer", model="m", tokens_used={"input": 10, "output": 5}),
        ))

        req = LookupOrExecRequest(
            workspace_id="ws_01", project_id="proj_01",
            query="cached question?",
            on_miss=OnMissConfig(model="anthropic.claude-sonnet-4-5-20250929", messages=[]),
        )

        result = cache_service.lookup_or_exec(req)
        assert result.status == "hit"
        assert result.source == "exact"
        assert result.response.content == "cached answer"

    def test_miss_invokes_gateway_and_caches(self, cache_service):
        """On cache miss, invokes Model Gateway SDK and caches the result."""
        from src.cache.schemas import (
            CacheLookupRequest,
            LookupOrExecRequest, OnMissConfig,
        )

        # Mock the gateway client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "generated answer"
        mock_response.usage.input_tokens = 50
        mock_response.usage.output_tokens = 30
        mock_response.gateway.model_alias = "anthropic.claude-sonnet-4-5-20250929"

        mock_client = MagicMock()
        mock_client.invoke.return_value = mock_response
        cache_service.gateway_client = mock_client

        req = LookupOrExecRequest(
            workspace_id="ws_01", project_id="proj_01",
            query="new question?",
            on_miss=OnMissConfig(
                model="anthropic.claude-sonnet-4-5-20250929",
                messages=[{"role": "user", "content": "new question?"}],
            ),
        )

        result = cache_service.lookup_or_exec(req)
        assert result.status == "miss_executed"
        assert result.source == "model_gateway"
        assert result.response.content == "generated answer"

        # Verify it was cached
        lookup = CacheLookupRequest(
            workspace_id="ws_01", project_id="proj_01", query="new question?",
        )
        cached = cache_service.lookup(lookup)
        assert cached.status == "hit"

    def test_miss_without_gateway_returns_503(self, cache_service):
        """If gateway client is not configured, returns error on miss."""
        from fastapi import HTTPException
        from src.cache.schemas import LookupOrExecRequest, OnMissConfig

        cache_service.gateway_client = None

        req = LookupOrExecRequest(
            workspace_id="ws_01", project_id="proj_01",
            query="uncached question?",
            on_miss=OnMissConfig(model="m", messages=[]),
        )

        from src.common.exceptions import GatewayNotConfiguredError
        import pytest
        with pytest.raises(GatewayNotConfiguredError):
            cache_service.lookup_or_exec(req)

    def test_miss_no_cache_when_disabled(self, cache_service):
        """When cache_response=False, the result is not cached."""
        from src.cache.schemas import (
            CacheLookupRequest,
            LookupOrExecRequest, OnMissConfig,
        )

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ephemeral answer"
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 5
        mock_response.gateway.model_alias = "m"

        mock_client = MagicMock()
        mock_client.invoke.return_value = mock_response
        cache_service.gateway_client = mock_client

        req = LookupOrExecRequest(
            workspace_id="ws_01", project_id="proj_01",
            query="ephemeral question?",
            on_miss=OnMissConfig(model="m", messages=[], cache_response=False),
        )

        result = cache_service.lookup_or_exec(req)
        assert result.status == "miss_executed"

        # Should NOT be cached
        lookup = CacheLookupRequest(
            workspace_id="ws_01", project_id="proj_01", query="ephemeral question?",
        )
        cached = cache_service.lookup(lookup)
        assert cached.status == "miss"
```

- [ ] **Step 3: Add `GatewayNotConfiguredError` to exceptions**

In `api/src/common/exceptions.py`, add:

```python
class GatewayNotConfiguredError(AppError):
    def __init__(self):
        super().__init__(
            "Lookup-or-exec requires Model Gateway integration",
            code="GATEWAY_NOT_CONFIGURED",
        )
```

Add to `EXCEPTION_STATUS_MAP`:

```python
    GatewayNotConfiguredError: 503,
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest tests/cache/test_service.py::TestLookupOrExec -v`

Expected: FAIL — `lookup_or_exec` not defined, `gateway_client` not on service.

- [ ] **Step 5: Wire `gateway_client` into CacheService**

In `api/src/cache/service.py`, update `__init__`:

```python
    def __init__(
        self,
        repository: CacheRepository,
        opensearch_repo=None,
        embedding_service=None,
        gateway_client=None,
    ):
        self.repository = repository
        self.opensearch_repo = opensearch_repo
        self.embedding_service = embedding_service
        self.gateway_client = gateway_client
```

In `api/src/cache/dependencies.py`, update `get_cache_service`:

```python
def get_cache_service(
    repo: CacheRepository = Depends(get_cache_repository),
) -> CacheService:
    """Build a CacheService with the tenant-scoped repository."""
    return CacheService(
        repository=repo,
        opensearch_repo=get_opensearch_repository(),
        embedding_service=get_embedding_service(),
        gateway_client=_get_gateway_client(),
    )
```

- [ ] **Step 6: Implement `lookup_or_exec` in service**

Add to `api/src/cache/service.py`:

```python
    def lookup_or_exec(
        self, request: "LookupOrExecRequest"
    ) -> "LookupOrExecResponse":
        """Cache-aside: lookup first, on miss invoke Model Gateway SDK."""
        from src.cache.schemas import (
            CachedResponse,
            CacheLookupRequest,
            CacheWriteRequest,
            LookupOrExecResponse,
            WriteConfig,
        )
        from src.common.exceptions import GatewayNotConfiguredError

        # Step 1: Try cache lookup
        lookup_req = CacheLookupRequest(
            workspace_id=request.workspace_id,
            project_id=request.project_id,
            query=request.query,
            request_id=request.request_id,
            context_hash=request.context_hash,
            lookup_config=request.lookup_config,
        )

        lookup_result = self.lookup(lookup_req)

        if lookup_result.status == "hit":
            return LookupOrExecResponse(
                request_id=request.request_id,
                status="hit",
                source=lookup_result.source,
                cache_entry_id=lookup_result.cache_entry_id,
                response=lookup_result.response,
                similarity_score=lookup_result.similarity_score,
                matched_query=lookup_result.matched_query,
                cache_metadata=lookup_result.cache_metadata,
                lookup_latency_ms=lookup_result.lookup_latency_ms,
                stages=lookup_result.stages,
            )

        # Step 2: Cache miss — invoke Model Gateway
        if self.gateway_client is None:
            raise GatewayNotConfiguredError()

        start = time.monotonic()
        gw_response = self.gateway_client.invoke(
            model=request.on_miss.model,
            messages=request.on_miss.messages,
            max_tokens=4096,
        )
        invoke_ms = (time.monotonic() - start) * 1000

        content = gw_response.choices[0].message.content
        input_tokens = gw_response.usage.input_tokens
        output_tokens = gw_response.usage.output_tokens
        model_alias = gw_response.gateway.model_alias

        cached_response = CachedResponse(
            content=content,
            model=model_alias,
            tokens_used={"input": input_tokens, "output": output_tokens},
        )

        # Step 3: Cache the result (if enabled)
        cache_entry_id = None
        if request.on_miss.cache_response:
            write_req = CacheWriteRequest(
                workspace_id=request.workspace_id,
                project_id=request.project_id,
                query=request.query,
                response=cached_response,
                request_id=request.request_id,
                context_hash=request.context_hash,
                write_config=WriteConfig(ttl_seconds=request.on_miss.ttl_seconds),
            )
            write_result = self.write(write_req)
            cache_entry_id = write_result.cache_entry_id

        total_ms = lookup_result.lookup_latency_ms + invoke_ms

        return LookupOrExecResponse(
            request_id=request.request_id,
            status="miss_executed",
            source="model_gateway",
            cache_entry_id=cache_entry_id,
            response=cached_response,
            lookup_latency_ms=round(total_ms, 2),
            stages=lookup_result.stages,
        )
```

- [ ] **Step 7: Add the endpoint to the router**

Add to `api/src/cache/router.py`:

```python
from src.cache.schemas import LookupOrExecRequest, LookupOrExecResponse

@router.post("/lookup-or-exec", response_model=LookupOrExecResponse)
def cache_lookup_or_exec(
    body: LookupOrExecRequest,
    auth: Auth,
    service: CacheServiceDep,
) -> LookupOrExecResponse:
    """Cache-aside lookup: hit returns cached, miss invokes Model Gateway and caches."""
    require_write(auth)
    return service.lookup_or_exec(body)
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest tests/cache/test_service.py::TestLookupOrExec -v`

Expected: PASS

- [ ] **Step 9: Add router integration test**

Add to `api/tests/cache/test_router.py`:

```python
class TestCacheLookupOrExec:
    def test_hit_returns_cached(self, client):
        # Write an entry first
        client.post("/v1/cache/write", json={
            "workspaceId": "ws_01", "projectId": "proj_01",
            "query": "lookup-or-exec cached?",
            "response": {"content": "cached answer", "model": "m", "tokensUsed": {}},
        })

        response = client.post("/v1/cache/lookup-or-exec", json={
            "workspaceId": "ws_01", "projectId": "proj_01",
            "query": "lookup-or-exec cached?",
            "onMiss": {"model": "m", "messages": []},
        })
        assert response.status_code == 200
        data = unwrap(response)
        assert data["status"] == "hit"
        assert data["response"]["content"] == "cached answer"

    def test_requires_write_scope(self, read_client):
        response = read_client.post("/v1/cache/lookup-or-exec", json={
            "workspaceId": "ws_01", "projectId": "proj_01",
            "query": "test?",
            "onMiss": {"model": "m", "messages": []},
        })
        assert response.status_code == 403
```

- [ ] **Step 10: Run full suite + lint + format**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest -v && uv run ruff check src tests && uv run ruff format --check src tests`

Expected: All pass.

- [ ] **Step 11: Commit**

```bash
cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer
git add api/src/cache/schemas.py api/src/cache/service.py api/src/cache/dependencies.py api/src/cache/router.py api/src/common/exceptions.py api/tests/cache/test_service.py api/tests/cache/test_router.py
git commit -m "feat: add lookup-or-exec cache-aside endpoint with Model Gateway SDK"
```

---

## Task 7: Stats Aggregator Lambda

**Files:**
- Create: `api/src/stats_aggregator.py`
- Modify: `api/src/config.py`
- Test: `api/tests/test_stats_aggregator.py` (new)

- [ ] **Step 1: Add aggregator config settings**

In `api/src/config.py`, add to the `Settings` class:

```python
    # Stats aggregator (Phase 4)
    application_id: str = ""
    client_id: str = ""
```

- [ ] **Step 2: Write failing test for aggregator**

Create `api/tests/test_stats_aggregator.py`:

```python
"""Tests for the stats aggregator Lambda."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.cache.models import StatsPeriodModel


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.application_id = "test-app"
    repo.client_id = "test-client"
    repo.query_stats_live_buckets.return_value = []
    repo.query_by_project.return_value = ([], None)
    return repo


@pytest.fixture
def _patch_deps(mock_repo):
    mock_table = MagicMock()
    mock_dynamodb = MagicMock()
    mock_dynamodb.Table.return_value = mock_table

    with (
        patch("src.stats_aggregator.boto3.resource", return_value=mock_dynamodb),
        patch("src.stats_aggregator.CacheRepository", return_value=mock_repo),
        patch("src.stats_aggregator.get_settings") as mock_settings,
    ):
        mock_settings.return_value.aws_region = "us-east-1"
        mock_settings.return_value.dynamodb_endpoint_url = None
        mock_settings.return_value.dynamodb_table = "test-table"
        mock_settings.return_value.application_id = "test-app"
        mock_settings.return_value.client_id = "test-client"
        yield


class TestStatsAggregator:
    def test_empty_buckets_no_error(self, mock_repo, _patch_deps):
        from src.stats_aggregator import handler
        result = handler({}, None)
        assert result["status"] == "ok"

    def test_aggregates_live_buckets(self, mock_repo, _patch_deps):
        from src.stats_aggregator import handler

        mock_repo.query_stats_live_buckets.return_value = [
            {
                "exact_hits": 10, "semantic_hits": 3, "misses": 5,
                "tokens_saved_input": 1000, "tokens_saved_output": 500,
                "workspace_id": "ws_01", "project_id": "proj_01",
                "bucket": "2026-04-01T14:00",
            },
            {
                "exact_hits": 20, "semantic_hits": 7, "misses": 10,
                "tokens_saved_input": 2000, "tokens_saved_output": 1000,
                "workspace_id": "ws_01", "project_id": "proj_01",
                "bucket": "2026-04-01T14:15",
            },
        ]
        mock_repo.query_by_project.return_value = ([], None)

        result = handler({}, None)
        assert result["status"] == "ok"
        assert result["scopes_processed"] >= 1
        mock_repo.put_stats_period.assert_called()

    def test_missing_app_id_skips(self, _patch_deps):
        from src.stats_aggregator import handler
        with patch("src.stats_aggregator.get_settings") as mock_s:
            mock_s.return_value.application_id = ""
            mock_s.return_value.client_id = ""
            mock_s.return_value.aws_region = "us-east-1"
            mock_s.return_value.dynamodb_endpoint_url = None
            mock_s.return_value.dynamodb_table = "t"
            result = handler({}, None)
            assert result["status"] == "skipped"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest tests/test_stats_aggregator.py -v`

Expected: FAIL — module doesn't exist.

- [ ] **Step 4: Implement stats aggregator Lambda**

Create `api/src/stats_aggregator.py`:

```python
"""Stats aggregator Lambda — rolls up live DynamoDB counters into period stats.

Triggered every 15 minutes by CloudWatch EventBridge scheduled rule.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import structlog

from src.cache.models import StatsPeriodModel
from src.cache.pricing import estimate_cost_saved
from src.cache.repository import CacheRepository
from src.config import get_settings

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

PERIOD_CONFIGS = {
    "1h": {"buckets": 4, "ttl_days": 2},
    "24h": {"buckets": 96, "ttl_days": 30},
    "7d": {"buckets": 672, "ttl_days": 90},
    "30d": {"buckets": 2880, "ttl_days": 365},
}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Aggregate live stats buckets into pre-aggregated period stats."""
    settings = get_settings()

    if not settings.application_id or not settings.client_id:
        logger.warning("stats_aggregator.missing_tenant_config")
        return {"status": "skipped", "reason": "missing_tenant_config"}

    dynamodb = boto3.resource(
        "dynamodb",
        region_name=settings.aws_region,
        endpoint_url=settings.dynamodb_endpoint_url,
    )
    table = dynamodb.Table(settings.dynamodb_table)
    repo = CacheRepository(table, settings.application_id, settings.client_id)

    now = datetime.now(UTC)

    # Discover active scopes from live buckets
    pk_val = f"APP#{settings.application_id}#CLIENT#{settings.client_id}"
    all_live = []
    last_key = None
    first = True

    while first or last_key is not None:
        first = False
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": (
                boto3.dynamodb.conditions.Key("PK").eq(pk_val)
                & boto3.dynamodb.conditions.Key("SK").begins_with("STATS_LIVE#")
            ),
        }
        if last_key is not None:
            kwargs["ExclusiveStartKey"] = last_key
        response = table.query(**kwargs)
        all_live.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")

    # Group by scope
    scopes: dict[tuple[str, str], list[dict]] = {}
    for item in all_live:
        ws = item.get("workspace_id", "")
        proj = item.get("project_id", "")
        scopes.setdefault((ws, proj), []).append(item)

    scopes_processed = 0

    for (ws, proj), buckets in scopes.items():
        # Sum counters across all buckets
        exact_hits = sum(int(b.get("exact_hits", 0)) for b in buckets)
        semantic_hits = sum(int(b.get("semantic_hits", 0)) for b in buckets)
        misses = sum(int(b.get("misses", 0)) for b in buckets)
        tokens_in = sum(int(b.get("tokens_saved_input", 0)) for b in buckets)
        tokens_out = sum(int(b.get("tokens_saved_output", 0)) for b in buckets)
        total = exact_hits + semantic_hits + misses

        # Count active entries
        entries, _ = repo.query_by_project(ws, proj, limit=1)
        total_entries = len(entries)

        cost_saved = estimate_cost_saved(tokens_in, tokens_out)

        for period, cfg in PERIOD_CONFIGS.items():
            hit_rate = (exact_hits + semantic_hits) / total if total > 0 else 0.0
            exact_rate = exact_hits / total if total > 0 else 0.0
            semantic_rate = semantic_hits / total if total > 0 else 0.0

            ttl_epoch = int((now + timedelta(days=cfg["ttl_days"])).timestamp())

            period_model = StatsPeriodModel(
                workspace_id=ws,
                project_id=proj,
                period=period,
                timestamp=now.isoformat(),
                exact_hits=exact_hits,
                semantic_hits=semantic_hits,
                misses=misses,
                total_lookups=total,
                hit_rate=round(hit_rate, 4),
                exact_hit_rate=round(exact_rate, 4),
                semantic_hit_rate=round(semantic_rate, 4),
                tokens_saved_input=tokens_in,
                tokens_saved_output=tokens_out,
                estimated_cost_saved_usd=round(cost_saved, 4),
                total_entries=total_entries,
                ttl=ttl_epoch,
            )
            try:
                repo.put_stats_period(period_model)
            except Exception:
                logger.warning(
                    "stats_aggregator.put_period_failed",
                    period=period, ws=ws, proj=proj,
                )

        scopes_processed += 1

    logger.info(
        "stats_aggregator.completed",
        scopes_processed=scopes_processed,
        total_live_buckets=len(all_live),
    )

    return {"status": "ok", "scopes_processed": scopes_processed}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest tests/test_stats_aggregator.py -v`

Expected: PASS

- [ ] **Step 6: Run full suite + lint + format**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest -v && uv run ruff check src tests && uv run ruff format --check src tests`

- [ ] **Step 7: Commit**

```bash
cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer
git add api/src/stats_aggregator.py api/src/config.py api/tests/test_stats_aggregator.py
git commit -m "feat: add stats aggregator Lambda for periodic stats rollup"
```

---

## Task 8: Terraform — GSI4 + Aggregator Lambda + Scheduled Rule

**Files:**
- Modify: `terraform/dynamodb.tf`
- Modify: `terraform/lambda.tf`
- Modify: `terraform/eventbridge.tf`
- Modify: `terraform/variables.tf`

- [ ] **Step 1: Add GSI4 to DynamoDB table**

In `terraform/dynamodb.tf`, add attribute definitions (after GSI3 attributes):

```hcl
  # GSI4: Stats — pre-aggregated cache statistics
  attribute {
    name = "GSI4PK"
    type = "S"
  }

  attribute {
    name = "GSI4SK"
    type = "S"
  }
```

Add the GSI4 index (after GSI3):

```hcl
  global_secondary_index {
    name            = "GSI4"
    hash_key        = "GSI4PK"
    range_key       = "GSI4SK"
    projection_type = "ALL"
  }
```

- [ ] **Step 2: Add aggregator Lambda variables**

In `terraform/variables.tf`, add:

```hcl
# Stats aggregator Lambda variables
variable "stats_aggregator_lambda_memory_size" {
  type        = number
  description = "Memory size for the Stats Aggregator Lambda (MB)"
  default     = 256
}

variable "stats_aggregator_lambda_timeout" {
  type        = number
  description = "Timeout for the Stats Aggregator Lambda (seconds)"
  default     = 120
}
```

- [ ] **Step 3: Add aggregator Lambda to `lambda.tf`**

Add to `terraform/lambda.tf`:

```hcl
# -----------------------------------------------------------------------------
# Stats Aggregator Lambda — periodic stats rollup (every 15 minutes)
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "stats_aggregator" {
  name              = "/aws/lambda/${local.name_prefix}-stats-aggregator"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${local.name_prefix}-stats-aggregator-logs"
  }
}

resource "aws_lambda_function" "stats_aggregator" {
  function_name = "${local.name_prefix}-stats-aggregator"
  role          = aws_iam_role.stats_aggregator_lambda.arn
  handler       = "src.stats_aggregator.handler"
  runtime       = "python3.12"
  architectures = ["arm64"]
  memory_size   = var.stats_aggregator_lambda_memory_size
  timeout       = var.stats_aggregator_lambda_timeout

  filename         = var.api_lambda_zip_path
  source_code_hash = filebase64sha256(var.api_lambda_zip_path)

  layers = [
    "arn:aws:lambda:${local.region}:901920570463:layer:aws-otel-python-arm64-ver-1-25-0:1",
  ]

  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.main.name
      AWS_REGION_NAME = local.region
      ENVIRONMENT    = var.environment
      LOG_LEVEL      = var.log_level
      APPLICATION_ID = var.application_id
      CLIENT_ID      = var.client_id
    }
  }

  depends_on = [aws_cloudwatch_log_group.stats_aggregator]

  tags = {
    Name = "${local.name_prefix}-stats-aggregator"
  }
}

# -----------------------------------------------------------------------------
# IAM role for Stats Aggregator Lambda
# -----------------------------------------------------------------------------

resource "aws_iam_role" "stats_aggregator_lambda" {
  name = "${local.name_prefix}-stats-aggregator-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  inline_policy {
    name = "stats-aggregator-lambda-policy"
    policy = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid    = "CloudWatchLogs"
          Effect = "Allow"
          Action = [
            "logs:CreateLogGroup",
            "logs:CreateLogStream",
            "logs:PutLogEvents",
          ]
          Resource = "${aws_cloudwatch_log_group.stats_aggregator.arn}:*"
        },
        {
          Sid    = "DynamoDB"
          Effect = "Allow"
          Action = [
            "dynamodb:GetItem",
            "dynamodb:PutItem",
            "dynamodb:UpdateItem",
            "dynamodb:Query",
            "dynamodb:Scan",
          ]
          Resource = [
            aws_dynamodb_table.main.arn,
            "${aws_dynamodb_table.main.arn}/index/*",
          ]
        },
        {
          Sid    = "XRay"
          Effect = "Allow"
          Action = [
            "xray:PutTraceSegments",
            "xray:PutTelemetryRecords",
            "xray:GetSamplingRules",
            "xray:GetSamplingTargets",
          ]
          Resource = "*"
        },
      ]
    })
  }

  tags = {
    Name = "${local.name_prefix}-stats-aggregator-lambda"
  }
}
```

Also add to `terraform/variables.tf`:

```hcl
variable "application_id" {
  type        = string
  description = "Application ID for the cache layer tenant"
  default     = ""
}

variable "client_id" {
  type        = string
  description = "Client ID for the cache layer tenant"
  default     = ""
}
```

- [ ] **Step 4: Add scheduled rule to `eventbridge.tf`**

Add to `terraform/eventbridge.tf`:

```hcl
# -----------------------------------------------------------------------------
# Scheduled rule for stats aggregation (every 15 minutes)
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "stats_aggregator" {
  name                = "${local.name_prefix}-stats-aggregator"
  description         = "Trigger stats aggregation every 15 minutes"
  schedule_expression = "rate(15 minutes)"

  tags = {
    Name = "${local.name_prefix}-stats-aggregator-rule"
  }
}

resource "aws_cloudwatch_event_target" "stats_aggregator" {
  rule = aws_cloudwatch_event_rule.stats_aggregator.name
  arn  = aws_lambda_function.stats_aggregator.arn
}

resource "aws_lambda_permission" "stats_aggregator_eventbridge" {
  statement_id  = "AllowEventBridgeStatsAggregator"
  function_name = aws_lambda_function.stats_aggregator.function_name
  action        = "lambda:InvokeFunction"
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.stats_aggregator.arn
}
```

- [ ] **Step 5: Commit**

```bash
cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer
git add terraform/dynamodb.tf terraform/lambda.tf terraform/eventbridge.tf terraform/variables.tf
git commit -m "infra: add GSI4 (Stats), aggregator Lambda, and 15-min scheduled rule"
```

---

## Task 9: OpenAPI Polish + App Metadata

**Files:**
- Modify: `api/src/main.py`

- [ ] **Step 1: Update app metadata**

In `api/src/main.py`, update the FastAPI app constructor:

```python
app = FastAPI(
    title="Bold Cache Layer API",
    description="Intelligent response caching for AI applications. "
    "Provides exact match and semantic similarity caching with "
    "lookup-or-exec cache-aside pattern, statistics, and cost savings tracking.",
    version="0.4.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)
```

- [ ] **Step 2: Verify OpenAPI schema includes new endpoints**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run python -c "from src.main import app; import json; schema = app.openapi(); paths = list(schema['paths'].keys()); print(json.dumps(paths, indent=2)); assert '/v1/cache/lookup-or-exec' in paths; assert '/v1/cache/stats' in paths; print('OK: all new endpoints present')"`

Expected: OK message with all endpoints listed.

- [ ] **Step 3: Commit**

```bash
cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer
git add api/src/main.py
git commit -m "chore: update app metadata and version for Phase 4"
```

---

## Task 10: Final Verification + Memory Update

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run pytest -v`

Expected: All tests pass (125 existing + ~30 new ≈ 155+ total).

- [ ] **Step 2: Lint + format check**

Run: `cd /Users/william.rood/Projects/bold-blocks/platform-block-cache-layer/api && uv run ruff check src tests && uv run ruff format --check src tests`

Expected: Clean.

- [ ] **Step 3: Update MEMORY.md**

Update `/Users/william.rood/.claude/projects/-Users-william-rood-Projects-bold-blocks-platform-block-cache-layer/memory/MEMORY.md` with Phase 4 completion status, new DynamoDB keys (STATS_LIVE SK, STATS period SK, GSI4PK/GSI4SK), new exceptions (GatewayNotConfiguredError), and total test count.
