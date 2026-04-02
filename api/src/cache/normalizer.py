"""Query normalization and cache key construction.

Normalizes queries to increase exact match rates and builds
DynamoDB key structures per ARCHITECTURE.md Appendix A/B.
"""

import hashlib
import re


def normalize_query(query: str) -> str:
    """Normalize a query string for consistent hashing.

    1. Strip leading/trailing whitespace
    2. Lowercase
    3. Collapse multiple spaces
    4. Normalize trailing punctuation (? ! . → ?)
    """
    query = query.strip()
    query = query.lower()
    query = re.sub(r"\s+", " ", query)
    query = re.sub(r"[?!.]+$", "?", query)
    return query


def compute_query_hash(normalized_query: str) -> str:
    """Compute SHA-256 hex digest of a normalized query."""
    return hashlib.sha256(normalized_query.encode("utf-8")).hexdigest()


def build_pk(application_id: str, client_id: str) -> str:
    """Build the DynamoDB partition key.

    Format: APP#{application_id}#CLIENT#{client_id}
    """
    return f"APP#{application_id}#CLIENT#{client_id}"


def build_cache_sk(workspace_id: str, project_id: str, cache_entry_id: str) -> str:
    """Build the DynamoDB sort key for a cache entry.

    Format: CACHE#WS#{workspace_id}#PROJ#{project_id}#{cache_entry_id}
    """
    return f"CACHE#WS#{workspace_id}#PROJ#{project_id}#{cache_entry_id}"


def build_gsi_query_hash_pk(application_id: str, client_id: str, query_hash: str) -> str:
    """Build GSI1 (QueryHash) partition key for exact match lookup.

    Format: APP#{application_id}#CLIENT#{client_id}#HASH#{query_hash}
    """
    return f"APP#{application_id}#CLIENT#{client_id}#HASH#{query_hash}"


def build_gsi_project_entries_pk(
    application_id: str, client_id: str, workspace_id: str, project_id: str
) -> str:
    """Build GSI2 (ProjectEntries) partition key.

    Format: APP#{application_id}#CLIENT#{client_id}#WS#{workspace_id}#PROJ#{project_id}
    """
    return f"APP#{application_id}#CLIENT#{client_id}#WS#{workspace_id}#PROJ#{project_id}"


def build_config_sk(workspace_id: str, project_id: str) -> str:
    """Build the DynamoDB sort key for a config entry.

    Format: CONFIG#WS#{workspace_id}#PROJ#{project_id}
    """
    return f"CONFIG#WS#{workspace_id}#PROJ#{project_id}"


def build_invalidation_sk(timestamp: str, event_id: str) -> str:
    """Build the DynamoDB sort key for an invalidation event.

    Format: INVAL#{timestamp}#{event_id}
    """
    return f"INVAL#{timestamp}#{event_id}"


def build_gsi_citation_pk(application_id: str, client_id: str, document_id: str) -> str:
    """Build GSI3 (Citation) partition key.

    Format: APP#{application_id}#CLIENT#{client_id}#DOC#{document_id}
    """
    return f"APP#{application_id}#CLIENT#{client_id}#DOC#{document_id}"


def build_citation_sk(document_id: str, cache_entry_id: str) -> str:
    """Build the DynamoDB sort key for a citation link item.

    Format: CITE#DOC#{document_id}#CACHE#{cache_entry_id}
    """
    return f"CITE#DOC#{document_id}#CACHE#{cache_entry_id}"
