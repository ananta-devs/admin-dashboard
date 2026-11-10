import { describe, it, expect } from "vitest";
import {
  calculateDatePresetRange,
  getDatePartsInTimezone,
  validateFilterCriterion,
  checkFilterCompatibility,
  mergeWidgetFilters,
  DATE_PRESETS,
} from "../components/dashboard/filter-utils";
import { FilterCriterion, Widget } from "../components/dashboard/dashboard-types";

describe("Phase 9 Gate 2: Dashboard Filter Utilities & Timezone Engine", () => {
  const refDate = new Date("2026-11-02T12:00:00.000Z");

  it("calculates date parts accurately in various timezones", () => {
    const utcParts = getDatePartsInTimezone(refDate, "UTC");
    expect(utcParts.year).toBe(2026);
    expect(utcParts.month).toBe(10); // November (0-based)
    expect(utcParts.day).toBe(2);

    const tokyoParts = getDatePartsInTimezone(refDate, "Asia/Tokyo");
    expect(tokyoParts.year).toBe(2026);
    expect(tokyoParts.month).toBe(10);
    expect(tokyoParts.day).toBe(2); // 21:00 in Tokyo
  });

  it("calculates start and end ranges for all 10 date presets", () => {
    for (const preset of DATE_PRESETS) {
      const range = calculateDatePresetRange(preset.id, "UTC", refDate);
      expect(range.start).toBeDefined();
      expect(range.end).toBeDefined();
      expect(new Date(range.start).getTime()).toBeLessThanOrEqual(
        new Date(range.end).getTime()
      );
    }
  });

  it("calculates 'today' boundary correctly using half-open [start, end) semantics", () => {
    const range = calculateDatePresetRange("today", "UTC", refDate);
    expect(range.start).toBe("2026-11-02T00:00:00.000Z");
    expect(range.end).toBe("2026-11-03T00:00:00.000Z");
  });

  it("calculates 'yesterday' boundary correctly using half-open [start, end) semantics", () => {
    const range = calculateDatePresetRange("yesterday", "UTC", refDate);
    expect(range.start).toBe("2026-11-01T00:00:00.000Z");
    expect(range.end).toBe("2026-11-02T00:00:00.000Z");
  });

  it("calculates 'last_7_days' boundary correctly using half-open [start, end) semantics", () => {
    const range = calculateDatePresetRange("last_7_days", "UTC", refDate);
    expect(range.start).toBe("2026-10-27T00:00:00.000Z");
    expect(range.end).toBe("2026-11-03T00:00:00.000Z");
  });

  it("calculates 'this_month' boundary correctly using half-open [start, end) semantics", () => {
    const range = calculateDatePresetRange("this_month", "UTC", refDate);
    expect(range.start).toBe("2026-11-01T00:00:00.000Z");
    expect(range.end).toBe("2026-12-01T00:00:00.000Z");
  });

  it("calculates 'this_quarter' (Q4) boundary correctly using half-open [start, end) semantics", () => {
    const range = calculateDatePresetRange("this_quarter", "UTC", refDate);
    expect(range.start).toBe("2026-10-01T00:00:00.000Z");
    expect(range.end).toBe("2027-01-01T00:00:00.000Z");
  });

  it("calculates 'this_year' boundary correctly using half-open [start, end) semantics", () => {
    const range = calculateDatePresetRange("this_year", "UTC", refDate);
    expect(range.start).toBe("2026-01-01T00:00:00.000Z");
    expect(range.end).toBe("2027-01-01T00:00:00.000Z");
  });

  describe("DEF-01: Timezone-Specific Instant Boundaries Matrix", () => {
    it("computes accurate UTC instants for Asia/Kolkata (UTC+5:30)", () => {
      const range = calculateDatePresetRange("today", "Asia/Kolkata", refDate);
      // Midnight Nov 2 in Kolkata is Nov 1 18:30 UTC; midnight Nov 3 is Nov 2 18:30 UTC
      expect(range.start).toBe("2026-11-01T18:30:00.000Z");
      expect(range.end).toBe("2026-11-02T18:30:00.000Z");
    });

    it("computes accurate UTC instants for America/New_York (EST, UTC-5)", () => {
      const range = calculateDatePresetRange("today", "America/New_York", refDate);
      // Midnight Nov 2 in New York is Nov 2 05:00 UTC; midnight Nov 3 is Nov 3 05:00 UTC
      expect(range.start).toBe("2026-11-02T05:00:00.000Z");
      expect(range.end).toBe("2026-11-03T05:00:00.000Z");
    });

    it("computes accurate UTC instants for Asia/Tokyo (UTC+9)", () => {
      const range = calculateDatePresetRange("today", "Asia/Tokyo", refDate);
      // Midnight Nov 2 in Tokyo is Nov 1 15:00 UTC; midnight Nov 3 is Nov 2 15:00 UTC
      expect(range.start).toBe("2026-11-01T15:00:00.000Z");
      expect(range.end).toBe("2026-11-02T15:00:00.000Z");
    });

    it("computes accurate UTC instants for Europe/London (GMT/BST)", () => {
      const range = calculateDatePresetRange("today", "Europe/London", refDate);
      expect(range.start).toBe("2026-11-02T00:00:00.000Z");
      expect(range.end).toBe("2026-11-03T00:00:00.000Z");
    });

    it("handles DST Fall-Back in America/New_York (2026-11-01)", () => {
      const dstFallBackRef = new Date("2026-11-01T14:00:00.000Z");
      const range = calculateDatePresetRange("today", "America/New_York", dstFallBackRef);
      // Midnight Nov 1 in New York was EDT (UTC-4)
      expect(range.start).toBe("2026-11-01T04:00:00.000Z");
      // Midnight Nov 2 in New York is EST (UTC-5)
      expect(range.end).toBe("2026-11-02T05:00:00.000Z");
    });

    it("handles DST Spring-Forward in America/New_York (2026-03-08)", () => {
      const dstSpringForwardRef = new Date("2026-03-08T14:00:00.000Z");
      const range = calculateDatePresetRange("today", "America/New_York", dstSpringForwardRef);
      // Midnight March 8 in New York was EST (UTC-5)
      expect(range.start).toBe("2026-03-08T05:00:00.000Z");
      // Midnight March 9 in New York is EDT (UTC-4)
      expect(range.end).toBe("2026-03-09T04:00:00.000Z");
    });

    it("handles Leap-Year leap day accurately (2024-02-29)", () => {
      const leapRef = new Date("2024-02-29T12:00:00.000Z");
      const range = calculateDatePresetRange("this_month", "UTC", leapRef);
      expect(range.start).toBe("2024-02-01T00:00:00.000Z");
      expect(range.end).toBe("2024-03-01T00:00:00.000Z");
    });

    it("handles Year Transition accurately (2026-12-31 to 2027-01-01)", () => {
      const yearEndRef = new Date("2026-12-31T20:00:00.000Z");
      const range = calculateDatePresetRange("today", "UTC", yearEndRef);
      expect(range.start).toBe("2026-12-31T00:00:00.000Z");
      expect(range.end).toBe("2027-01-01T00:00:00.000Z");
    });
  });

  describe("validateFilterCriterion", () => {
    it("validates well-formed criteria", () => {
      const valid: FilterCriterion = {
        id: "1",
        column: "Region",
        operator: "eq",
        value: "East",
      };
      expect(validateFilterCriterion(valid).isValid).toBe(true);
    });

    it("rejects missing column name", () => {
      const invalid = { id: "1", column: "", operator: "eq", value: "East" } as FilterCriterion;
      const res = validateFilterCriterion(invalid);
      expect(res.isValid).toBe(false);
      expect(res.error).toContain("Column name is required");
    });

    it("rejects invalid operator", () => {
      const invalid = { id: "1", column: "Sales", operator: "DROP", value: 100 } as unknown as FilterCriterion;
      const res = validateFilterCriterion(invalid);
      expect(res.isValid).toBe(false);
      expect(res.error).toContain("Invalid operator");
    });

    it("validates 'in' operator requires array", () => {
      const invalid = { id: "1", column: "Status", operator: "in", value: "active" } as unknown as FilterCriterion;
      expect(validateFilterCriterion(invalid).isValid).toBe(false);

      const valid = { id: "1", column: "Status", operator: "in", value: ["active", "pending"] } as FilterCriterion;
      expect(validateFilterCriterion(valid).isValid).toBe(true);
    });

    it("validates 'between' operator requires 2-element array with min <= max", () => {
      const notArray = { id: "1", column: "Price", operator: "between", value: 50 } as unknown as FilterCriterion;
      expect(validateFilterCriterion(notArray).isValid).toBe(false);

      const badLength = { id: "1", column: "Price", operator: "between", value: [10] } as unknown as FilterCriterion;
      expect(validateFilterCriterion(badLength).isValid).toBe(false);

      const reversedBounds = { id: "1", column: "Price", operator: "between", value: [100, 20] } as unknown as FilterCriterion;
      expect(validateFilterCriterion(reversedBounds).isValid).toBe(false);

      const valid = { id: "1", column: "Price", operator: "between", value: [20, 100] } as FilterCriterion;
      expect(validateFilterCriterion(valid).isValid).toBe(true);
    });
  });

  describe("checkFilterCompatibility & mergeWidgetFilters", () => {
    const mockWidget: Widget = {
      id: "w-1",
      dashboard_id: "d-1",
      title: "Category Sales",
      widget_type: "bar",
      source_type: "sheet",
      source_id: "s-1",
      query_config: {
        dimensions: ["Category"],
        metrics: [{ column: "Sales", agg: "sum" }],
        local_filters: [
          { id: "local-1", column: "Sales", operator: "gt", value: 500, enabled: true },
        ],
      },
      position_x: 0,
      position_y: 0,
      width: 6,
      height: 4,
      sort_order: 0,
    };

    it("identifies compatible filter when column is in declared dimensions", () => {
      const crit: FilterCriterion = { id: "c1", column: "Category", operator: "eq", value: "Electronics" };
      const compat = checkFilterCompatibility(crit, mockWidget);
      expect(compat.isCompatible).toBe(true);
    });

    it("identifies incompatible filter when column does not exist in widget query config", () => {
      const crit: FilterCriterion = { id: "c2", column: "NonExistent", operator: "eq", value: "X" };
      const compat = checkFilterCompatibility(crit, mockWidget);
      expect(compat.isCompatible).toBe(false);
      expect(compat.reason).toBeDefined();
    });

    it("identifies compatibility from known runtime columns", () => {
      const crit: FilterCriterion = { id: "c3", column: "Region", operator: "eq", value: "East" };
      // Not in declared query_config dimensions, but present in runtime columns:
      const compat = checkFilterCompatibility(crit, mockWidget, ["Category", "Sales", "Region", "Date"]);
      expect(compat.isCompatible).toBe(true);
    });

    it("merges global and local filters using safe AND semantics", () => {
      const globalFilters: FilterCriterion[] = [
        { id: "g1", column: "Category", operator: "eq", value: "Electronics", enabled: true },
        { id: "g2", column: "UnknownCol", operator: "eq", value: "Val", enabled: true }, // incompatible
        { id: "g3", column: "Category", operator: "neq", value: "Discontinued", enabled: false }, // disabled
      ];

      const merged = mergeWidgetFilters(globalFilters, mockWidget.query_config.local_filters, mockWidget);
      expect(merged).toHaveLength(2);
      expect(merged[0]).toEqual({
        column: "Category",
        operator: "eq",
        value: "Electronics",
      });
      expect(merged[1]).toEqual({
        column: "Sales",
        operator: "gt",
        value: 500,
      });
    });
  });
});
