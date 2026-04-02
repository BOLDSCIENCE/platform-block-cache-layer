"""OpenSearch operations for semantic similarity search."""

from datetime import UTC, datetime
from typing import Any

import structlog
from opensearchpy import OpenSearch

from src.common.circuit_breaker import CircuitBreaker
from src.config import get_settings

logger = structlog.get_logger()

_circuit_breaker = CircuitBreaker("opensearch")


class OpenSearchRepository:
    """OpenSearch repository for embedding-based similarity search."""

    def __init__(self, client: OpenSearch):
        self._client = client
        self._index_verified = False

    def _ensure_index(self) -> None:
        """Create the semantic cache index if it doesn't exist."""
        if self._index_verified:
            return

        settings = get_settings()
        index_name = settings.opensearch_index

        if self._client.indices.exists(index=index_name):
            self._index_verified = True
            return

        body = {
            "settings": {
                "index": {
                    "knn": True,
                    "knn.algo_param.ef_search": 100,
                }
            },
            "mappings": {
                "properties": {
                    "query_embedding": {
                        "type": "knn_vector",
                        "dimension": settings.embedding_dimensions,
                        "method": {
                            "name": "hnsw",
                            "space_type": "cosinesimil",
                            "engine": "nmslib",
                            "parameters": {"ef_construction": 128, "m": 24},
                        },
                    },
                    "application_id": {"type": "keyword"},
                    "client_id": {"type": "keyword"},
                    "workspace_id": {"type": "keyword"},
                    "project_id": {"type": "keyword"},
                    "query_normalized": {"type": "text"},
                    "cache_entry_id": {"type": "keyword"},
                    "status": {"type": "keyword"},
                    "expires_at": {"type": "date"},
                    "created_at": {"type": "date"},
                }
            },
        }

        self._client.indices.create(index=index_name, body=body)
        self._index_verified = True
        logger.info("opensearch.index_created", index=index_name)

    def index_embedding(
        self,
        cache_entry_id: str,
        query_embedding: list[float],
        query_normalized: str,
        application_id: str,
        client_id: str,
        workspace_id: str,
        project_id: str,
        expires_at: str,
        created_at: str,
    ) -> bool:
        """Index a cache entry embedding in OpenSearch.

        Returns True on success, False on failure.
        """
        settings = get_settings()

        def _index() -> bool:
            self._ensure_index()
            doc = {
                "query_embedding": query_embedding,
                "query_normalized": query_normalized,
                "application_id": application_id,
                "client_id": client_id,
                "workspace_id": workspace_id,
                "project_id": project_id,
                "cache_entry_id": cache_entry_id,
                "status": "active",
                "expires_at": expires_at,
                "created_at": created_at,
            }
            self._client.index(
                index=settings.opensearch_index,
                id=cache_entry_id,
                body=doc,
            )
            return True

        result = _circuit_breaker.call(_index)
        return result is True

    def search_similar(
        self,
        query_embedding: list[float],
        application_id: str,
        client_id: str,
        workspace_id: str,
        project_id: str,
        threshold: float,
    ) -> dict[str, Any] | None:
        """Search for a semantically similar cache entry.

        Returns {cache_entry_id, score, query_normalized} or None.
        """
        settings = get_settings()
        now_iso = datetime.now(UTC).isoformat()

        def _search() -> dict[str, Any] | None:
            self._ensure_index()
            query = {
                "size": 1,
                "min_score": threshold,
                "query": {
                    "bool": {
                        "must": [
                            {
                                "knn": {
                                    "query_embedding": {
                                        "vector": query_embedding,
                                        "k": 5,
                                    }
                                }
                            }
                        ],
                        "filter": [
                            {"term": {"application_id": application_id}},
                            {"term": {"client_id": client_id}},
                            {"term": {"workspace_id": workspace_id}},
                            {"term": {"project_id": project_id}},
                            {"term": {"status": "active"}},
                            {"range": {"expires_at": {"gte": now_iso}}},
                        ],
                    }
                },
            }

            response = self._client.search(
                index=settings.opensearch_index,
                body=query,
            )

            hits = response.get("hits", {}).get("hits", [])
            if not hits:
                return None

            best = hits[0]
            return {
                "cache_entry_id": best["_source"]["cache_entry_id"],
                "score": best["_score"],
                "query_normalized": best["_source"]["query_normalized"],
            }

        return _circuit_breaker.call(_search)

    def delete_by_query(
        self,
        application_id: str,
        client_id: str,
        workspace_id: str,
        project_id: str | None = None,
    ) -> int:
        """Delete all OpenSearch entries matching the scope. Returns count deleted."""
        settings = get_settings()

        def _delete() -> int:
            self._ensure_index()
            filters = [
                {"term": {"application_id": application_id}},
                {"term": {"client_id": client_id}},
                {"term": {"workspace_id": workspace_id}},
            ]
            if project_id is not None:
                filters.append({"term": {"project_id": project_id}})

            body = {"query": {"bool": {"filter": filters}}}
            response = self._client.delete_by_query(
                index=settings.opensearch_index,
                body=body,
            )
            return response.get("deleted", 0)

        result = _circuit_breaker.call(_delete)
        return result if result is not None else 0

    def delete_entry(self, cache_entry_id: str) -> bool:
        """Delete a document from OpenSearch by cache entry ID.

        Returns True on success, False on failure.
        """
        settings = get_settings()

        def _delete() -> bool:
            self._client.delete(
                index=settings.opensearch_index,
                id=cache_entry_id,
                ignore=[404],
            )
            return True

        result = _circuit_breaker.call(_delete)
        return result is True
