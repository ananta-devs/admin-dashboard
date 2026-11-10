"""Service layer for analytical dashboards, widget layouts, and Polars aggregation queries."""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.storage import AnalyticsStorageService
from app.core.exceptions import ConflictError, EntityNotFoundError, ValidationError
from app.db.base import utc_now
from app.db.transaction import transaction
from app.formula import col_to_letter
from app.models.dashboard import Dashboard, DashboardWidget
from app.repositories.analytics import AnalyticsResultRepository
from app.repositories.dashboard import DashboardRepository, DashboardWidgetRepository
from app.repositories.spreadsheet import SheetRepository, SheetRowRepository
from app.repositories.workspace import WorkspaceRepository
from app.schemas.dashboard import (
    DashboardCreateInput,
    DashboardUpdateInput,
    WidgetCreateInput,
    WidgetDataQueryResponse,
    WidgetLayoutUpdateInput,
    WidgetUpdateInput,
)
from app.services.sync import _cell_display_value


def next_updated_at(previous_updated_at: datetime | None = None) -> datetime:
    """Calculate strictly increasing UTC timestamp respecting microsecond precision.

    Enforces new_updated_at > previous_updated_at by taking the greater of:
    - current UTC time (utc_now())
    - previous_updated_at + 1 microsecond (smallest quantum in PostgreSQL TIMESTAMPTZ)
    """
    now = utc_now()
    if previous_updated_at is not None:
        prev = (
            previous_updated_at.replace(tzinfo=UTC)
            if previous_updated_at.tzinfo is None
            else previous_updated_at.astimezone(UTC)
        )
        if now <= prev:
            return prev + timedelta(microseconds=1)
    return now


