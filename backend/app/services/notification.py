from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AdministrativeAuditEvent
from app.models.notification import UserNotificationState
from app.schemas.notification import NotificationItem
from app.services.system_health import SystemHealthService


def format_action_title(action: str) -> str:
    words = action.replace(".", " ").replace("_", " ").split()
    return " ".join(word.capitalize() for word in words)


def format_relative_time(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff_sec = int((now - dt).total_seconds())

    if diff_sec < 60:
        return "Just now"
    if diff_sec < 3600:
        mins = diff_sec // 60
        return f"{mins}m ago"
    if diff_sec < 86400:
        hours = diff_sec // 3600
        return f"{hours}h ago"
    days = diff_sec // 86400
    return f"{days}d ago"


def format_action_description(event: AdministrativeAuditEvent) -> str:
    target = f"{event.target_type} ({event.target_id[:8]})" if event.target_id else event.target_type
    return f"Administrative operation on {target} recorded in ledger."


class NotificationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_user_notifications(
        self,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> list[NotificationItem]:
        # 1. Fetch recent audit logs for the organization
        audit_stmt = (
            select(AdministrativeAuditEvent)
            .where(AdministrativeAuditEvent.organization_id == organization_id)
            .order_by(AdministrativeAuditEvent.occurred_at.desc())
            .limit(20)
        )
        audit_res = await self.db.execute(audit_stmt)
        events = audit_res.scalars().all()

        # 2. Fetch existing user notification states
        states_stmt = select(UserNotificationState).where(
            UserNotificationState.user_id == user_id,
            UserNotificationState.organization_id == organization_id,
        )
        states_res = await self.db.execute(states_stmt)
        states = states_res.scalars().all()
        state_map = {s.notification_id: s for s in states}

        items: list[NotificationItem] = []

        # 3. Process audit events
        for ev in events:
            notif_id = f"audit-{ev.id}"
            state = state_map.get(notif_id)
            if state and state.is_dismissed:
                continue

            is_warn = any(k in ev.action for k in ("failed", "alert", "breach"))
            is_success = any(k in ev.action for k in ("created", "assigned", "completed"))
            item_type: Any = "warning" if is_warn else ("success" if is_success else "info")

            items.push if False else None  # type guard
            items.append(
                NotificationItem(
                    id=notif_id,
                    title=format_action_title(ev.action),
                    description=format_action_description(ev),
                    time=format_relative_time(ev.occurred_at),
                    read=state.is_read if state else False,
                    type=item_type,
                )
            )

        # 4. Process system telemetry notification
        try:
            health_svc = SystemHealthService(self.db)
            telemetry = await health_svc.get_operational_telemetry(organization_id)
            subsystems = telemetry.get("subsystems", {})
            is_healthy = telemetry.get("overall_status") == "Healthy"

            # Core health item
            sys_id = "sys-core-health"
            sys_state = state_map.get(sys_id)
            if not (sys_state and sys_state.is_dismissed):
                db_sub = subsystems.get("database", {})
                redis_sub = subsystems.get("redis", {})
                storage_sub = subsystems.get("storage", {})

                db_ms = db_sub.get("latency_ms")
                redis_ms = redis_sub.get("latency_ms")
                free_gb = storage_sub.get("host_free_disk_gb")

                db_str = f"PostgreSQL ({db_ms:.1f}ms)" if db_ms is not None else "Database"
                redis_str = f"Redis ({redis_ms:.1f}ms)" if redis_ms is not None else "Cache"
                free_str = f" • {free_gb:.0f} GB free" if free_gb is not None else ""

                items.append(
                    NotificationItem(
                        id=sys_id,
                        title="Subsystems Operational" if is_healthy else "System Core Alert",
                        description=f"{db_str}, {redis_str} online{free_str}.",
                        time="Live",
                        read=sys_state.is_read if sys_state else False,
                        type="success" if is_healthy else "warning",
                    )
                )

            # Disaster recovery item
            dr = subsystems.get("backup_recovery")
            if dr:
                dr_id = "sys-dr-status"
                dr_state = state_map.get(dr_id)
                if not (dr_state and dr_state.is_dismissed):
                    rpo = dr.get("rpo") or {}
                    sla = rpo.get("sla_compliant", True)
                    target_min = rpo.get("target_minutes", 15)
                    items.append(
                        NotificationItem(
                            id=dr_id,
                            title="Disaster Recovery SLA Active" if sla else "Disaster Recovery Warning",
                            description=(
                                f"Continuous WAL replication verified within RPO SLA (< {target_min}m target)."
                                if sla
                                else "Continuous disaster recovery replication requires attention."
                            ),
                            time="Live",
                            read=dr_state.is_read if dr_state else False,
                            type="success" if sla else "warning",
                        )
                    )
        except Exception:
            pass

        return items

    async def mark_all_read(
        self,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        notification_ids: list[str] | None = None,
    ) -> int:
        now = datetime.now(timezone.utc)
        ids_to_mark = notification_ids

        if not ids_to_mark:
            # Gather all current active notification IDs
            active_items = await self.get_user_notifications(user_id, organization_id)
            ids_to_mark = [item.id for item in active_items]

        if not ids_to_mark:
            return 0

        for nid in ids_to_mark:
            stmt = (
                pg_insert(UserNotificationState)
                .values(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    organization_id=organization_id,
                    notification_id=nid,
                    is_read=True,
                    is_dismissed=False,
                    read_at=now,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_user_notif_state_user_org_notif",
                    set_={
                        "is_read": True,
                        "read_at": now,
                        "updated_at": now,
                    },
                )
            )
            await self.db.execute(stmt)

        await self.db.commit()
        return len(ids_to_mark)

    async def mark_single_read(
        self,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        notification_id: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        stmt = (
            pg_insert(UserNotificationState)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                organization_id=organization_id,
                notification_id=notification_id,
                is_read=True,
                is_dismissed=False,
                read_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_user_notif_state_user_org_notif",
                set_={
                    "is_read": True,
                    "read_at": now,
                    "updated_at": now,
                },
            )
        )
        await self.db.execute(stmt)
        await self.db.commit()

    async def dismiss_notification(
        self,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        notification_id: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        stmt = (
            pg_insert(UserNotificationState)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                organization_id=organization_id,
                notification_id=notification_id,
                is_read=True,
                is_dismissed=True,
                dismissed_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_user_notif_state_user_org_notif",
                set_={
                    "is_dismissed": True,
                    "dismissed_at": now,
                    "updated_at": now,
                },
            )
        )
        await self.db.execute(stmt)
        await self.db.commit()

