/**
 * Type definitions for VIBE Enterprise BI Dashboard Studio & Global Filtering Engine.
 */

export type DatePreset =
  | "today"
  | "yesterday"
  | "last_7_days"
  | "last_30_days"
  | "last_90_days"
  | "this_month"
  | "previous_month"
  | "this_quarter"
  | "this_year"
  | "custom";

export type FilterOperator =
  | "eq"
  | "neq"
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "in"
  | "between"
  | "contains";

export interface FilterCriterion {
  id: string;
  column: string;
  operator: FilterOperator;
  value: string | number | boolean | (string | number)[];
  display_label?: string;
  is_local?: boolean;
  enabled?: boolean;
}

export interface DashboardFilterState {
  timezone: string;
  date_preset?: DatePreset;
  date_range?: {
    start: string; // ISO 8601 string
    end: string;   // ISO 8601 string
  };
  criteria: FilterCriterion[];
}

export interface DashboardLayoutItem {
  widget_id: string;
  position_x: number; // 0 to 11 (12-column grid)
  position_y: number; // row index
  width: number;      // 3 to 12 columns
  height: number;     // 2 to 8 rows (each row ~90px)
  sort_order: number;
}

export interface Dashboard {
  id: string;
  organization_id?: string;
  workspace_id: string;
  title: string;
  description?: string;
  layout_config: {
    version?: number;
    timezone?: string;
    filters?: DashboardFilterState;
    grid_columns?: number;
    positions?: Record<string, DashboardLayoutItem>;
    [key: string]: unknown;
  };
  is_shared: boolean;
  widget_count: number;
  created_at?: string;
  updated_at?: string;
}

export interface Widget {
  id: string;
  dashboard_id: string;
  organization_id?: string;
  title: string;
  widget_type: string;
  source_type: "sheet" | "analytics_result";
  source_id: string;
  query_config: {
    dimensions?: string[];
    metrics?: Array<{ column: string; agg: string; alias?: string }>;
    filters?: Array<{ column: string; operator: string; value: unknown }>;
    local_filters?: FilterCriterion[];
    header_row?: number;
    limit?: number;
    sort_by?: string;
    sort_desc?: boolean;
    bar_mode?: "grouped" | "stacked";
    pie_variant?: "pie" | "donut";
    smooth?: boolean;
    target?: number;
    comparison_label?: string;
    [key: string]: unknown;
  };
  position_x: number;
  position_y: number;
  width: number;
  height: number;
  sort_order: number;
  created_at?: string;
  updated_at?: string;
}

export interface QueryResult {
  columns: string[];
  data: Record<string, unknown>[];
  total_rows: number;
  summary: Record<string, unknown>;
}

