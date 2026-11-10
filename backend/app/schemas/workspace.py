import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.organization import validate_slug_format


class WorkspaceBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class WorkspaceCreate(WorkspaceBase):
    slug: str = Field(min_length=2, max_length=100)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        return validate_slug_format(v)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=2, max_length=100)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_slug_format(v)


class WorkspaceRead(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    slug: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkspaceDetailRead(WorkspaceRead):
    resource_counts: dict[str, int] = Field(default_factory=dict)
