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
                    period=period,
                    ws=ws,
                    proj=proj,
                )

        scopes_processed += 1

    logger.info(
        "stats_aggregator.completed",
        scopes_processed=scopes_processed,
        total_live_buckets=len(all_live),
    )

    return {"status": "ok", "scopes_processed": scopes_processed}
