/**
 * Typed chart configurations, supported visualization types, and declarative formatters.
 */

import type { EChartsOption } from "echarts";

/**
 * 10 Supported Visualization Configurations:
 * 1. Standard Bar
 * 2. Stacked/Grouped Bar (selectable via bar_mode: "stacked" | "grouped")
 * 3. Horizontal Bar
 * 4. Standard Line (conventional non-smoothed)
 * 5. Smooth Multi-Axis Line (smooth curves and dual/multi Y-axes)
 * 6. Stacked Area
 * 7. Pie/Donut (variants within the Pie/Donut chart family)
 * 8. Scatter
 * 9. Heatmap Matrix
 * 10. Multi-Metric KPI Card
 */
export type SupportedChartType =
  | "bar"
  | "grouped_bar"
  | "stacked_bar"
  | "horizontal_bar"
  | "line"
  | "smooth_multi_axis_line"
  | "multi_axis_line"
  | "stacked_area"
  | "pie"
  | "donut"
  | "scatter"
  | "heatmap"
  | "kpi_card";

/**
 * Declarative Formatter Tokens (no arbitrary JS execution allowed)
 */
export type FormatterToken =
  | "number"
  | "number_compact"
  | "percentage"
  | "currency_usd"
  | "currency_local"
  | "integer"
  | "decimal_2";

/**
 * Tabular Data Shape emitted by backend Polars query aggregation
 */
export interface TabularDataPayload {
  columns: string[];
  data: Record<string, unknown>[];
  total_rows: number;
  summary?: Record<string, unknown>;
}

/**
 * Metric Configuration inside query_config
 */
export interface QueryMetricConfig {
  column: string;
  aggregation?: string;
  formatter?: FormatterToken;
  axis_index?: number;
  label?: string;
}

/**
 * Dimension Configuration inside query_config
 */
export interface QueryDimensionConfig {
  column: string;
  label?: string;
  time_interval?: string;
}

/**
 * High-Level Chart Configuration Specification
 */
export interface ChartConfigSpec {
  chart_type: SupportedChartType;
  title?: string;
  subtitle?: string;
  dimensions?: (string | QueryDimensionConfig)[];
  metrics?: (string | QueryMetricConfig)[];
  formatter?: FormatterToken;
  color_palette?: string[];
  show_legend?: boolean;
  show_data_zoom?: boolean;
  /** Selectable behavior for Bar charts */
  bar_mode?: "grouped" | "stacked";
  /** Selectable variant for Pie/Donut charts */
  pie_variant?: "pie" | "donut";
  /** Smooth line curves (default: false for standard line, true for smooth_multi_axis_line) */
  smooth?: boolean;
  stacked?: boolean;
  horizontal?: boolean;
  is_donut?: boolean;
  is_area?: boolean;
  x_axis_label?: string;
  y_axis_label?: string;
  y2_axis_label?: string;
  kpi_target?: number;
  kpi_comparison_label?: string;
}

/**
 * Theme Tokens required for ECharts options styling
 */
export interface ChartThemeTokens {
  mode: "light" | "dark";
  background: string;
  surface: string;
  foreground: string;
  mutedForeground: string;
  border: string;
  primary: string;
  palette: string[];
  gridLineColor: string;
  tooltipBackground: string;
  tooltipBorder: string;
  tooltipText: string;
}

/**
 * Option Builder Output
 */
export interface OptionBuilderResult {
  option: EChartsOption | null;
  isValid: boolean;
  emptyReason?: string;
}
