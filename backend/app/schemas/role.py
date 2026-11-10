import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.permission import PermissionRead


class RoleRead(BaseModel):
    """Schema representing an organization role with assigned permissions."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    slug: str
    description: str | None
    is_system: bool
    created_at: datetime
    updated_at: datetime
    permissions: list[PermissionRead] = []


class RoleCreateInput(BaseModel):
    """Payload to create a custom organization role."""

    name: str = Field(min_length=2, max_length=100)
    slug: str = Field(min_length=2, max_length=100, pattern=r"^[a-z0-9-]+$")
    description: str | None = Field(default=None, max_length=500)
    permission_codes: list[str] = Field(min_length=1, max_length=50)


class RoleUpdateInput(BaseModel):
    """Payload to update an existing custom organization role."""

    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    permission_codes: list[str] | None = Field(default=None, min_length=1, max_length=50)


class MemberRolesUpdateInput(BaseModel):
    """Payload to assign a set of organization roles to a member."""

    role_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
