"use client";

import * as React from "react";
import {
  Calendar,
  Filter,
  Plus,
  X,
  Globe,
  RotateCcw,
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
import {
  DashboardFilterState,
  DatePreset,
  FilterCriterion,
  FilterOperator,
} from "./dashboard-types";
import {
  DATE_PRESETS,
  ALLOWED_OPERATORS,
  calculateDatePresetRange,
  validateFilterCriterion,
} from "./filter-utils";

interface FilterBarProps {
  filterState: DashboardFilterState;
  onFilterChange: (newState: DashboardFilterState) => void;
  availableColumns?: string[];
}

const COMMON_TIMEZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Los_Angeles",
  "Europe/London",
  "Europe/Paris",
  "Asia/Tokyo",
  "Asia/Singapore",
  "Australia/Sydney",
];

export function FilterBar({
  filterState,
  onFilterChange,
  availableColumns = [],
}: FilterBarProps) {
  const [addFilterOpen, setAddFilterOpen] = React.useState(false);
  const [customDateOpen, setCustomDateOpen] = React.useState(false);

  // Form states for adding a filter
  const [newCol, setNewCol] = React.useState("");
  const [newOp, setNewOp] = React.useState<FilterOperator>("eq");
  const [newVal, setNewVal] = React.useState("");
  const [newValSecond, setNewValSecond] = React.useState(""); // for 'between'
  const [formError, setFormError] = React.useState<string | null>(null);

  // Custom date states
  const [customStart, setCustomStart] = React.useState(
    filterState.date_range?.start?.slice(0, 10) || ""
  );
  const [customEnd, setCustomEnd] = React.useState(
    filterState.date_range?.end?.slice(0, 10) || ""
  );

  const activeTimezone = filterState.timezone || "UTC";

  // Handle Preset Change
  const handlePresetSelect = (preset: DatePreset) => {
    if (preset === "custom") {
      setCustomDateOpen(true);
      return;
    }
    const range = calculateDatePresetRange(preset, activeTimezone);
    onFilterChange({
      ...filterState,
      date_preset: preset,
      date_range: range,
    });
  };

  // Handle Custom Date Submit
  const handleCustomDateSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!customStart || !customEnd) return;
    const startISO = new Date(`${customStart}T00:00:00.000Z`).toISOString();
    const endISO = new Date(`${customEnd}T23:59:59.999Z`).toISOString();
    onFilterChange({
      ...filterState,
      date_preset: "custom",
      date_range: { start: startISO, end: endISO },
    });
    setCustomDateOpen(false);
  };

  // Handle Timezone Change
  const handleTimezoneChange = (tz: string) => {
    const preset = filterState.date_preset || "last_30_days";
    const range =
      preset !== "custom"
        ? calculateDatePresetRange(preset, tz)
        : filterState.date_range;
    onFilterChange({
      ...filterState,
      timezone: tz,
      date_range: range,
    });
  };

  // Add Categorical / Numeric Filter
  const handleAddFilter = (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);

    let parsedVal: string | number | boolean | (string | number)[] = newVal;

    if (newOp === "in") {
      parsedVal = newVal
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    } else if (newOp === "between") {
      const v1 = parseFloat(newVal);
      const v2 = parseFloat(newValSecond);
      if (isNaN(v1) || isNaN(v2)) {
        setFormError("Both minimum and maximum values must be valid numbers");
        return;
      }
      parsedVal = [v1, v2];
    } else if (["gt", "gte", "lt", "lte"].includes(newOp)) {
      const num = parseFloat(newVal);
      if (!isNaN(num)) parsedVal = num;
    } else if (newVal.toLowerCase() === "true") {
      parsedVal = true;
    } else if (newVal.toLowerCase() === "false") {
      parsedVal = false;
    }

    const criterion: FilterCriterion = {
      id: `filter-${Date.now()}-${Math.random().toString(36).substr(2, 4)}`,
      column: newCol,
      operator: newOp,
      value: parsedVal,
      enabled: true,
      display_label: `${newCol} ${newOp} ${Array.isArray(parsedVal) ? parsedVal.join(", ") : parsedVal}`,
    };

    const validation = validateFilterCriterion(criterion);
    if (!validation.isValid) {
      setFormError(validation.error || "Invalid filter parameters");
      return;
    }

    onFilterChange({
      ...filterState,
      criteria: [...filterState.criteria, criterion],
    });

    setAddFilterOpen(false);
    setNewCol("");
    setNewOp("eq");
    setNewVal("");
    setNewValSecond("");
  };

  // Remove Filter
  const handleRemoveFilter = (id: string) => {
    onFilterChange({
      ...filterState,
      criteria: filterState.criteria.filter((c) => c.id !== id),
    });
  };

  // Clear All
  const handleClearAll = () => {
    onFilterChange({
      timezone: activeTimezone,
      date_preset: undefined,
      date_range: undefined,
      criteria: [],
    });
  };

  const hasActiveFilters =
    Boolean(filterState.date_range) || filterState.criteria.length > 0;

  return (
    <div className="flex flex-col gap-2 p-3 rounded-lg border bg-card/90 shadow-xs">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          {/* Filter Bar Header Icon & Label */}
          <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground mr-1">
            <Filter className="h-3.5 w-3.5" />
            <span>Global Filters</span>
          </div>

          {/* Date Preset Dropdown */}
          <div className="flex items-center gap-1">
            <Calendar className="h-3.5 w-3.5 text-muted-foreground" />
            <select
              aria-label="Date Range Preset"
              className="text-xs border rounded-md px-2 py-1 bg-background"
              value={filterState.date_preset || ""}
              onChange={(e) => handlePresetSelect(e.target.value as DatePreset)}
            >
              <option value="" disabled>
                Select Date Preset...
              </option>
              {DATE_PRESETS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>

          {/* Timezone Dropdown */}
          <div className="flex items-center gap-1">
            <Globe className="h-3.5 w-3.5 text-muted-foreground" />
            <select
              aria-label="Dashboard Timezone"
              className="text-xs border rounded-md px-2 py-1 bg-background"
              value={activeTimezone}
              onChange={(e) => handleTimezoneChange(e.target.value)}
            >
              {COMMON_TIMEZONES.map((tz) => (
                <option key={tz} value={tz}>
                  {tz}
                </option>
              ))}
            </select>
          </div>

          {/* Add Filter Button */}
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs gap-1 px-2.5"
            onClick={() => setAddFilterOpen(true)}
          >
            <Plus className="h-3.5 w-3.5" />
            Add Filter
          </Button>

          {/* Clear All Button */}
          {hasActiveFilters && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs gap-1 px-2 text-muted-foreground hover:text-destructive"
              onClick={handleClearAll}
            >
              <RotateCcw className="h-3 w-3" />
              Clear All
            </Button>
          )}
        </div>

        {/* Active Filter Count Badge */}
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Badge variant="secondary" className="text-[11px] font-normal">
            {(filterState.date_range ? 1 : 0) + filterState.criteria.length} Active Filters
          </Badge>
        </div>
      </div>

      {/* Active Filter Chips Container */}
      {hasActiveFilters && (
        <div className="flex flex-wrap items-center gap-1.5 pt-1 border-t border-border/50">
          {/* Date Filter Chip */}
          {filterState.date_range && (
            <Badge
              variant="outline"
              className="text-xs py-0.5 px-2 gap-1.5 bg-primary/5 border-primary/20 text-primary"
            >
              <Calendar className="h-3 w-3" />
              <span>
                {filterState.date_preset
                  ? DATE_PRESETS.find((p) => p.id === filterState.date_preset)?.label
                  : "Date"}
                : {filterState.date_range.start.slice(0, 10)} to{" "}
                {filterState.date_range.end.slice(0, 10)} ({activeTimezone})
              </span>
              <button
                type="button"
                aria-label="Clear date range filter"
                onClick={() =>
                  onFilterChange({
                    ...filterState,
                    date_preset: undefined,
                    date_range: undefined,
                  })
                }
                className="hover:opacity-75 cursor-pointer ml-1"
              >
                <X className="h-3 w-3" />
              </button>
            </Badge>
          )}

          {/* Categorical & Numeric Filter Chips */}
          {filterState.criteria.map((c) => (
            <Badge
              key={c.id}
              variant="outline"
              className="text-xs py-0.5 px-2 gap-1.5 bg-accent/40 border-border"
            >
              <SlidersHorizontal className="h-3 w-3 text-muted-foreground" />
              <span>
                <strong>{c.column}</strong> {c.operator}{" "}
                {Array.isArray(c.value) ? c.value.join(", ") : String(c.value)}
              </span>
              <button
                type="button"
                aria-label={`Remove filter for ${c.column}`}
                onClick={() => handleRemoveFilter(c.id)}
                className="hover:opacity-75 cursor-pointer ml-1"
              >
                <X className="h-3 w-3" />
              </button>
            </Badge>
          ))}
        </div>
      )}

      {/* Add Filter Modal Dialog */}
      <Dialog open={addFilterOpen} onOpenChange={setAddFilterOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Add Global Filter</DialogTitle>
            <DialogDescription>
              Filter all compatible charts and KPI widgets across the dashboard.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleAddFilter} className="space-y-4">
            {formError && (
              <div
                role="alert"
                className="text-xs text-destructive bg-destructive/10 p-2 rounded border border-destructive/20"
              >
                {formError}
              </div>
            )}

            <div>
              <label className="text-xs font-semibold">Column / Dimension</label>
              {availableColumns.length > 0 ? (
                <div className="space-y-1">
                  <select
                    aria-label="Filter Column"
                    className="w-full text-sm border rounded-md p-2 bg-background"
                    value={newCol}
                    onChange={(e) => setNewCol(e.target.value)}
                    required
                  >
                    <option value="">Select Column...</option>
                    {availableColumns.map((col) => (
                      <option key={col} value={col}>
                        {col}
                      </option>
                    ))}
                  </select>
                </div>
              ) : (
                <Input
                  placeholder="e.g. Region, Category, Sales"
                  value={newCol}
                  onChange={(e) => setNewCol(e.target.value)}
                  required
                />
              )}
            </div>

            <div>
              <label className="text-xs font-semibold">Operator</label>
              <select
                aria-label="Filter Operator"
                className="w-full text-sm border rounded-md p-2 bg-background"
                value={newOp}
                onChange={(e) => setNewOp(e.target.value as FilterOperator)}
              >
                {ALLOWED_OPERATORS.map((op) => (
                  <option key={op} value={op}>
                    {op === "eq" && "equals (eq)"}
                    {op === "neq" && "does not equal (neq)"}
                    {op === "gt" && "greater than (gt)"}
                    {op === "gte" && "greater than or equal (gte)"}
                    {op === "lt" && "less than (lt)"}
                    {op === "lte" && "less than or equal (lte)"}
                    {op === "in" && "is in list (in)"}
                    {op === "between" && "is between (between)"}
                    {op === "contains" && "contains text (contains)"}
                  </option>
                ))}
              </select>
            </div>

            {newOp === "between" ? (
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-xs font-semibold">Minimum Value</label>
                  <Input
                    type="number"
                    step="any"
                    placeholder="Min value"
                    value={newVal}
                    onChange={(e) => setNewVal(e.target.value)}
                    required
                  />
                </div>
                <div>
                  <label className="text-xs font-semibold">Maximum Value</label>
                  <Input
                    type="number"
                    step="any"
                    placeholder="Max value"
                    value={newValSecond}
                    onChange={(e) => setNewValSecond(e.target.value)}
                    required
                  />
                </div>
              </div>
            ) : (
              <div>
                <label className="text-xs font-semibold">
                  {newOp === "in" ? "Values (comma separated)" : "Filter Value"}
                </label>
                <Input
                  placeholder={
                    newOp === "in"
                      ? "Electronics, Apparel, Furniture"
                      : "e.g. East, 1000, active"
                  }
                  value={newVal}
                  onChange={(e) => setNewVal(e.target.value)}
                  required
                />
              </div>
            )}

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setAddFilterOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit">Apply Filter</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* Custom Date Range Dialog */}
      <Dialog open={customDateOpen} onOpenChange={setCustomDateOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Custom Date Range</DialogTitle>
            <DialogDescription>
              Select exact start and end dates evaluated in {activeTimezone}.
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleCustomDateSubmit} className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-xs font-semibold">Start Date</label>
                <Input
                  type="date"
                  value={customStart}
                  onChange={(e) => setCustomStart(e.target.value)}
                  required
                />
              </div>
              <div>
                <label className="text-xs font-semibold">End Date</label>
                <Input
                  type="date"
                  value={customEnd}
                  onChange={(e) => setCustomEnd(e.target.value)}
                  required
                />
              </div>
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setCustomDateOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit">Set Date Range</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
