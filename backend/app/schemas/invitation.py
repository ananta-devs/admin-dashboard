import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from app.schemas.role import RoleRead
from app.schemas.user import UserRead


class InvitationRead(BaseModel):
    """Schema representing an organization invitation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    email: str
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None
    is_valid: bool
    role: RoleRead
    invited_by: UserRead


class InvitationCreateInput(BaseModel):
    """Payload to invite a member to the organization."""

    email: EmailStr
    role_id: uuid.UUID


class InvitationAcceptInput(BaseModel):
    """Payload to accept an invitation using the opaque action token."""

    token: str
