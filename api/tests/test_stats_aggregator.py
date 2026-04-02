"""Tests for the stats aggregator Lambda."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_table():
    table = MagicMock()
    table.query.return_value = {"Items": []}
    return table


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.application_id = "test-app"
    repo.client_id = "test-client"
    repo.query_by_project.return_value = ([], None)
    return repo


@pytest.fixture
def _patch_deps(mock_repo, mock_table):
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
    def test_empty_buckets_no_error(self, mock_repo, mock_table, _patch_deps):
        from src.stats_aggregator import handler

        result = handler({}, None)
        assert result["status"] == "ok"

    def test_aggregates_live_buckets(self, mock_repo, mock_table, _patch_deps):
        from src.stats_aggregator import handler

        mock_table.query.return_value = {
            "Items": [
                {
                    "exact_hits": 10,
                    "semantic_hits": 3,
                    "misses": 5,
                    "tokens_saved_input": 1000,
                    "tokens_saved_output": 500,
                    "workspace_id": "ws_01",
                    "project_id": "proj_01",
                    "bucket": "2026-04-01T14:00",
                },
                {
                    "exact_hits": 20,
                    "semantic_hits": 7,
                    "misses": 10,
                    "tokens_saved_input": 2000,
                    "tokens_saved_output": 1000,
                    "workspace_id": "ws_01",
                    "project_id": "proj_01",
                    "bucket": "2026-04-01T14:15",
                },
            ]
        }

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
