"""MCP server configuration from environment variables."""

from __future__ import annotations

import os


def get_api_url() -> str:
    """Get Cache Layer API URL from environment."""
    url = os.environ.get("CACHE_LAYER_API_URL", "")
    if not url:
        raise RuntimeError("CACHE_LAYER_API_URL environment variable is required")
    return url


def get_api_key() -> str:
    """Get Cache Layer API key from environment."""
    key = os.environ.get("CACHE_LAYER_API_KEY", "")
    if not key:
        raise RuntimeError("CACHE_LAYER_API_KEY environment variable is required")
    return key
