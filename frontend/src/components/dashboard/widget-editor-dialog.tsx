"use client";

import * as React from "react";
import {
  Activity,
  BarChart3,
  Layers,
  LineChart as LineChartIcon,
  PieChart as PieChartIcon,
  Sliders,
  Grid,
  Table as TableIcon,
  TrendingUp,
  Plus,
  Trash2,
  SlidersHorizontal,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Widget, FilterCriterion, FilterOperator } from "./dashboard-types";
import { ALLOWED_OPERATORS } from "./filter-utils";

export const WIDGET_TYPES = [
  { id: "kpi_card", label: "KPI Metric Card", icon: Activity },
  { id: "bar", label: "Bar Chart", icon: BarChart3 },
  { id: "stacked_bar", label: "Stacked Bar Chart", icon: Layers },
  { id: "horizontal_bar", label: "Horizontal Bar Chart", icon: BarChart3 },
  { id: "line", label: "Line Chart", icon: LineChartIcon },
  { id: "multi_axis_line", label: "Smooth Multi-Axis Line", icon: LineChartIcon },
  { id: "area", label: "Stacked Area Chart", icon: TrendingUp },
  { id: "pie", label: "Pie Chart", icon: PieChartIcon },
  { id: "donut", label: "Donut Chart", icon: PieChartIcon },
  { id: "scatter", label: "Scatter Plot", icon: Sliders },
  { id: "heatmap", label: "Heatmap Matrix", icon: Grid },
  { id: "data_table", label: "Interactive Data Table (Paginated)", icon: TableIcon },
  { id: "table", label: "Aggregated Data Table", icon: TableIcon },
];

interface WidgetEditorDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  widgetToEdit: Widget | null;
  onSaveWidget: (widgetPayload: Partial<Widget>) => void;
  availableColumns?: string[];
}

