"""DynamoDB operations for cache entries."""

from typing import Any

import structlog
from boto3.dynamodb.conditions import Key

from src.cache.models import CacheEntryModel
from src.cache.normalizer import (
    build_cache_sk,
    build_gsi_project_entries_pk,
    build_gsi_query_hash_pk,
    build_pk,
)
from src.common.exceptions import CacheEntryNotFoundError, CacheWriteFailedError

logger = structlog.get_logger()


class CacheRepository:
    """DynamoDB repository for cache entries."""

    def __init__(self, table: Any, application_id: str, client_id: str):
        self.table = table
        self.application_id = application_id
        self.client_id = client_id

    def get_by_hash(
        self,
        workspace_id: str,
        project_id: str,
        query_hash: str,
    ) -> CacheEntryModel | None:
        """Look up a cache entry by query hash via GSI1 (QueryHash).

        Returns the first active entry matching the hash within the given scope,
        or None if no match is found.
        """
        gsi_pk = build_gsi_query_hash_pk(self.application_id, self.client_id, query_hash)

        response = self.table.query(
            IndexName="GSI1",
            KeyConditionExpression=Key("GSI1PK").eq(gsi_pk),
        )

        items = response.get("Items", [])
        for item in items:
            if (
                item.get("workspace_id") == workspace_id
                and item.get("project_id") == project_id
                and item.get("status") == "active"
            ):
                return self._item_to_model(item)

        return None

    def put(self, entry: CacheEntryModel) -> None:
        """Write a cache entry to DynamoDB."""
        pk = build_pk(self.application_id, self.client_id)
        sk = build_cache_sk(entry.workspace_id, entry.project_id, entry.cache_entry_id)
        gsi1pk = build_gsi_query_hash_pk(self.application_id, self.client_id, entry.query_hash)
        gsi1sk = f"CACHE#{entry.cache_entry_id}"
        gsi2pk = build_gsi_project_entries_pk(
            self.application_id, self.client_id, entry.workspace_id, entry.project_id
        )
        gsi2sk = f"CREATED#{entry.created_at}"

        item: dict[str, Any] = {
            "PK": pk,
            "SK": sk,
            "GSI1PK": gsi1pk,
            "GSI1SK": gsi1sk,
            "GSI2PK": gsi2pk,
            "GSI2SK": gsi2sk,
            "cache_entry_id": entry.cache_entry_id,
            "application_id": entry.application_id,
            "client_id": entry.client_id,
            "workspace_id": entry.workspace_id,
            "project_id": entry.project_id,
            "query_normalized": entry.query_normalized,
            "query_hash": entry.query_hash,
            "response": entry.response,
            "model": entry.model,
            "tokens_used": entry.tokens_used,
            "citations": entry.citations,
            "hit_count": entry.hit_count,
            "last_hit_at": entry.last_hit_at or "",
            "created_at": entry.created_at,
            "created_by_user": entry.created_by_user or "",
            "original_request_id": entry.original_request_id or "",
            "status": entry.status,
            "ttl": entry.ttl,
        }

        if entry.guardrail_policy_version:
            item["guardrail_policy_version"] = entry.guardrail_policy_version

        try:
            self.table.put_item(Item=item)
        except Exception as exc:
            logger.error("Failed to write cache entry", error=str(exc))
            raise CacheWriteFailedError(f"Failed to write cache entry: {exc}") from exc

    def delete(self, cache_entry_id: str, workspace_id: str, project_id: str) -> None:
        """Delete a cache entry by marking it as invalidated.

        Raises CacheEntryNotFoundError if the entry does not exist.
        """
        pk = build_pk(self.application_id, self.client_id)
        sk = build_cache_sk(workspace_id, project_id, cache_entry_id)

        try:
            self.table.update_item(
                Key={"PK": pk, "SK": sk},
                UpdateExpression="SET #status = :status",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":status": "invalidated"},
                ConditionExpression="attribute_exists(PK)",
            )
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            raise CacheEntryNotFoundError(f"Cache entry {cache_entry_id} not found")

    def increment_hit_count(self, pk: str, sk: str, last_hit_at: str) -> None:
        """Increment the hit count and update last_hit_at on a cache entry."""
        self.table.update_item(
            Key={"PK": pk, "SK": sk},
            UpdateExpression="SET hit_count = hit_count + :inc, last_hit_at = :ts",
            ExpressionAttributeValues={":inc": 1, ":ts": last_hit_at},
        )

    def _item_to_model(self, item: dict[str, Any]) -> CacheEntryModel:
        """Convert a DynamoDB item to a CacheEntryModel."""
        return CacheEntryModel(
            cache_entry_id=item["cache_entry_id"],
            application_id=item["application_id"],
            client_id=item["client_id"],
            workspace_id=item["workspace_id"],
            project_id=item["project_id"],
            query_normalized=item["query_normalized"],
            query_hash=item["query_hash"],
            response=item.get("response", {}),
            model=item.get("model", ""),
            tokens_used=item.get("tokens_used", {}),
            citations=item.get("citations", []),
            guardrail_policy_version=item.get("guardrail_policy_version"),
            hit_count=int(item.get("hit_count", 0)),
            last_hit_at=item.get("last_hit_at") or None,
            created_at=item.get("created_at", ""),
            created_by_user=item.get("created_by_user") or None,
            original_request_id=item.get("original_request_id") or None,
            status=item.get("status", "active"),
            ttl=int(item.get("ttl", 0)),
        )
