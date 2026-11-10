from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_membership, verify_csrf_token
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership
from app.schemas.notification import (
    NotificationItem,
    NotificationMarkReadRequest,
    NotificationOperationResponse,
)
from app.services.notification import NotificationService

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get(
    "",
    response_model=list[NotificationItem],
    summary="Get user notifications with persisted PostgreSQL read/dismissed state",
)
async def list_notifications(
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> list[NotificationItem]:
    service = NotificationService(db)
    return await service.get_user_notifications(
        user_id=membership.user_id,
        organization_id=membership.organization_id,
    )


@router.post(
    "/mark-all-read",
    response_model=NotificationOperationResponse,
    summary="Mark all notifications as read in PostgreSQL",
    dependencies=[Depends(verify_csrf_token)],
)
async def mark_all_notifications_read(
    payload: NotificationMarkReadRequest | None = None,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> NotificationOperationResponse:
    service = NotificationService(db)
    count = await service.mark_all_read(
        user_id=membership.user_id,
        organization_id=membership.organization_id,
        notification_ids=payload.notification_ids if payload else None,
    )
    return NotificationOperationResponse(
        success=True,
        marked_count=count,
        is_read=True,
    )


@router.post(
    "/{notification_id}/read",
    response_model=NotificationOperationResponse,
    summary="Mark a single notification as read in PostgreSQL",
    dependencies=[Depends(verify_csrf_token)],
)
async def mark_single_notification_read(
    notification_id: str,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> NotificationOperationResponse:
    service = NotificationService(db)
    await service.mark_single_read(
        user_id=membership.user_id,
        organization_id=membership.organization_id,
        notification_id=notification_id,
    )
    return NotificationOperationResponse(
        success=True,
        notification_id=notification_id,
        is_read=True,
    )


@router.delete(
    "/{notification_id}",
    response_model=NotificationOperationResponse,
    summary="Dismiss a notification in PostgreSQL",
    dependencies=[Depends(verify_csrf_token)],
)
async def dismiss_notification(
    notification_id: str,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> NotificationOperationResponse:
    service = NotificationService(db)
    await service.dismiss_notification(
        user_id=membership.user_id,
        organization_id=membership.organization_id,
        notification_id=notification_id,
    )
    return NotificationOperationResponse(
        success=True,
        notification_id=notification_id,
        is_dismissed=True,
    )