class DashboardService:
    """Manages dashboard lifecycle, widget configurations, and server-side Polars aggregation."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.dashboard_repo = DashboardRepository(session)
        self.widget_repo = DashboardWidgetRepository(session)
        self.workspace_repo = WorkspaceRepository(session)
        self.sheet_repo = SheetRepository(session)
        self.row_repo = SheetRowRepository(session)
        self.analytics_repo = AnalyticsResultRepository(session)
        self.storage = AnalyticsStorageService()

    # =========================================================================
    # Dashboards
    # =========================================================================

    async def create_dashboard(
        self,
        input_data: DashboardCreateInput,
        organization_id: uuid.UUID,
        created_by_user_id: uuid.UUID | None = None,
    ) -> Dashboard:
        ws = await self.workspace_repo.get_by_id(input_data.workspace_id, organization_id)
        if not ws:
            raise EntityNotFoundError("Workspace", str(input_data.workspace_id))

        async with transaction(self.session):
            dashboard = Dashboard(
                organization_id=organization_id,
                workspace_id=input_data.workspace_id,
                created_by_user_id=created_by_user_id,
                title=input_data.title,
                description=input_data.description,
                layout_config=input_data.layout_config,
                is_shared=input_data.is_shared,
            )
            created = await self.dashboard_repo.create(dashboard)
            dash_id = created.id
        return await self.get_dashboard(dash_id, organization_id)

    async def get_dashboard(
        self, dashboard_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Dashboard:
        dashboard = await self.dashboard_repo.get_by_id(dashboard_id, organization_id)
        if not dashboard:
            raise EntityNotFoundError("Dashboard", str(dashboard_id))
        return dashboard

    async def list_dashboards(
        self, organization_id: uuid.UUID, workspace_id: uuid.UUID | None = None
    ) -> Sequence[Dashboard]:
        return await self.dashboard_repo.list_by_organization(
            organization_id, workspace_id=workspace_id
        )

    async def update_dashboard(
        self,
        dashboard_id: uuid.UUID,
        organization_id: uuid.UUID,
        input_data: DashboardUpdateInput,
    ) -> Dashboard:
        async with transaction(self.session):
            dashboard = await self.dashboard_repo.get_by_id_for_update(
                dashboard_id, organization_id
            )
            if not dashboard:
                raise EntityNotFoundError("Dashboard", str(dashboard_id))

            if input_data.expected_updated_at is not None:
                expected = input_data.expected_updated_at
                actual = dashboard.updated_at

                if expected.tzinfo is None:
                    expected = expected.replace(tzinfo=UTC)
                else:
                    expected = expected.astimezone(UTC)

                if actual.tzinfo is None:
                    actual = actual.replace(tzinfo=UTC)
                else:
                    actual = actual.astimezone(UTC)

                if actual != expected:
                    raise ConflictError("Dashboard has been modified by another user.")

            if input_data.title is not None:
                dashboard.title = input_data.title
            if input_data.description is not None:
                dashboard.description = input_data.description
            if input_data.layout_config is not None:
                dashboard.layout_config = input_data.layout_config
            if input_data.is_shared is not None:
                dashboard.is_shared = input_data.is_shared

            dashboard.updated_at = next_updated_at(dashboard.updated_at)
            await self.dashboard_repo.update(dashboard)

        return await self.get_dashboard(dashboard_id, organization_id)

    async def share_dashboard(
        self, dashboard_id: uuid.UUID, organization_id: uuid.UUID, is_shared: bool
    ) -> Dashboard:
        async with transaction(self.session):
            dashboard = await self.dashboard_repo.get_by_id_for_update(
                dashboard_id, organization_id
            )
            if not dashboard:
                raise EntityNotFoundError("Dashboard", str(dashboard_id))
            dashboard.is_shared = is_shared
            dashboard.updated_at = next_updated_at(dashboard.updated_at)
            await self.dashboard_repo.update(dashboard)
        return await self.get_dashboard(dashboard_id, organization_id)

    async def delete_dashboard(
        self, dashboard_id: uuid.UUID, organization_id: uuid.UUID
    ) -> None:
        async with transaction(self.session):
            deleted = await self.dashboard_repo.delete(dashboard_id, organization_id)
            if not deleted:
                raise EntityNotFoundError("Dashboard", str(dashboard_id))

    async def duplicate_dashboard(
        self,
        dashboard_id: uuid.UUID,
        organization_id: uuid.UUID,
        created_by_user_id: uuid.UUID | None = None,
        new_title: str | None = None,
    ) -> Dashboard:
        """Atomically duplicates an existing dashboard and all associated child widgets."""
        import copy

        source_dash = await self.get_dashboard(dashboard_id, organization_id)
        source_widgets = await self.widget_repo.list_by_dashboard(dashboard_id, organization_id)
        title = new_title.strip() if new_title and new_title.strip() else f"{source_dash.title} (Copy)"

        async with transaction(self.session):
            cloned_layout = copy.deepcopy(source_dash.layout_config or {})
            new_dash = Dashboard(
                organization_id=organization_id,
                workspace_id=source_dash.workspace_id,
                created_by_user_id=created_by_user_id,
                title=title,
                description=source_dash.description,
                layout_config=cloned_layout,
                is_shared=source_dash.is_shared,
            )
            created_dash = await self.dashboard_repo.create(new_dash)
            new_dash_id = created_dash.id

            for w in source_widgets:
                cloned_query_cfg = copy.deepcopy(w.query_config or {})
                new_widget = DashboardWidget(
                    dashboard=new_dash,
                    dashboard_id=new_dash_id,
                    organization_id=organization_id,
                    title=w.title,
                    widget_type=w.widget_type,
                    source_type=w.source_type,
                    source_id=w.source_id,
                    query_config=cloned_query_cfg,
                    position_x=w.position_x,
                    position_y=w.position_y,
                    width=w.width,
                    height=w.height,
                    sort_order=w.sort_order,
                )
                await self.widget_repo.create(new_widget)

            await self.session.flush()
            self.session.expire(new_dash)

        return await self.get_dashboard(new_dash_id, organization_id)

    # =========================================================================
    # Widgets
    # =========================================================================

    async def create_widget(
        self,
        dashboard_id: uuid.UUID,
        organization_id: uuid.UUID,
        input_data: WidgetCreateInput,
    ) -> DashboardWidget:
        dashboard = await self.get_dashboard(dashboard_id, organization_id)

        # Validate source exists in organization
        if input_data.source_type == "sheet":
            sheet = await self.sheet_repo.get_by_id(input_data.source_id, organization_id)
            if not sheet:
                raise EntityNotFoundError("Sheet", str(input_data.source_id))
        elif input_data.source_type == "analytics_result":
            result = await self.analytics_repo.get_by_id_scoped(input_data.source_id, organization_id)
            if not result:
                raise EntityNotFoundError("AnalyticsResult", str(input_data.source_id))
        else:
            raise ValidationError("source_type must be 'sheet' or 'analytics_result'.")

        async with transaction(self.session):
            widget = DashboardWidget(
                dashboard_id=dashboard.id,
                organization_id=organization_id,
                title=input_data.title,
                widget_type=input_data.widget_type,
                source_type=input_data.source_type,
                source_id=input_data.source_id,
                query_config=input_data.query_config,
                position_x=input_data.position_x,
                position_y=input_data.position_y,
                width=input_data.width,
                height=input_data.height,
                sort_order=input_data.sort_order,
            )
            created = await self.widget_repo.create(widget)
        return created

    async def get_widget(
        self, widget_id: uuid.UUID, organization_id: uuid.UUID
    ) -> DashboardWidget:
        widget = await self.widget_repo.get_by_id(widget_id, organization_id)
        if not widget:
            raise EntityNotFoundError("DashboardWidget", str(widget_id))
        return widget

    async def list_widgets(
        self, dashboard_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Sequence[DashboardWidget]:
        await self.get_dashboard(dashboard_id, organization_id)
        return await self.widget_repo.list_by_dashboard(dashboard_id, organization_id)

    async def update_widget(
        self,
        widget_id: uuid.UUID,
        organization_id: uuid.UUID,
        input_data: WidgetUpdateInput,
        dashboard_id: uuid.UUID | None = None,
    ) -> DashboardWidget:
        widget = await self.get_widget(widget_id, organization_id)
        if dashboard_id is not None and widget.dashboard_id != dashboard_id:
            raise EntityNotFoundError("DashboardWidget", str(widget_id))

        # DEF-03: Validate resulting source configuration belongs to organization
        if input_data.source_type is not None or input_data.source_id is not None:
            target_source_type = (
                input_data.source_type
                if input_data.source_type is not None
                else widget.source_type
            )
            target_source_id = (
                input_data.source_id
                if input_data.source_id is not None
                else widget.source_id
            )
            if target_source_type == "sheet":
                sheet = await self.sheet_repo.get_by_id(target_source_id, organization_id)
                if not sheet:
                    raise EntityNotFoundError("Sheet", str(target_source_id))
            elif target_source_type == "analytics_result":
                result = await self.analytics_repo.get_by_id_scoped(target_source_id, organization_id)
                if not result:
                    raise EntityNotFoundError("AnalyticsResult", str(target_source_id))
            else:
                raise ValidationError("source_type must be 'sheet' or 'analytics_result'.")

        async with transaction(self.session):
            if input_data.title is not None:
                widget.title = input_data.title
            if input_data.widget_type is not None:
                widget.widget_type = input_data.widget_type
            if input_data.source_type is not None:
                widget.source_type = input_data.source_type
            if input_data.source_id is not None:
                widget.source_id = input_data.source_id
            if input_data.query_config is not None:
                widget.query_config = input_data.query_config
            if input_data.position_x is not None:
                widget.position_x = input_data.position_x
            if input_data.position_y is not None:
                widget.position_y = input_data.position_y
            if input_data.width is not None:
                widget.width = input_data.width
            if input_data.height is not None:
                widget.height = input_data.height
            if input_data.sort_order is not None:
                widget.sort_order = input_data.sort_order
            await self.widget_repo.update(widget)
        return widget

    async def delete_widget(
        self,
        widget_id: uuid.UUID,
        organization_id: uuid.UUID,
        dashboard_id: uuid.UUID | None = None,
    ) -> None:
        if dashboard_id is not None:
            widget = await self.get_widget(widget_id, organization_id)
            if widget.dashboard_id != dashboard_id:
                raise EntityNotFoundError("DashboardWidget", str(widget_id))
        async with transaction(self.session):
            deleted = await self.widget_repo.delete(widget_id, organization_id)
            if not deleted:
                raise EntityNotFoundError("DashboardWidget", str(widget_id))

    async def batch_update_layout(
        self,
        dashboard_id: uuid.UUID,
        organization_id: uuid.UUID,
        input_data: WidgetLayoutUpdateInput,
    ) -> list[DashboardWidget]:
        async with transaction(self.session):
            dashboard = await self.dashboard_repo.get_by_id_for_update(
                dashboard_id, organization_id
            )
            if not dashboard:
                raise EntityNotFoundError("Dashboard", str(dashboard_id))

            updated_widgets: list[DashboardWidget] = []
            for pos in input_data.positions:
                widget = await self.widget_repo.get_by_id(pos.widget_id, organization_id)
                if widget and widget.dashboard_id == dashboard_id:
                    widget.position_x = pos.position_x
                    widget.position_y = pos.position_y
                    widget.width = pos.width
                    widget.height = pos.height
                    widget.sort_order = pos.sort_order
                    await self.widget_repo.update(widget)
                    updated_widgets.append(widget)

            dashboard.updated_at = next_updated_at(dashboard.updated_at)
            await self.dashboard_repo.update(dashboard)

        return updated_widgets

    # =========================================================================
    # Polars-Powered Analytical Query Engine
    # =========================================================================

    async def query_widget_data(
        self,
        widget_id: uuid.UUID,
        organization_id: uuid.UUID,
        custom_config: dict[str, Any] | None = None,
        dashboard_id: uuid.UUID | None = None,
    ) -> WidgetDataQueryResponse:
        """Executes server-side Polars aggregation against sheet or analytics result data."""
        widget = await self.get_widget(widget_id, organization_id)
        if dashboard_id is not None and widget.dashboard_id != dashboard_id:
            raise EntityNotFoundError("DashboardWidget", str(widget_id))

        config = custom_config or widget.query_config or {}

        df: pl.DataFrame

        if widget.source_type == "sheet":
            # DEF-03: Independently re-validate sheet belongs to authenticated organization
            sheet = await self.sheet_repo.get_by_id(widget.source_id, organization_id)
            if not sheet:
                raise EntityNotFoundError("Sheet", str(widget.source_id))

            # 1. Load sheet data
            rows = await self.row_repo.list_rows_paginated(widget.source_id, limit=5000, offset=0)
            if not rows:
                return WidgetDataQueryResponse(columns=[], data=[], total_rows=0)

            # Determine column names from header row or default
            header_row_idx = int(config.get("header_row", 0))
            max_c_idx = 0
            raw_matrix: dict[int, dict[int, Any]] = {}

            for r in rows:
                r_cells: dict[int, Any] = {}
                for col_letter, c_dict in (r.cells or {}).items():
                    from app.formula import letter_to_col

                    col_num = letter_to_col(col_letter)
                    if col_num > max_c_idx:
                        max_c_idx = col_num
                    r_cells[col_num] = _cell_display_value(c_dict)
                raw_matrix[r.row_index] = r_cells

            # Headers
            headers: list[str] = []
            if header_row_idx in raw_matrix:
                for c in range(max_c_idx + 1):
                    val = str(raw_matrix[header_row_idx].get(c, "")).strip()
                    headers.append(val if val else col_to_letter(c))
            else:
                headers = [col_to_letter(c) for c in range(max_c_idx + 1)]

            # Data rows
            records: list[dict[str, Any]] = []
            for r_idx in sorted(raw_matrix.keys()):
                if r_idx == header_row_idx:
                    continue
                row_dict: dict[str, Any] = {}
                row_cells = raw_matrix[r_idx]
                for c_idx, col_name in enumerate(headers):
                    cell_val: Any = row_cells.get(c_idx, None)
                    # Infer numeric types where possible
                    if cell_val is not None and cell_val != "":
                        try:
                            if "." in str(cell_val):
                                cell_val = float(cell_val)
                            else:
                                cell_val = int(cell_val)
                        except (ValueError, TypeError):
                            pass
                    row_dict[col_name] = cell_val
                records.append(row_dict)

            if not records:
                return WidgetDataQueryResponse(columns=headers, data=[], total_rows=0)

            df = pl.DataFrame(records)

        elif widget.source_type == "analytics_result":
            # 2. Load Parquet artifact from analytics result scoped to organization
            res_item = await self.analytics_repo.get_by_id_scoped(widget.source_id, organization_id)
            if not res_item or not res_item.artifact_path:
                raise EntityNotFoundError("AnalyticsResult", str(widget.source_id))

            file_path = Path(res_item.artifact_path)
            if not file_path.is_file():
                raise EntityNotFoundError("AnalyticsArtifact", str(file_path))

            df = pl.read_parquet(file_path)

        else:
            raise ValidationError(f"Unsupported source_type: {widget.source_type}")

        # 3. Apply Polars Transformations
        dimensions: list[str] = config.get("dimensions", [])
        metrics: list[dict[str, Any]] = config.get("metrics", [])
        filters: list[dict[str, Any]] = config.get("filters", [])
        sort_by: str | None = config.get("sort_by")
        sort_desc: bool = bool(config.get("sort_desc", False))
        limit: int = min(int(config.get("limit", 100)), 500)

        # Filters — Backend-enforced validation and Polars execution
        ALLOWED_OPS = {"eq", "neq", "gt", "gte", "lt", "lte", "in", "between", "contains"}
        for f in filters:
            f_col = f.get("column")
            f_op = str(f.get("operator", "eq")).lower()
            f_val: Any = f.get("value")

            if not f_col or not isinstance(f_col, str):
                raise ValidationError("Filter 'column' must be a non-empty string.")

            if f_op not in ALLOWED_OPS:
                raise ValidationError(
                    f"Unsupported filter operator '{f_op}'. Allowed: {sorted(ALLOWED_OPS)}"
                )

            if f_col not in df.columns:
                raise ValidationError(
                    f"Filter column '{f_col}' does not exist in data source schema."
                )

            col_type = df.schema[f_col]

            # 1. Type-aware numeric coercion if column is numeric
            if col_type in (pl.Float64, pl.Float32, pl.Int64, pl.Int32, pl.Int16, pl.Int8):
                def _coerce_num(v: Any) -> Any:
                    if v is None or isinstance(v, bool):
                        return v
                    try:
                        return float(v) if "." in str(v) else int(v)
                    except (ValueError, TypeError):
                        raise ValidationError(
                            f"Invalid numeric filter value '{v}' for column '{f_col}'."
                        )

                if isinstance(f_val, (list, tuple)):
                    f_val = [_coerce_num(v) for v in f_val]
                else:
                    f_val = _coerce_num(f_val)

            # 2. Temporal coercion if column is Date or Datetime (DEF-02)
            is_temporal = False
            is_date_only = False
            col_tz = None
            if col_type == pl.Date:
                is_temporal = True
                is_date_only = True
            elif isinstance(col_type, pl.Datetime) or col_type == pl.Datetime:
                is_temporal = True
                col_tz = getattr(col_type, "time_zone", None)

            if is_temporal:
                from datetime import date as py_date, datetime as py_datetime

                def _coerce_temporal(v: Any) -> Any:
                    if v is None:
                        return None
                    if isinstance(v, py_datetime):
                        dt = v
                    elif isinstance(v, py_date):
                        dt = py_datetime.combine(v, py_datetime.min.time())
                    elif isinstance(v, str):
                        clean_str = v.strip().replace("Z", "+00:00")
                        try:
                            if len(clean_str) == 10 and clean_str.count("-") == 2:
                                dt = py_datetime.fromisoformat(clean_str)
                            else:
                                dt = py_datetime.fromisoformat(clean_str)
                        except (ValueError, TypeError):
                            raise ValidationError(
                                f"Invalid date/datetime value '{v}' for column '{f_col}'."
                            )
                    else:
                        raise ValidationError(
                            f"Expected string or date object for column '{f_col}', got {type(v).__name__}."
                        )

                    if is_date_only:
                        return dt.date()

                    if col_tz:
                        if dt.tzinfo is None:
                            from datetime import timezone
                            dt = dt.replace(tzinfo=timezone.utc)
                    else:
                        if dt.tzinfo is not None:
                            from datetime import timezone
                            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                    return dt

                if isinstance(f_val, (list, tuple)):
                    f_val = [_coerce_temporal(v) for v in f_val]
                else:
                    f_val = _coerce_temporal(f_val)

            if f_op == "eq":
                df = df.filter(pl.col(f_col) == f_val)
            elif f_op == "neq":
                df = df.filter(pl.col(f_col) != f_val)
            elif f_op == "gt":
                df = df.filter(pl.col(f_col) > f_val)
            elif f_op == "gte":
                df = df.filter(pl.col(f_col) >= f_val)
            elif f_op == "lt":
                df = df.filter(pl.col(f_col) < f_val)
            elif f_op == "lte":
                df = df.filter(pl.col(f_col) <= f_val)
            elif f_op == "in":
                if not isinstance(f_val, (list, tuple)):
                    raise ValidationError(
                        f"Filter operator 'in' requires a list of values, got {type(f_val).__name__}."
                    )
                val_list = list(f_val)
                if not val_list:
                    df = df.filter(pl.lit(False))
                else:
                    df = df.filter(pl.col(f_col).is_in(val_list))
            elif f_op == "between":
                if not isinstance(f_val, (list, tuple)) or len(f_val) != 2:
                    raise ValidationError(
                        "Filter operator 'between' requires a 2-element array [start, end]."
                    )
                low, high = f_val[0], f_val[1]
                # Half-open interval semantics for temporal boundaries: [start, end)
                if is_temporal or (
                    isinstance(low, str) and isinstance(high, str) and "-" in low and ("T" in low or ":" in low)
                ):
                    df = df.filter((pl.col(f_col) >= low) & (pl.col(f_col) < high))
                else:
                    df = df.filter((pl.col(f_col) >= low) & (pl.col(f_col) <= high))
            elif f_op == "contains":
                df = df.filter(pl.col(f_col).cast(pl.Utf8).str.contains(str(f_val)))

        # Specialized Analytical Chart Types (Phase 8 Specification Reconciliation)
        if widget.widget_type == "correlation_matrix":
            num_cols = [c for c in df.columns if df[c].dtype in (pl.Float64, pl.Float32, pl.Int64, pl.Int32, pl.Int16, pl.Int8)]
            if not num_cols:
                return WidgetDataQueryResponse(columns=["variable"], data=[], total_rows=0)
            corr_records: list[dict[str, Any]] = []
            for c1 in num_cols:
                row_item: dict[str, Any] = {"variable": c1}
                for c2 in num_cols:
                    if c1 == c2:
                        row_item[c2] = 1.0
                    else:
                        try:
                            s1 = df.get_column(c1).cast(pl.Float64, strict=False)
                            s2 = df.get_column(c2).cast(pl.Float64, strict=False)
                            corr_val = s1.corr(s2)  # type: ignore[attr-defined]
                            row_item[c2] = round(float(str(corr_val)), 4) if (corr_val is not None and not math.isnan(float(str(corr_val)))) else 0.0
                        except Exception:
                            row_item[c2] = 0.0
                corr_records.append(row_item)
            return WidgetDataQueryResponse(
                columns=["variable"] + num_cols,
                data=corr_records,
                total_rows=len(corr_records),
                summary={},
            )

        if widget.widget_type in ("box_plot", "boxplot"):
            target_cols = [m.get("column") for m in metrics if m.get("column") in df.columns]
            if not target_cols:
                target_cols = [c for c in df.columns if df[c].dtype in (pl.Float64, pl.Float32, pl.Int64, pl.Int32, pl.Int16, pl.Int8)]
            box_records: list[dict[str, Any]] = []
            for c_box in target_cols:
                if c_box is None:
                    continue
                s_box = df.get_column(c_box).cast(pl.Float64, strict=False).drop_nulls()
                if len(s_box) > 0:
                    box_records.append({
                        "metric": c_box,
                        "min": round(float(str(s_box.min())), 4) if s_box.min() is not None else 0.0,
                        "q1": round(float(str(s_box.quantile(0.25))), 4) if s_box.quantile(0.25) is not None else 0.0,
                        "median": round(float(str(s_box.median())), 4) if s_box.median() is not None else 0.0,
                        "q3": round(float(str(s_box.quantile(0.75))), 4) if s_box.quantile(0.75) is not None else 0.0,
                        "max": round(float(str(s_box.max())), 4) if s_box.max() is not None else 0.0,
                    })
            return WidgetDataQueryResponse(
                columns=["metric", "min", "q1", "median", "q3", "max"],
                data=box_records,
                total_rows=len(box_records),
                summary={},
            )

        if widget.widget_type == "histogram":
            target_col = metrics[0].get("column") if metrics and metrics[0].get("column") in df.columns else None
            if not target_col:
                num_cols = [c for c in df.columns if df[c].dtype in (pl.Float64, pl.Float32, pl.Int64, pl.Int32, pl.Int16, pl.Int8)]
                target_col = num_cols[0] if num_cols else None
            if not target_col:
                return WidgetDataQueryResponse(columns=["bin", "count"], data=[], total_rows=0)
            s_hist = df.get_column(target_col).cast(pl.Float64, strict=False).drop_nulls()
            hist_records: list[dict[str, Any]] = []
            if len(s_hist) > 0:
                s_min = float(str(s_hist.min()))
                s_max = float(str(s_hist.max()))
                if s_min == s_max:
                    hist_records.append({"bin": f"{s_min:.1f}", "count": len(s_hist)})
                else:
                    bin_count = 10
                    step = (s_max - s_min) / bin_count
                    for i in range(bin_count):
                        b_low = s_min + i * step
                        b_high = b_low + step
                        cnt = len(s_hist.filter((s_hist >= b_low) & (s_hist < b_high if i < bin_count - 1 else s_hist <= b_high)))
                        hist_records.append({"bin": f"{b_low:.1f}-{b_high:.1f}", "count": cnt})
            return WidgetDataQueryResponse(
                columns=["bin", "count"],
                data=hist_records,
                total_rows=len(hist_records),
                summary={},
            )

        if widget.widget_type in ("summary_stats", "summary_statistics"):
            num_cols = [c for c in df.columns if df[c].dtype in (pl.Float64, pl.Float32, pl.Int64, pl.Int32, pl.Int16, pl.Int8)]
            stat_records: list[dict[str, Any]] = []
            for c_stat in num_cols:
                s_stat = df.get_column(c_stat).cast(pl.Float64, strict=False)
                s_clean = s_stat.drop_nulls()
                stat_records.append({
                    "column": c_stat,
                    "count": len(s_stat),
                    "nulls": s_stat.null_count(),
                    "mean": round(float(str(s_clean.mean())), 4) if len(s_clean) > 0 and s_clean.mean() is not None else 0.0,
                    "std": round(float(str(s_clean.std())), 4) if len(s_clean) > 0 and s_clean.std() is not None else 0.0,
                    "min": round(float(str(s_clean.min())), 4) if len(s_clean) > 0 and s_clean.min() is not None else 0.0,
                    "max": round(float(str(s_clean.max())), 4) if len(s_clean) > 0 and s_clean.max() is not None else 0.0,
                })
            return WidgetDataQueryResponse(
                columns=["column", "count", "nulls", "mean", "std", "min", "max"],
                data=stat_records,
                total_rows=len(stat_records),
                summary={},
            )

        if widget.widget_type in ("data_table", "table"):
            # Server-side pagination, sorting, column selection, filters
            selected_cols = config.get("columns", [])
            valid_cols = [c for c in selected_cols if c in df.columns] if selected_cols else list(df.columns)
            offset = max(int(config.get("offset", 0)), 0)
            page_limit = min(max(int(config.get("limit", 25)), 1), 500)

            if sort_by and sort_by in df.columns:
                df = df.sort(sort_by, descending=sort_desc)

            total_records = len(df)
            paged_df = df.select(valid_cols).slice(offset, page_limit)
            return WidgetDataQueryResponse(
                columns=valid_cols,
                data=paged_df.to_dicts(),
                total_rows=total_records,
                summary={
                    "offset": offset,
                    "limit": page_limit,
                    "total": total_records,
                    "page": (offset // page_limit) + 1,
                    "total_pages": math.ceil(total_records / page_limit) if total_records > 0 else 1,
                },
            )

        # Grouping and Aggregation
        valid_dims = [d for d in dimensions if d in df.columns]
        if valid_dims:
            agg_exprs: list[pl.Expr] = []
            for m in metrics:
                m_col_name: str | None = m.get("column")
                m_agg_type: str = str(m.get("agg", "sum")).lower()
                m_alias: str = str(m.get("alias") or f"{m_agg_type}_{m_col_name}")
                if m_col_name and m_col_name in df.columns:
                    col_expr = pl.col(m_col_name)
                    # Cast to numeric if computing numerical aggregation
                    if m_agg_type in ("sum", "mean", "median", "min", "max", "std"):
                        col_expr = pl.col(m_col_name).cast(pl.Float64, strict=False)

                    if m_agg_type == "sum":
                        agg_exprs.append(col_expr.sum().alias(m_alias))
                    elif m_agg_type == "mean":
                        agg_exprs.append(col_expr.mean().alias(m_alias))
                    elif m_agg_type == "median":
                        agg_exprs.append(col_expr.median().alias(m_alias))
                    elif m_agg_type == "min":
                        agg_exprs.append(col_expr.min().alias(m_alias))
                    elif m_agg_type == "max":
                        agg_exprs.append(col_expr.max().alias(m_alias))
                    elif m_agg_type == "count":
                        agg_exprs.append(pl.col(m_col_name).count().alias(m_alias))
                    elif m_agg_type == "std":
                        agg_exprs.append(col_expr.std().alias(m_alias))

            if not agg_exprs:
                # Default count per group
                agg_exprs = [pl.len().alias("count")]

            df = df.group_by(valid_dims).agg(agg_exprs)

        elif metrics:
            select_exprs: list[pl.Expr] = []
            for m in metrics:
                sel_col_name: str | None = m.get("column")
                sel_agg_type: str = str(m.get("agg", "sum")).lower()
                sel_alias: str = str(m.get("alias") or f"{sel_agg_type}_{sel_col_name}")
                if sel_col_name and sel_col_name in df.columns:
                    col_expr = pl.col(sel_col_name).cast(pl.Float64, strict=False)
                    if sel_agg_type == "sum":
                        select_exprs.append(col_expr.sum().alias(sel_alias))
                    elif sel_agg_type == "mean":
                        select_exprs.append(col_expr.mean().alias(sel_alias))
                    elif sel_agg_type == "median":
                        select_exprs.append(col_expr.median().alias(sel_alias))
                    elif sel_agg_type == "min":
                        select_exprs.append(col_expr.min().alias(sel_alias))
                    elif sel_agg_type == "max":
                        select_exprs.append(col_expr.max().alias(sel_alias))
                    elif sel_agg_type == "count":
                        select_exprs.append(pl.col(sel_col_name).count().alias(sel_alias))
            if select_exprs:
                df = df.select(select_exprs)

        # Sorting
        if sort_by and sort_by in df.columns:
            df = df.sort(sort_by, descending=sort_desc)

        # Limit
        df = df.head(limit)

        data = df.to_dicts()
        summary: dict[str, Any] = {}
        for col_name in df.columns:
            col_series = df.get_column(col_name)
            if col_series.dtype in (pl.Float64, pl.Float32, pl.Int64, pl.Int32, pl.Int16, pl.Int8):
                sum_metric = col_series.sum()
                mean_metric = col_series.mean()
                summary[col_name] = {
                    "total": float(str(sum_metric)) if len(col_series) > 0 and sum_metric is not None else 0.0,
                    "avg": float(str(mean_metric)) if len(col_series) > 0 and mean_metric is not None else 0.0,
                }

        return WidgetDataQueryResponse(
            columns=df.columns,
            data=data,
            total_rows=len(data),
            summary=summary,
        )

    async def export_widget_csv(
        self,
        widget_id: uuid.UUID,
        organization_id: uuid.UUID,
        custom_config: dict[str, Any] | None = None,
    ) -> str:
        """Generates RFC 4180-compliant CSV with formula injection sanitization (CWE-1236)."""
        import csv
        import io

        query_res = await self.query_widget_data(widget_id, organization_id, custom_config)
        columns = query_res.columns
        rows = query_res.data

        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

        def sanitize_val(val: Any) -> Any:
            if val is None:
                return ""
            s = str(val)
            # CWE-1236 Formula Injection Protection:
            # Prefix dangerous formula triggers (=, +, -, @, \t, \r) with ' (single quote)
            if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
                return f"'{s}"
            return s

        writer.writerow([sanitize_val(c) for c in columns])
        for row in rows:
            writer.writerow([sanitize_val(row.get(c, "")) for c in columns])

        return output.getvalue()
