"""Embedding generation via Model Gateway SDK with circuit breaker protection."""

import structlog
from boldsci_model_gateway import GatewayClient

from src.common.circuit_breaker import CircuitBreaker
from src.config import get_settings

logger = structlog.get_logger()

_circuit_breaker = CircuitBreaker("model-gateway-embed")


class EmbeddingService:
    """Wraps the Model Gateway SDK to generate embeddings."""

    def __init__(self, client: GatewayClient):
        self._client = client

    def generate_embedding(self, text: str) -> list[float] | None:
        """Generate an embedding for the given text.

        Returns None on failure or when the circuit is open.
        """
        settings = get_settings()

        def _embed() -> list[float]:
            response = self._client.embed(
                model=settings.embedding_model,
                input=[text],
                dimensions=settings.embedding_dimensions,
            )
            return response.data[0].embedding

        result = _circuit_breaker.call(_embed)
        if result is None:
            logger.debug("embedding.unavailable", text_len=len(text))
        return result
