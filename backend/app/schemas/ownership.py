import uuid

from pydantic import BaseModel, Field


class OwnershipTransferInput(BaseModel):
    """Payload to transfer organization ownership to another active member."""

    target_membership_id: uuid.UUID
    confirmation: str = Field(min_length=1, max_length=50)
