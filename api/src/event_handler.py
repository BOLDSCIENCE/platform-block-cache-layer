"""EventBridge event handler for cache invalidation.

Separate Lambda entry point (no FastAPI/Mangum) that processes
platform events for automatic cache invalidation.

Supported events:
- bold.doc-ingest / DocumentIngested
- bold.model-gateway / ModelVersionChanged
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import structlog
import ulid

from src.cache.models import InvalidationEventModel
from src.cache.repository import CacheRepository
from src.config import get_settings

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


def _get_opensearch_repo():
    """Build an OpenSearchRepository or None if not configured."""
    settings = get_settings()
    if not settings.opensearch_endpoint:
        return None

    from opensearchpy import OpenSearch, RequestsHttpConnection
    from requests_aws4auth import AWS4Auth

    credentials = boto3.Session().get_credentials()
    aws_auth = AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        settings.aws_region,
        "es",
        session_token=credentials.token,
    )

    client = OpenSearch(
        hosts=[{"host": settings.opensearch_endpoint, "port": 443}],
        http_auth=aws_auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
    )

    from src.cache.opensearch_repository import OpenSearchRepository

    return OpenSearchRepository(client)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Process EventBridge events for cache invalidation."""
    source = event.get("source", "")
    detail_type = event.get("detail-type", "")
    detail = event.get("detail", {})

    logger.info(
        "event_handler.received",
        source=source,
        detail_type=detail_type,
    )

    application_id = detail.get("application_id", "")
    client_id = detail.get("client_id", "")
    workspace_id = detail.get("workspace_id", "")
    project_id = detail.get("project_id", "")

    if not application_id or not client_id:
        logger.warning("event_handler.missing_tenant_fields", detail=detail)
        return {"status": "skipped", "reason": "missing_tenant_fields"}

    # Build repository
    settings = get_settings()
    dynamodb = boto3.resource(
        "dynamodb",
        region_name=settings.aws_region,
        endpoint_url=settings.dynamodb_endpoint_url,
    )
    table = dynamodb.Table(settings.dynamodb_table)
    repo = CacheRepository(table, application_id, client_id)
    opensearch_repo = _get_opensearch_repo()

    now = datetime.now(UTC)
    entries_affected = 0

    if source == "bold.doc-ingest" and detail_type == "DocumentIngested":
        document_id = detail.get("document_id", "")
        if not document_id:
            logger.warning("event_handler.missing_document_id")
            return {"status": "skipped", "reason": "missing_document_id"}

        # Find entries citing this document
        entry_ids = repo.query_by_citation(document_id)
        if entry_ids:
            # Hydrate and invalidate
            entries = []
            for eid in entry_ids:
                entry = repo.get_by_id(eid, workspace_id, project_id)
                if entry is not None:
                    entries.append(entry)

            entries_affected = repo.batch_invalidate(entries)

            # Best-effort OpenSearch cleanup
            if opensearch_repo is not None:
                for entry in entries:
                    try:
                        opensearch_repo.delete_entry(entry.cache_entry_id)
                    except Exception:
                        pass

    elif source == "bold.model-gateway" and detail_type == "ModelVersionChanged":
        # Purge all entries for the client scope
        if workspace_id:
            entries = repo.query_all_by_workspace(workspace_id)
        else:
            logger.warning("event_handler.model_change_no_workspace")
            return {"status": "skipped", "reason": "no_workspace_id"}

        entries_affected = repo.batch_invalidate(entries)

        # Best-effort OpenSearch cleanup
        if opensearch_repo is not None:
            try:
                opensearch_repo.delete_by_query(
                    application_id=application_id,
                    client_id=client_id,
                    workspace_id=workspace_id,
                )
            except Exception:
                pass

    else:
        logger.info("event_handler.unknown_event", source=source, detail_type=detail_type)
        return {"status": "skipped", "reason": "unknown_event"}

    # Record audit event
    audit = InvalidationEventModel(
        event_id=f"inv_{ulid.new().str}",
        workspace_id=workspace_id,
        project_id=project_id,
        source="event",
        criteria={"event_source": source, "detail_type": detail_type},
        entries_affected=entries_affected,
        triggered_by=f"{source}/{detail_type}",
        created_at=now.isoformat(),
        ttl=int((now + timedelta(days=90)).timestamp()),
    )
    try:
        repo.record_invalidation_event(audit)
    except Exception:
        logger.warning("event_handler.audit_failed", event_id=audit.event_id)

    logger.info(
        "event_handler.completed",
        entries_affected=entries_affected,
        source=source,
        detail_type=detail_type,
    )

    return {
        "status": "processed",
        "entries_affected": entries_affected,
        "event_source": source,
        "detail_type": detail_type,
    }
