"""Application configuration using pydantic-settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "cache-layer-api"
    allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    dynamodb_table: str = "bold-cache-layer"
    dynamodb_endpoint_url: str | None = None
    aws_region: str = "us-east-1"
    log_level: str = "INFO"
    environment: str = "development"
    api_version: str = "v1"

    # Semantic similarity settings (Phase 2)
    opensearch_endpoint: str | None = None
    opensearch_index: str = "bold-semantic-cache"
    model_gateway_api_url: str | None = None
    embedding_model: str = "amazon.titan-embed-text-v2:0"
    embedding_dimensions: int = 1024
    default_similarity_threshold: float = 0.92

    # Stats aggregator (Phase 4)
    application_id: str = ""
    client_id: str = ""


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
