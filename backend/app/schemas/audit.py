import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditEventRead(BaseModel):
    """Schema representing an administrative audit log event."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID | None = None
    organization_slug: str | None = None
    actor_user_id: uuid.UUID | None
    action: str
    target_type: str
    target_id: str
    occurred_at: datetime
    request_id: str | None = None
    sanitized_metadata: dict[str, Any] | None = None