function WidgetEditorForm({
  widgetToEdit,
  onSaveWidget,
  onCancel,
}: {
  widgetToEdit: Widget | null;
  onSaveWidget: (widgetPayload: Partial<Widget>) => void;
  onCancel: () => void;
}) {
  const isEditing = Boolean(widgetToEdit);
  const qc = widgetToEdit?.query_config || {};

  const [title, setTitle] = React.useState(widgetToEdit?.title || "");
  const [widgetType, setWidgetType] = React.useState(widgetToEdit?.widget_type || "bar");
  const [sourceType, setSourceType] = React.useState<"sheet" | "analytics_result">(
    widgetToEdit?.source_type || "sheet"
  );
  const [sourceId, setSourceId] = React.useState(widgetToEdit?.source_id || "");
  const [dimensionCol, setDimensionCol] = React.useState(
    Array.isArray(qc.dimensions) && qc.dimensions.length > 0 ? qc.dimensions[0] : ""
  );
  const [metricCol, setMetricCol] = React.useState(
    Array.isArray(qc.metrics) && qc.metrics.length > 0 ? qc.metrics[0].column : ""
  );
  const [metricAgg, setMetricAgg] = React.useState(
    Array.isArray(qc.metrics) && qc.metrics.length > 0 ? qc.metrics[0].agg : "sum"
  );
  const [barMode, setBarMode] = React.useState<"grouped" | "stacked">(
    qc.bar_mode === "stacked" ? "stacked" : "grouped"
  );
  const [pieVariant, setPieVariant] = React.useState<"pie" | "donut">(
    qc.pie_variant === "donut" ? "donut" : "pie"
  );
  const [smoothLine, setSmoothLine] = React.useState(Boolean(qc.smooth));
  const [kpiTarget, setKpiTarget] = React.useState(
    qc.target !== undefined ? String(qc.target) : ""
  );
  const [localFilters, setLocalFilters] = React.useState<FilterCriterion[]>(
    qc.local_filters || []
  );

  // Local filter form
  const [filterCol, setFilterCol] = React.useState("");
  const [filterOp, setFilterOp] = React.useState<FilterOperator>("eq");
  const [filterVal, setFilterVal] = React.useState("");

  const handleAddLocalFilter = () => {
    if (!filterCol.trim() || !filterVal.trim()) return;
    const criterion: FilterCriterion = {
      id: `local-${Date.now()}-${Math.random().toString(36).substr(2, 4)}`,
      column: filterCol.trim(),
      operator: filterOp,
      value: filterVal.trim(),
      is_local: true,
      enabled: true,
    };
    setLocalFilters((prev) => [...prev, criterion]);
    setFilterCol("");
    setFilterVal("");
  };

  const handleRemoveLocalFilter = (id: string) => {
    setLocalFilters((prev) => prev.filter((f) => f.id !== id));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    const query_config: Record<string, unknown> = {
      header_row: 0,
      limit: 50,
      local_filters: localFilters,
    };

    if (dimensionCol.trim()) {
      query_config.dimensions = [dimensionCol.trim()];
    }
    if (metricCol.trim()) {
      query_config.metrics = [
        {
          column: metricCol.trim(),
          agg: metricAgg,
          alias: `${metricAgg}_${metricCol.trim()}`,
        },
      ];
    }
    if (widgetType === "stacked_bar" || barMode === "stacked") {
      query_config.bar_mode = "stacked";
    } else {
      query_config.bar_mode = "grouped";
    }
    if (widgetType === "donut" || pieVariant === "donut") {
      query_config.pie_variant = "donut";
    }
    query_config.smooth = smoothLine;
    if (kpiTarget.trim() && !isNaN(Number(kpiTarget))) {
      query_config.target = Number(kpiTarget);
      query_config.comparison_label = "Target";
    }

    const payload: Partial<Widget> = {
      title: title.trim(),
      widget_type: widgetType,
      source_type: sourceType,
      source_id: sourceId.trim(),
      query_config,
    };

    onSaveWidget(payload);
    onCancel();
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="text-xs font-semibold">Widget Title</label>
        <Input
          placeholder="e.g. Revenue by Product Category"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          required
        />
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="text-xs font-semibold">Visualization Type</label>
          <select
            aria-label="Visualization Type"
            className="w-full text-sm border rounded-md p-2 bg-background"
            value={widgetType}
            onChange={(e) => setWidgetType(e.target.value)}
          >
            {WIDGET_TYPES.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="text-xs font-semibold">Data Source Type</label>
          <select
            aria-label="Data Source Type"
            className="w-full text-sm border rounded-md p-2 bg-background"
            value={sourceType}
            onChange={(e) => setSourceType(e.target.value as "sheet" | "analytics_result")}
          >
            <option value="sheet">Spreadsheet Sheet</option>
            <option value="analytics_result">Analytics Result Parquet</option>
          </select>
        </div>
      </div>

      <div>
        <label className="text-xs font-semibold">Source ID (UUID)</label>
        <Input
          placeholder="Sheet or Result UUID"
          value={sourceId}
          onChange={(e) => setSourceId(e.target.value)}
          required
        />
      </div>

      {/* Dimension and Metric */}
      <div className="grid grid-cols-3 gap-3">
        <div>
          <label className="text-xs font-semibold">Dimension Column</label>
          <Input
            placeholder="e.g. Category"
            value={dimensionCol}
            onChange={(e) => setDimensionCol(e.target.value)}
          />
        </div>
        <div>
          <label className="text-xs font-semibold">Metric Column</label>
          <Input
            placeholder="e.g. Sales"
            value={metricCol}
            onChange={(e) => setMetricCol(e.target.value)}
          />
        </div>
        <div>
          <label className="text-xs font-semibold">Aggregation</label>
          <select
            aria-label="Aggregation Function"
            className="w-full text-sm border rounded-md p-2 bg-background"
            value={metricAgg}
            onChange={(e) => setMetricAgg(e.target.value)}
          >
            <option value="sum">Sum</option>
            <option value="mean">Mean (Average)</option>
            <option value="median">Median</option>
            <option value="min">Minimum</option>
            <option value="max">Maximum</option>
            <option value="count">Count</option>
          </select>
        </div>
      </div>

      {/* Specific chart customization options */}
      <div className="p-3 border rounded-md bg-muted/20 space-y-3">
        <span className="text-xs font-semibold text-muted-foreground block">
          Chart Display Options
        </span>
        <div className="grid grid-cols-2 gap-4 text-xs">
          {widgetType.includes("bar") && (
            <div>
              <label className="font-medium block mb-1">Bar Mode</label>
              <select
                className="w-full text-xs border rounded p-1.5 bg-background"
                value={barMode}
                onChange={(e) => setBarMode(e.target.value as "grouped" | "stacked")}
              >
                <option value="grouped">Grouped (Side-by-Side)</option>
                <option value="stacked">Stacked</option>
              </select>
            </div>
          )}

          {(widgetType === "pie" || widgetType === "donut") && (
            <div>
              <label className="font-medium block mb-1">Pie Variant</label>
              <select
                className="w-full text-xs border rounded p-1.5 bg-background"
                value={pieVariant}
                onChange={(e) => setPieVariant(e.target.value as "pie" | "donut")}
              >
                <option value="pie">Full Pie Disk</option>
                <option value="donut">Donut Ring</option>
              </select>
            </div>
          )}

          {widgetType.includes("line") && (
            <div className="flex items-center gap-2 pt-4">
              <input
                type="checkbox"
                id="smoothCheck"
                checked={smoothLine}
                onChange={(e) => setSmoothLine(e.target.checked)}
                className="rounded"
              />
              <label htmlFor="smoothCheck" className="cursor-pointer">
                Smooth Bézier curves
              </label>
            </div>
          )}

          {widgetType === "kpi_card" && (
            <div>
              <label className="font-medium block mb-1">KPI Target Value</label>
              <Input
                type="number"
                step="any"
                placeholder="e.g. 50000"
                value={kpiTarget}
                onChange={(e) => setKpiTarget(e.target.value)}
              />
            </div>
          )}
        </div>
      </div>

      {/* Local Filter Overrides */}
      <div className="p-3 border rounded-md bg-muted/20 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-muted-foreground flex items-center gap-1.5">
            <SlidersHorizontal className="h-3.5 w-3.5" />
            Widget-Level Local Filters ({localFilters.length})
          </span>
        </div>

        {localFilters.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {localFilters.map((f) => (
              <Badge
                key={f.id}
                variant="outline"
                className="text-xs py-0.5 px-2 gap-1.5 bg-background border-border"
              >
                <span>
                  {f.column} {f.operator} {String(f.value)}
                </span>
                <button
                  type="button"
                  onClick={() => handleRemoveLocalFilter(f.id)}
                  className="cursor-pointer hover:opacity-75"
                >
                  <Trash2 className="h-3 w-3 text-destructive" />
                </button>
              </Badge>
            ))}
          </div>
        )}

        <div className="grid grid-cols-3 gap-2 pt-2 border-t border-border/40">
          <Input
            placeholder="Column name"
            className="h-7 text-xs"
            value={filterCol}
            onChange={(e) => setFilterCol(e.target.value)}
          />
          <select
            className="h-7 text-xs border rounded px-1.5 bg-background"
            value={filterOp}
            onChange={(e) => setFilterOp(e.target.value as FilterOperator)}
          >
            {ALLOWED_OPERATORS.map((op) => (
              <option key={op} value={op}>
                {op}
              </option>
            ))}
          </select>
          <div className="flex items-center gap-1">
            <Input
              placeholder="Value"
              className="h-7 text-xs flex-1"
              value={filterVal}
              onChange={(e) => setFilterVal(e.target.value)}
            />
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={handleAddLocalFilter}
            >
              <Plus className="h-3 w-3" />
            </Button>
          </div>
        </div>
      </div>

      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          onClick={onCancel}
        >
          Cancel
        </Button>
        <Button type="submit">
          {isEditing ? "Update Widget" : "Create Widget"}
        </Button>
      </DialogFooter>
    </form>
  );
}

export function WidgetEditorDialog({
  open,
  onOpenChange,
  widgetToEdit,
  onSaveWidget,
}: WidgetEditorDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {widgetToEdit ? "Configure Widget" : "Add Visualization Widget"}
          </DialogTitle>
          <DialogDescription>
            Configure chart dimensions, metrics, and local filter overrides for server-side aggregation.
          </DialogDescription>
        </DialogHeader>

        {open && (
          <WidgetEditorForm
            key={widgetToEdit?.id || "new-widget"}
            widgetToEdit={widgetToEdit}
            onSaveWidget={onSaveWidget}
            onCancel={() => onOpenChange(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
