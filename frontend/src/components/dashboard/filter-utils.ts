/**
 * Pure utility functions for Timezone-Correct Date Filtering, Filter Merging,
 * and Cross-Widget Compatibility Validation.
 */

import {
  DatePreset,
  FilterCriterion,
  FilterOperator,
  Widget,
} from "./dashboard-types";

export const ALLOWED_OPERATORS: FilterOperator[] = [
  "eq",
  "neq",
  "gt",
  "gte",
  "lt",
  "lte",
  "in",
  "between",
  "contains",
];

export const DATE_PRESETS: { id: DatePreset; label: string }[] = [
  { id: "today", label: "Today" },
  { id: "yesterday", label: "Yesterday" },
  { id: "last_7_days", label: "Last 7 Days" },
  { id: "last_30_days", label: "Last 30 Days" },
  { id: "last_90_days", label: "Last 90 Days" },
  { id: "this_month", label: "This Month" },
  { id: "previous_month", label: "Previous Month" },
  { id: "this_quarter", label: "This Quarter" },
  { id: "this_year", label: "This Year" },
  { id: "custom", label: "Custom Range" },
];

/**
 * Validates whether an IANA timezone identifier is valid in this JavaScript runtime.
 */
export function isValidTimezone(tz: string): boolean {
  if (!tz || typeof tz !== "string" || !tz.trim()) return false;
  try {
    Intl.DateTimeFormat(undefined, { timeZone: tz.trim() });
    return true;
  } catch {
    return false;
  }
}

/**
 * Resolves a safe timezone string, defaulting to "UTC" if invalid.
 */
export function resolveSafeTimezone(tz?: string): string {
  if (tz && isValidTimezone(tz)) {
    return tz.trim();
  }
  return "UTC";
}

/**
 * Get date parts (year, month 0-11, day 1-31) in the specified IANA timezone.
 */
export function getDatePartsInTimezone(date: Date, timezone: string): {
  year: number;
  month: number;
  day: number;
} {
  const safeTz = resolveSafeTimezone(timezone);
  try {
    const formatter = new Intl.DateTimeFormat("en-US", {
      timeZone: safeTz,
      year: "numeric",
      month: "numeric",
      day: "numeric",
    });
    const parts = formatter.formatToParts(date);
    let year = date.getUTCFullYear();
    let month = date.getUTCMonth();
    let day = date.getUTCDate();
    for (const p of parts) {
      if (p.type === "year") year = parseInt(p.value, 10);
      if (p.type === "month") month = parseInt(p.value, 10) - 1; // 0-based
      if (p.type === "day") day = parseInt(p.value, 10);
    }
    return { year, month, day };
  } catch {
    return {
      year: date.getUTCFullYear(),
      month: date.getUTCMonth(),
      day: date.getUTCDate(),
    };
  }
}

/**
 * Helper to compute timezone offset in milliseconds at a specific reference instant.
 */
function getTimezoneOffsetMs(date: Date, timezone: string): number {
  const safeTz = resolveSafeTimezone(timezone);
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone: safeTz,
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
    second: "numeric",
    hour12: false,
  });
  const parts = formatter.formatToParts(date);
  let y = 0, m = 0, d = 0, h = 0, min = 0, s = 0;
  for (const p of parts) {
    if (p.type === "year") y = parseInt(p.value, 10);
    if (p.type === "month") m = parseInt(p.value, 10);
    if (p.type === "day") d = parseInt(p.value, 10);
    if (p.type === "hour") h = parseInt(p.value, 10) % 24;
    if (p.type === "minute") min = parseInt(p.value, 10);
    if (p.type === "second") s = parseInt(p.value, 10);
  }
  const localAsUtc = Date.UTC(y, m - 1, d, h, min, s);
  return localAsUtc - date.getTime();
}

/**
 * Converts a calendar date and time in a specified IANA timezone to a true UTC Instant.
 * Accurately handles DST spring-forward and fall-back boundaries.
 */
export function localToUtcInstant(
  year: number,
  month: number, // 0-based month
  day: number,
  hour: number,
  minute: number,
  second: number,
  millisecond: number,
  timezone: string
): Date {
  const safeTz = resolveSafeTimezone(timezone);
  const naiveUtc = Date.UTC(year, month, day, hour, minute, second, millisecond);
  const offset = getTimezoneOffsetMs(new Date(naiveUtc), safeTz);
  let utcMs = naiveUtc - offset;
  const refinedOffset = getTimezoneOffsetMs(new Date(utcMs), safeTz);
  if (refinedOffset !== offset) {
    utcMs = naiveUtc - refinedOffset;
  }
  return new Date(utcMs);
}

