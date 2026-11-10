import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.role import RoleRead
from app.schemas.user import UserRead


class MemberRead(BaseModel):
    """Schema representing an organization membership with user details and roles."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    user: UserRead
    roles: list[RoleRead] = []


class MemberUpdateInput(BaseModel):
    """Schema for updating organization membership properties."""

    role_ids: list[uuid.UUID] | None = None
