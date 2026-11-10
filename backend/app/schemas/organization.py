import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


def validate_slug_format(v: str) -> str:
    cleaned = v.strip()
    if not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", cleaned):
        raise ValueError(
            "Slug must consist of lowercase alphanumeric characters and single hyphens"
        )
    return cleaned


class OrganizationBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class OrganizationCreate(OrganizationBase):
    slug: str = Field(min_length=2, max_length=100)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        return validate_slug_format(v)


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)


class OrganizationRead(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProvisionOrganizationInput(BaseModel):
    """Input payload for atomic organization, owner user, and default workspace creation."""

    organization_name: str = Field(min_length=1, max_length=255)
    organization_slug: str = Field(min_length=2, max_length=100)
    user_email: str = Field(min_length=3, max_length=255)
    user_display_name: str = Field(min_length=1, max_length=255)
    default_workspace_name: str = Field(default="Default Workspace", min_length=1, max_length=255)
    default_workspace_slug: str = Field(default="default", min_length=2, max_length=100)

    @field_validator("organization_slug", "default_workspace_slug")
    @classmethod
    def validate_slugs(cls, v: str) -> str:
        return validate_slug_format(v)
