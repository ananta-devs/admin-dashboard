import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PermissionRead(BaseModel):
    """Schema representing an application permission."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str
    category: str
    created_at: datetime