/**
 * Creates an ISO string representing a local calendar boundary converted to UTC.
 */
function createBoundaryISO(
  year: number,
  month: number, // 0-based month
  day: number,
  hour: number,
  minute: number,
  second: number,
  millisecond: number,
  timezone: string
): string {
  const d = localToUtcInstant(year, month, day, hour, minute, second, millisecond, timezone);
  return d.toISOString();
}

/**
 * Helper to add days to a calendar date using UTC date arithmetic.
 */
function addCalendarDays(year: number, month: number, day: number, daysToAdd: number): {
  year: number;
  month: number;
  day: number;
} {
  const d = new Date(Date.UTC(year, month, day + daysToAdd));
  return {
    year: d.getUTCFullYear(),
    month: d.getUTCMonth(),
    day: d.getUTCDate(),
  };
}

/**
 * Calculates start and end timestamp boundaries for a given preset in the given timezone.
 * Returns half-open interval semantics: [start, end) where:
 * start <= timestamp < end
 */
export function calculateDatePresetRange(
  preset: DatePreset,
  timezone: string = "UTC",
  referenceDate: Date = new Date()
): { start: string; end: string } {
  const safeTz = resolveSafeTimezone(timezone);
  const { year, month, day } = getDatePartsInTimezone(referenceDate, safeTz);

  // Tomorrow is the start of the next calendar day in half-open [start, end)
  const tomorrow = addCalendarDays(year, month, day, 1);

  switch (preset) {
    case "today": {
      const start = createBoundaryISO(year, month, day, 0, 0, 0, 0, safeTz);
      const end = createBoundaryISO(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0, 0, safeTz);
      return { start, end };
    }
    case "yesterday": {
      const yDay = addCalendarDays(year, month, day, -1);
      const start = createBoundaryISO(yDay.year, yDay.month, yDay.day, 0, 0, 0, 0, safeTz);
      const end = createBoundaryISO(year, month, day, 0, 0, 0, 0, safeTz);
      return { start, end };
    }
    case "last_7_days": {
      // 7 calendar days ending at end of today (half-open tomorrow midnight)
      const sDay = addCalendarDays(year, month, day, -6);
      const start = createBoundaryISO(sDay.year, sDay.month, sDay.day, 0, 0, 0, 0, safeTz);
      const end = createBoundaryISO(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0, 0, safeTz);
      return { start, end };
    }
    case "last_30_days": {
      // 30 calendar days ending at end of today
      const sDay = addCalendarDays(year, month, day, -29);
      const start = createBoundaryISO(sDay.year, sDay.month, sDay.day, 0, 0, 0, 0, safeTz);
      const end = createBoundaryISO(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0, 0, safeTz);
      return { start, end };
    }
    case "last_90_days": {
      // 90 calendar days ending at end of today
      const sDay = addCalendarDays(year, month, day, -89);
      const start = createBoundaryISO(sDay.year, sDay.month, sDay.day, 0, 0, 0, 0, safeTz);
      const end = createBoundaryISO(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0, 0, safeTz);
      return { start, end };
    }
    case "this_month": {
      const start = createBoundaryISO(year, month, 1, 0, 0, 0, 0, safeTz);
      // Start of next month in half-open [start, end)
      const nextMonth = new Date(Date.UTC(year, month + 1, 1));
      const end = createBoundaryISO(nextMonth.getUTCFullYear(), nextMonth.getUTCMonth(), 1, 0, 0, 0, 0, safeTz);
      return { start, end };
    }
    case "previous_month": {
      const prevMonth = new Date(Date.UTC(year, month - 1, 1));
      const start = createBoundaryISO(prevMonth.getUTCFullYear(), prevMonth.getUTCMonth(), 1, 0, 0, 0, 0, safeTz);
      const end = createBoundaryISO(year, month, 1, 0, 0, 0, 0, safeTz);
      return { start, end };
    }
    case "this_quarter": {
      const qStartMonth = Math.floor(month / 3) * 3;
      const start = createBoundaryISO(year, qStartMonth, 1, 0, 0, 0, 0, safeTz);
      const nextQuarter = new Date(Date.UTC(year, qStartMonth + 3, 1));
      const end = createBoundaryISO(nextQuarter.getUTCFullYear(), nextQuarter.getUTCMonth(), 1, 0, 0, 0, 0, safeTz);
      return { start, end };
    }
    case "this_year": {
      const start = createBoundaryISO(year, 0, 1, 0, 0, 0, 0, safeTz);
      const end = createBoundaryISO(year + 1, 0, 1, 0, 0, 0, 0, safeTz);
      return { start, end };
    }
    case "custom":
    default: {
      const start = createBoundaryISO(year, month, day, 0, 0, 0, 0, safeTz);
      const end = createBoundaryISO(tomorrow.year, tomorrow.month, tomorrow.day, 0, 0, 0, 0, safeTz);
      return { start, end };
    }
  }
}

