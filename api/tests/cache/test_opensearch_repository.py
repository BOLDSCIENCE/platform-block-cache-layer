"""Unit tests for OpenSearchRepository."""

from unittest.mock import MagicMock

import pytest

from src.cache.opensearch_repository import OpenSearchRepository


@pytest.fixture
def mock_os_client():
    client = MagicMock()
    client.indices.exists.return_value = True
    return client


@pytest.fixture
def repo(mock_os_client):
    return OpenSearchRepository(mock_os_client)


class TestOpenSearchRepository:
    def test_ensure_index_creates_when_missing(self, mock_os_client):
        """Index is created when it doesn't exist."""
        mock_os_client.indices.exists.return_value = False
        repo = OpenSearchRepository(mock_os_client)

        repo._ensure_index()

        mock_os_client.indices.create.assert_called_once()
        assert repo._index_verified is True

    def test_ensure_index_skips_when_exists(self, mock_os_client):
        """Index creation is skipped when index already exists."""
        mock_os_client.indices.exists.return_value = True
        repo = OpenSearchRepository(mock_os_client)

        repo._ensure_index()

        mock_os_client.indices.create.assert_not_called()
        assert repo._index_verified is True

    def test_ensure_index_cached_after_first_check(self, repo, mock_os_client):
        """Second call to _ensure_index skips the exists check."""
        repo._ensure_index()
        mock_os_client.indices.exists.assert_called_once()

        # Second call should not re-check
        repo._ensure_index()
        mock_os_client.indices.exists.assert_called_once()

    def test_index_embedding_success(self, repo, mock_os_client):
        """Successful embedding indexing returns True."""
        result = repo.index_embedding(
            cache_entry_id="ce_123",
            query_embedding=[0.1, 0.2],
            query_normalized="test query",
            application_id="app1",
            client_id="client1",
            workspace_id="ws1",
            project_id="proj1",
            expires_at="2026-12-31T00:00:00+00:00",
            created_at="2026-01-01T00:00:00+00:00",
        )

        assert result is True
        mock_os_client.index.assert_called_once()

    def test_index_embedding_failure_returns_false(self, mock_os_client):
        """Indexing failure returns False."""
        mock_os_client.indices.exists.return_value = True
        mock_os_client.index.side_effect = RuntimeError("connection error")
        repo = OpenSearchRepository(mock_os_client)

        result = repo.index_embedding(
            cache_entry_id="ce_123",
            query_embedding=[0.1, 0.2],
            query_normalized="test query",
            application_id="app1",
            client_id="client1",
            workspace_id="ws1",
            project_id="proj1",
            expires_at="2026-12-31T00:00:00+00:00",
            created_at="2026-01-01T00:00:00+00:00",
        )

        assert result is False

    def test_search_similar_match(self, repo, mock_os_client):
        """Search returns a match when hits are found."""
        mock_os_client.search.return_value = {
            "hits": {
                "hits": [
                    {
                        "_score": 0.95,
                        "_source": {
                            "cache_entry_id": "ce_match",
                            "query_normalized": "matching query",
                        },
                    }
                ]
            }
        }

        result = repo.search_similar(
            query_embedding=[0.1, 0.2],
            application_id="app1",
            client_id="client1",
            workspace_id="ws1",
            project_id="proj1",
            threshold=0.90,
        )

        assert result is not None
        assert result["cache_entry_id"] == "ce_match"
        assert result["score"] == 0.95
        assert result["query_normalized"] == "matching query"

    def test_search_similar_no_hits(self, repo, mock_os_client):
        """Search returns None when no hits found."""
        mock_os_client.search.return_value = {"hits": {"hits": []}}

        result = repo.search_similar(
            query_embedding=[0.1, 0.2],
            application_id="app1",
            client_id="client1",
            workspace_id="ws1",
            project_id="proj1",
            threshold=0.90,
        )

        assert result is None

    def test_search_similar_failure_returns_none(self, mock_os_client):
        """Search failure returns None."""
        mock_os_client.indices.exists.return_value = True
        mock_os_client.search.side_effect = RuntimeError("timeout")
        repo = OpenSearchRepository(mock_os_client)

        result = repo.search_similar(
            query_embedding=[0.1, 0.2],
            application_id="app1",
            client_id="client1",
            workspace_id="ws1",
            project_id="proj1",
            threshold=0.90,
        )

        assert result is None

    def test_delete_entry_success(self, repo, mock_os_client):
        """Successful delete returns True."""
        result = repo.delete_entry("ce_123")
        assert result is True
        mock_os_client.delete.assert_called_once()
