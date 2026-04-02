"""Tests for the EventBridge event handler Lambda."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.cache.models import CacheEntryModel
from src.event_handler import handler


def _make_entry(**overrides) -> CacheEntryModel:
    defaults = {
        "cache_entry_id": "ce_EV01",
        "application_id": "test-app",
        "client_id": "test-client",
        "workspace_id": "ws_01",
        "project_id": "proj_01",
        "query_normalized": "test query?",
        "query_hash": "hash123",
        "response": {"content": "answer"},
        "model": "test-model",
        "tokens_used": {},
        "citations": [],
        "hit_count": 0,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "active",
        "ttl": 9999999999,
    }
    defaults.update(overrides)
    return CacheEntryModel(**defaults)


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.application_id = "test-app"
    repo.client_id = "test-client"
    repo.query_by_citation.return_value = []
    repo.get_by_id.return_value = None
    repo.batch_invalidate.return_value = 0
    repo.query_all_by_workspace.return_value = []
    return repo


@pytest.fixture
def _patch_deps(mock_repo):
    """Patch dependencies used by the event handler."""
    mock_table = MagicMock()
    mock_dynamodb = MagicMock()
    mock_dynamodb.Table.return_value = mock_table

    with (
        patch("src.event_handler.boto3.resource", return_value=mock_dynamodb),
        patch("src.event_handler.CacheRepository", return_value=mock_repo),
        patch("src.event_handler._get_opensearch_repo", return_value=None),
    ):
        yield


class TestDocumentIngestedEvent:
    def test_invalidates_cited_entries(self, mock_repo, _patch_deps):
        entry = _make_entry(cache_entry_id="ce_CITED")
        mock_repo.query_by_citation.return_value = ["ce_CITED"]
        mock_repo.get_by_id.return_value = entry
        mock_repo.batch_invalidate.return_value = 1

        event = {
            "source": "bold.doc-ingest",
            "detail-type": "DocumentIngested",
            "detail": {
                "application_id": "test-app",
                "client_id": "test-client",
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "document_id": "doc_123",
            },
        }

        result = handler(event, None)

        assert result["status"] == "processed"
        assert result["entries_affected"] == 1
        mock_repo.query_by_citation.assert_called_once_with("doc_123")
        mock_repo.record_invalidation_event.assert_called_once()

    def test_missing_document_id_skipped(self, mock_repo, _patch_deps):
        event = {
            "source": "bold.doc-ingest",
            "detail-type": "DocumentIngested",
            "detail": {
                "application_id": "test-app",
                "client_id": "test-client",
                "workspace_id": "ws_01",
                "project_id": "proj_01",
            },
        }

        result = handler(event, None)
        assert result["status"] == "skipped"
        assert result["reason"] == "missing_document_id"


class TestModelVersionChangedEvent:
    def test_purges_workspace(self, mock_repo, _patch_deps):
        entries = [_make_entry(cache_entry_id="ce_W1")]
        mock_repo.query_all_by_workspace.return_value = entries
        mock_repo.batch_invalidate.return_value = 1

        event = {
            "source": "bold.model-gateway",
            "detail-type": "ModelVersionChanged",
            "detail": {
                "application_id": "test-app",
                "client_id": "test-client",
                "workspace_id": "ws_01",
                "project_id": "",
            },
        }

        result = handler(event, None)

        assert result["status"] == "processed"
        assert result["entries_affected"] == 1
        mock_repo.query_all_by_workspace.assert_called_once_with("ws_01")


class TestUnknownEvent:
    def test_unknown_event_skipped(self, mock_repo, _patch_deps):
        event = {
            "source": "bold.unknown",
            "detail-type": "SomethingHappened",
            "detail": {
                "application_id": "test-app",
                "client_id": "test-client",
            },
        }

        result = handler(event, None)
        assert result["status"] == "skipped"
        assert result["reason"] == "unknown_event"


class TestMissingFields:
    def test_missing_tenant_fields(self, _patch_deps):
        event = {
            "source": "bold.doc-ingest",
            "detail-type": "DocumentIngested",
            "detail": {"workspace_id": "ws_01"},
        }

        result = handler(event, None)
        assert result["status"] == "skipped"
        assert result["reason"] == "missing_tenant_fields"
