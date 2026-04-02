"""DynamoDB operations for cache entries."""

from typing import Any

import structlog
from boto3.dynamodb.conditions import Key

from src.cache.models import CacheConfigModel, CacheEntryModel, InvalidationEventModel
from src.cache.normalizer import (
    build_cache_sk,
    build_citation_sk,
    build_config_sk,
    build_gsi_citation_pk,
    build_gsi_project_entries_pk,
    build_gsi_query_hash_pk,
    build_invalidation_sk,
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

    def get_by_id(
        self,
        cache_entry_id: str,
        workspace_id: str,
        project_id: str,
    ) -> CacheEntryModel | None:
        """Look up a cache entry by its ID via direct GetItem.

        Returns the entry if it exists and is active, or None.
        """
        pk = build_pk(self.application_id, self.client_id)
        sk = build_cache_sk(workspace_id, project_id, cache_entry_id)

        response = self.table.get_item(Key={"PK": pk, "SK": sk})
        item = response.get("Item")
        if item is None or item.get("status") != "active":
            return None
        return self._item_to_model(item)

    def get_by_hash(
        self,
        workspace_id: str,
        project_id: str,
        query_hash: str,
        context_hash: str | None = None,
    ) -> CacheEntryModel | None:
        """Look up a cache entry by query hash via GSI1 (QueryHash).

        Returns the first active entry matching the hash within the given scope,
        or None if no match is found.
        """
        gsi_pk = build_gsi_query_hash_pk(
            self.application_id, self.client_id, query_hash, context_hash
        )

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
        gsi1pk = build_gsi_query_hash_pk(
            self.application_id, self.client_id, entry.query_hash, entry.context_hash
        )
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

        if entry.context_hash:
            item["context_hash"] = entry.context_hash

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

    # -----------------------------------------------------------------
    # Bulk query operations (Phase 3)
    # -----------------------------------------------------------------

    def query_by_project(
        self, workspace_id: str, project_id: str, limit: int = 100
    ) -> tuple[list[CacheEntryModel], dict | None]:
        """Query active entries for a project via GSI2.

        Returns (entries, last_evaluated_key) for pagination.
        """
        gsi2pk = build_gsi_project_entries_pk(
            self.application_id, self.client_id, workspace_id, project_id
        )

        kwargs: dict[str, Any] = {
            "IndexName": "GSI2",
            "KeyConditionExpression": Key("GSI2PK").eq(gsi2pk),
            "Limit": limit,
        }

        response = self.table.query(**kwargs)
        items = response.get("Items", [])
        entries = [self._item_to_model(item) for item in items if item.get("status") == "active"]
        last_key = response.get("LastEvaluatedKey")
        return entries, last_key

    def query_all_by_project(self, workspace_id: str, project_id: str) -> list[CacheEntryModel]:
        """Query ALL active entries for a project (auto-paginate)."""
        all_entries: list[CacheEntryModel] = []
        last_key: dict | None = None
        first = True

        while first or last_key is not None:
            first = False
            gsi2pk = build_gsi_project_entries_pk(
                self.application_id, self.client_id, workspace_id, project_id
            )
            kwargs: dict[str, Any] = {
                "IndexName": "GSI2",
                "KeyConditionExpression": Key("GSI2PK").eq(gsi2pk),
            }
            if last_key is not None:
                kwargs["ExclusiveStartKey"] = last_key

            response = self.table.query(**kwargs)
            items = response.get("Items", [])
            all_entries.extend(
                self._item_to_model(item) for item in items if item.get("status") == "active"
            )
            last_key = response.get("LastEvaluatedKey")

        return all_entries

    def query_all_by_workspace(self, workspace_id: str) -> list[CacheEntryModel]:
        """Query ALL active entries for a workspace (all projects).

        Uses PK with begins_with on SK for CACHE#WS#{workspace_id}.
        """
        pk = build_pk(self.application_id, self.client_id)
        sk_prefix = f"CACHE#WS#{workspace_id}#"
        all_entries: list[CacheEntryModel] = []
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
            items = response.get("Items", [])
            all_entries.extend(
                self._item_to_model(item) for item in items if item.get("status") == "active"
            )
            last_key = response.get("LastEvaluatedKey")

        return all_entries

    def batch_invalidate(self, entries: list[CacheEntryModel]) -> int:
        """Mark a list of entries as 'invalidated'. Returns count invalidated."""
        count = 0
        for entry in entries:
            pk = build_pk(self.application_id, self.client_id)
            sk = build_cache_sk(entry.workspace_id, entry.project_id, entry.cache_entry_id)
            try:
                self.table.update_item(
                    Key={"PK": pk, "SK": sk},
                    UpdateExpression="SET #status = :status",
                    ExpressionAttributeNames={"#status": "status"},
                    ExpressionAttributeValues={":status": "invalidated"},
                    ConditionExpression="attribute_exists(PK)",
                )
                count += 1
            except Exception:
                logger.warning(
                    "batch_invalidate.entry_failed",
                    cache_entry_id=entry.cache_entry_id,
                )
        return count

    # -----------------------------------------------------------------
    # Config operations (Phase 3)
    # -----------------------------------------------------------------

    def get_config(self, workspace_id: str, project_id: str) -> CacheConfigModel | None:
        """Get config for a project scope via direct GetItem."""
        pk = build_pk(self.application_id, self.client_id)
        sk = build_config_sk(workspace_id, project_id)

        response = self.table.get_item(Key={"PK": pk, "SK": sk})
        item = response.get("Item")
        if item is None:
            return None

        return CacheConfigModel(
            workspace_id=item.get("workspace_id", workspace_id),
            project_id=item.get("project_id", project_id),
            enabled=item.get("enabled", True),
            default_ttl_seconds=int(item.get("default_ttl_seconds", 86400)),
            semantic_ttl_seconds=int(item.get("semantic_ttl_seconds", 3600)),
            similarity_threshold=float(item.get("similarity_threshold", 0.92)),
            max_entry_size_bytes=int(item.get("max_entry_size_bytes", 102400)),
            event_driven_invalidation=item.get("event_driven_invalidation", True),
            invalidation_events=item.get("invalidation_events", []),
            updated_at=item.get("updated_at", ""),
            updated_by=item.get("updated_by"),
        )

    def put_config(self, config: CacheConfigModel) -> None:
        """Write a config entry to DynamoDB."""
        pk = build_pk(self.application_id, self.client_id)
        sk = build_config_sk(config.workspace_id, config.project_id)

        item: dict[str, Any] = {
            "PK": pk,
            "SK": sk,
            "workspace_id": config.workspace_id,
            "project_id": config.project_id,
            "enabled": config.enabled,
            "default_ttl_seconds": config.default_ttl_seconds,
            "semantic_ttl_seconds": config.semantic_ttl_seconds,
            "similarity_threshold": str(config.similarity_threshold),
            "max_entry_size_bytes": config.max_entry_size_bytes,
            "event_driven_invalidation": config.event_driven_invalidation,
            "invalidation_events": config.invalidation_events,
            "updated_at": config.updated_at,
            "updated_by": config.updated_by or "",
        }

        self.table.put_item(Item=item)

    # -----------------------------------------------------------------
    # Invalidation event recording (Phase 3)
    # -----------------------------------------------------------------

    def record_invalidation_event(self, event: InvalidationEventModel) -> None:
        """Write an invalidation event to DynamoDB for audit."""
        pk = build_pk(self.application_id, self.client_id)
        sk = build_invalidation_sk(event.created_at, event.event_id)

        item: dict[str, Any] = {
            "PK": pk,
            "SK": sk,
            "event_id": event.event_id,
            "workspace_id": event.workspace_id,
            "project_id": event.project_id,
            "source": event.source,
            "criteria": event.criteria,
            "entries_affected": event.entries_affected,
            "triggered_by": event.triggered_by,
            "created_at": event.created_at,
            "ttl": event.ttl,
        }

        self.table.put_item(Item=item)

    # -----------------------------------------------------------------
    # Citation link operations (Phase 3)
    # -----------------------------------------------------------------

    def put_citation_links(
        self,
        cache_entry_id: str,
        workspace_id: str,
        project_id: str,
        document_ids: list[str],
    ) -> None:
        """Write citation link items for GSI-Citation lookups."""
        pk = build_pk(self.application_id, self.client_id)
        for doc_id in document_ids:
            sk = build_citation_sk(doc_id, cache_entry_id)
            gsi3pk = build_gsi_citation_pk(self.application_id, self.client_id, doc_id)
            gsi3sk = f"CACHE#{cache_entry_id}"

            item: dict[str, Any] = {
                "PK": pk,
                "SK": sk,
                "GSI3PK": gsi3pk,
                "GSI3SK": gsi3sk,
                "cache_entry_id": cache_entry_id,
                "document_id": doc_id,
                "workspace_id": workspace_id,
                "project_id": project_id,
            }
            self.table.put_item(Item=item)

    def query_by_citation(self, document_id: str) -> list[str]:
        """Query GSI3 for cache entry IDs citing a document.

        GSI3 uses KEYS_ONLY projection, so we extract the cache_entry_id
        from GSI3SK (format: CACHE#{cache_entry_id}).
        """
        gsi3pk = build_gsi_citation_pk(self.application_id, self.client_id, document_id)

        response = self.table.query(
            IndexName="GSI3",
            KeyConditionExpression=Key("GSI3PK").eq(gsi3pk),
        )

        entry_ids: list[str] = []
        for item in response.get("Items", []):
            gsi3sk = item.get("GSI3SK", "")
            if gsi3sk.startswith("CACHE#"):
                entry_ids.append(gsi3sk[len("CACHE#") :])
        return entry_ids

    def delete_citation_links(self, cache_entry_id: str, document_ids: list[str]) -> None:
        """Delete citation link items when invalidating an entry."""
        pk = build_pk(self.application_id, self.client_id)
        for doc_id in document_ids:
            sk = build_citation_sk(doc_id, cache_entry_id)
            try:
                self.table.delete_item(Key={"PK": pk, "SK": sk})
            except Exception:
                logger.warning(
                    "delete_citation_link.failed",
                    cache_entry_id=cache_entry_id,
                    document_id=doc_id,
                )

    # -----------------------------------------------------------------
    # Item conversion
    # -----------------------------------------------------------------

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
            context_hash=item.get("context_hash"),
        )
