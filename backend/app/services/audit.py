import re
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AdministrativeAuditEvent
from app.repositories.audit import AuditRepository

SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|token|secret|hash|credential|auth|cookie|csrf|recovery_code|key|api_key|bearer|authorization|session)",
    re.IGNORECASE,
)


def sanitize_audit_metadata(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Recursively mask sensitive keys in audit metadata before persisting."""
    if not data:
        return data

    sanitized: dict[str, Any] = {}
    for key, value in data.items():
        if SENSITIVE_KEY_PATTERN.search(key):
            sanitized[key] = "[REDACTED]"
        elif isinstance(value, dict):
            sanitized[key] = sanitize_audit_metadata(value)
        elif isinstance(value, list):
            sanitized[key] = [
                sanitize_audit_metadata(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            sanitized[key] = value
    return sanitized


class AuditService:
    """Service recording and querying append-only administrative audit events."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.audit_repo = AuditRepository(session)

    async def record_event(
        self,
        organization_id: uuid.UUID,
        actor_user_id: uuid.UUID | None,
        action: str,
        target_type: str,
        target_id: str,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        organization_slug: str | None = None,
    ) -> AdministrativeAuditEvent:
        """Record an append-only administrative audit event with sanitized metadata."""
        cleaned_metadata = sanitize_audit_metadata(metadata)
        if organization_slug is None and organization_id:
            from app.models.organization import Organization

            org = await self.session.get(Organization, organization_id)
            if org:
                organization_slug = org.slug

        event = AdministrativeAuditEvent(
            organization_id=organization_id,
            organization_slug=organization_slug,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            request_id=request_id,
            sanitized_metadata=cleaned_metadata,
        )
        return await self.audit_repo.create(event)

    async def list_events(
        self, organization_id: uuid.UUID, limit: int = 50, offset: int = 0
    ) -> Sequence[AdministrativeAuditEvent]:
        """Fetch audit events for an organization."""
        return await self.audit_repo.list_by_organization(
            organization_id, limit=limit, offset=offset
        )

    async def count_events(self, organization_id: uuid.UUID) -> int:
        """Count total audit events for an organization."""
        return await self.audit_repo.count_by_organization(organization_id)