/**
 * Validates whether a single filter criterion is well-formed before sending to backend.
 */
export function validateFilterCriterion(criterion: FilterCriterion): {
  isValid: boolean;
  error?: string;
} {
  if (!criterion.column || typeof criterion.column !== "string" || !criterion.column.trim()) {
    return { isValid: false, error: "Column name is required" };
  }

  if (!ALLOWED_OPERATORS.includes(criterion.operator)) {
    return { isValid: false, error: `Invalid operator: ${criterion.operator}` };
  }

  if (criterion.operator === "in") {
    if (!Array.isArray(criterion.value)) {
      return { isValid: false, error: "Operator 'in' requires an array of values" };
    }
  } else if (criterion.operator === "between") {
    if (!Array.isArray(criterion.value) || criterion.value.length !== 2) {
      return { isValid: false, error: "Operator 'between' requires a 2-element array [min, max]" };
    }
    const [low, high] = criterion.value;
    if (typeof low === "number" && typeof high === "number" && low > high) {
      return { isValid: false, error: "Lower bound cannot exceed upper bound in 'between' filter" };
    }
  } else {
    if (criterion.value === null || criterion.value === undefined || criterion.value === "") {
      return { isValid: false, error: "Filter value cannot be empty" };
    }
  }

  return { isValid: true };
}

/**
 * Determines whether a global filter criterion is compatible with a widget's data source and query config.
 */
export function checkFilterCompatibility(
  criterion: FilterCriterion,
  widget: Widget,
  knownColumns?: string[]
): { isCompatible: boolean; reason?: string } {
  const qc = widget.query_config || {};
  const targetCol = criterion.column.trim();

  // If we have actual runtime columns from a previous query response, check presence
  if (knownColumns && knownColumns.length > 0) {
    if (knownColumns.includes(targetCol)) {
      return { isCompatible: true };
    }
    return {
      isCompatible: false,
      reason: `Column '${targetCol}' is not present in widget data source`,
    };
  }

  // Fallback: Check explicitly declared dimensions, metrics, or table columns
  const dims = Array.isArray(qc.dimensions) ? qc.dimensions : [];
  const metrics = Array.isArray(qc.metrics) ? qc.metrics.map((m) => m.column) : [];
  const cols = Array.isArray(qc.columns) ? qc.columns : [];

  const declared = new Set([...dims, ...metrics, ...cols]);
  if (declared.size === 0 || declared.has(targetCol)) {
    return { isCompatible: true };
  }

  return {
    isCompatible: false,
    reason: `Column '${targetCol}' does not match configured dimensions or metrics`,
  };
}

/**
 * Merges global dashboard filters and widget-level local filters using safe AND semantics.
 * Filters out invalid or incompatible criteria.
 */
export function mergeWidgetFilters(
  globalFilters: FilterCriterion[],
  localFilters: FilterCriterion[] | undefined,
  widget: Widget,
  knownColumns?: string[]
): Array<{ column: string; operator: string; value: unknown }> {
  const result: Array<{ column: string; operator: string; value: unknown }> = [];

  // 1. Process Global Filters
  for (const gf of globalFilters) {
    if (gf.enabled === false) continue;
    const valCheck = validateFilterCriterion(gf);
    if (!valCheck.isValid) continue;

    const compat = checkFilterCompatibility(gf, widget, knownColumns);
    if (!compat.isCompatible) continue;

    result.push({
      column: gf.column,
      operator: gf.operator,
      value: gf.value,
    });
  }

  // 2. Process Widget Local Filters (Local filters always apply to the local widget)
  const locals = localFilters || widget.query_config?.local_filters || [];
  for (const lf of locals) {
    if (lf.enabled === false) continue;
    const valCheck = validateFilterCriterion(lf);
    if (!valCheck.isValid) continue;

    result.push({
      column: lf.column,
      operator: lf.operator,
      value: lf.value,
    });
  }

  return result;
}
