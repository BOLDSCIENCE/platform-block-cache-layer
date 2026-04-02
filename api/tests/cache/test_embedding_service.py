"""Unit tests for EmbeddingService."""

import time
from unittest.mock import MagicMock

from src.cache.embedding_service import EmbeddingService, _circuit_breaker
from src.common.circuit_breaker import CircuitState


class TestEmbeddingService:
    def test_generate_embedding_success(self):
        """Successful embedding generation returns a list of floats."""
        mock_client = MagicMock()
        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1, 0.2, 0.3]
        mock_response = MagicMock()
        mock_response.data = [mock_embedding]
        mock_client.embed.return_value = mock_response

        service = EmbeddingService(mock_client)
        result = service.generate_embedding("hello world")

        assert result == [0.1, 0.2, 0.3]
        mock_client.embed.assert_called_once()

    def test_generate_embedding_failure_returns_none(self):
        """SDK failure returns None (doesn't raise)."""
        mock_client = MagicMock()
        mock_client.embed.side_effect = RuntimeError("network error")

        service = EmbeddingService(mock_client)
        result = service.generate_embedding("hello world")

        assert result is None

    def test_generate_embedding_circuit_open_returns_none(self):
        """Returns None when circuit breaker is open."""
        _circuit_breaker._state = CircuitState.OPEN
        _circuit_breaker._last_failure_time = time.monotonic()

        mock_client = MagicMock()
        service = EmbeddingService(mock_client)
        result = service.generate_embedding("hello world")

        assert result is None
        mock_client.embed.assert_not_called()
