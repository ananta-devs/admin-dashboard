import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class MembershipCreate(BaseModel):
    organization_id: uuid.UUID
    user_id: uuid.UUID


class MembershipRead(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
