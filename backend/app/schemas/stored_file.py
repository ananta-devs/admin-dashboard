"""Pydantic schemas for tenant-isolated file storage."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class StoredFileRead(BaseModel):
    """Authoritative representation of a stored file."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID | None = None
    uploaded_by_user_id: uuid.UUID | None = None
    filename: str
    content_type: str
    size_bytes: int
    storage_path: str
    checksum_sha256: str
    folder: str
    created_at: datetime
    updated_at: datetime


class StorageQuotaRead(BaseModel):
    """Storage quota metrics for active organization."""

    organization_id: uuid.UUID
    usage_bytes: int
    quota_bytes: int
    available_bytes: int
    file_count: int
    utilization_percent: float

