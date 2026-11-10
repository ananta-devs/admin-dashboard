import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_email_address(email: str) -> str:
    """Consistently trim whitespace and lowercase an email address."""
    return email.strip().lower()


class UserBase(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)


class UserCreate(UserBase):
    email: str = Field(min_length=3, max_length=255)

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", cleaned):
            raise ValueError("Invalid email format")
        return cleaned


class UserRead(BaseModel):
    id: uuid.UUID
    email: str
    normalized_email: str
    display_name: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
