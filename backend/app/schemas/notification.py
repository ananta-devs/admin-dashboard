from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class NotificationItem(BaseModel):
    id: str
    title: str
    description: str
    time: str
    read: bool
    type: Literal["info", "success", "warning", "error"]


class NotificationMarkReadRequest(BaseModel):
    notification_ids: list[str] | None = Field(
        default=None,
        description="Optional list of notification IDs to mark as read. If omitted or empty, all current notifications are marked as read.",
    )


class NotificationOperationResponse(BaseModel):
    success: bool
    marked_count: int | None = None
    notification_id: str | None = None
    is_read: bool | None = None
    is_dismissed: bool | None = None

