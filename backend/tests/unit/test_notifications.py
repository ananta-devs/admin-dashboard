import uuid

import pytest
from sqlalchemy import select

from app.models.membership import OrganizationMembership
from app.models.notification import UserNotificationState
from app.models.organization import Organization
from app.models.user import User
from app.services.notification import NotificationService


@pytest.mark.asyncio
async def test_notification_service_postgres_operations(db_session):
    # Create test organization & user to satisfy foreign key integrity
    org_id = uuid.uuid4()
    org = Organization(
        id=org_id,
        name="Test Org",
        slug=f"test-org-{uuid.uuid4().hex[:8]}",
    )
    db_session.add(org)

    user_id = uuid.uuid4()
    email_val = f"test-{uuid.uuid4().hex[:8]}@example.com"
    user = User(
        id=user_id,
        email=email_val,
        normalized_email=email_val.lower(),
        display_name="Test User",
    )
    db_session.add(user)

    membership = OrganizationMembership(
        id=uuid.uuid4(),
        organization_id=org_id,
        user_id=user_id,
    )
    db_session.add(membership)
    await db_session.commit()

    service = NotificationService(db_session)

    # 1. Mark single notification as read in PostgreSQL
    notif_id = "test-notif-1"
    await service.mark_single_read(
        user_id=user_id, organization_id=org_id, notification_id=notif_id
    )

    # Verify directly from database
    res = await db_session.execute(
        select(UserNotificationState).where(
            UserNotificationState.user_id == user_id,
            UserNotificationState.organization_id == org_id,
            UserNotificationState.notification_id == notif_id,
        )
    )
    state = res.scalar_one_or_none()
    assert state is not None
    assert state.is_read is True
    assert state.is_dismissed is False
    assert state.read_at is not None

    # 2. Mark multiple as read in PostgreSQL
    ids = ["test-notif-2", "test-notif-3"]
    marked = await service.mark_all_read(
        user_id=user_id, organization_id=org_id, notification_ids=ids
    )
    assert marked == 2

    # Verify multiple marked
    res_all = await db_session.execute(
        select(UserNotificationState).where(
            UserNotificationState.user_id == user_id,
            UserNotificationState.organization_id == org_id,
            UserNotificationState.notification_id.in_(ids),
        )
    )
    states_all = res_all.scalars().all()
    assert len(states_all) == 2
    for s in states_all:
        assert s.is_read is True

    # 3. Dismiss notification in PostgreSQL
    await service.dismiss_notification(
        user_id=user_id, organization_id=org_id, notification_id="test-notif-1"
    )
    db_session.expire_all()
    res_dismiss = await db_session.execute(
        select(UserNotificationState).where(
            UserNotificationState.user_id == user_id,
            UserNotificationState.organization_id == org_id,
            UserNotificationState.notification_id == notif_id,
        )
    )
    state_dismiss = res_dismiss.scalar_one_or_none()
    assert state_dismiss is not None
    assert state_dismiss.is_dismissed is True
    assert state_dismiss.dismissed_at is not None
