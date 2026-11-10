import { describe, it, expect } from "vitest";
import {
  isFiniteNumeric,
  safeNumber,
  formatMetricValue,
  buildChartOptions,
} from "../components/charts/chart-options-builder";
import {
  LIGHT_THEME_TOKENS,
  DARK_THEME_TOKENS,
  getChartThemeTokens,
} from "../components/charts/chart-theme";
import type {
  ChartConfigSpec,
  TabularDataPayload,
} from "../components/charts/chart-types";

interface CustomAxisOption {
  type?: string;
  data?: unknown[];
  name?: string;
}

interface CustomSeriesOption {
  name?: string;
  type?: string;
  stack?: string;
  smooth?: boolean;
  yAxisIndex?: number;
  data?: unknown[];
  radius?: unknown;
  areaStyle?: { opacity?: number };
}

describe("Chart Numeric Validation and Safety", () => {
  it("validates finite numeric values correctly", () => {
    expect(isFiniteNumeric(42)).toBe(true);
    expect(isFiniteNumeric(0)).toBe(true);
    expect(isFiniteNumeric(-12.34)).toBe(true);
    expect(isFiniteNumeric("123")).toBe(true);
    expect(isFiniteNumeric("99.99")).toBe(true);

    expect(isFiniteNumeric(NaN)).toBe(false);
    expect(isFiniteNumeric(Infinity)).toBe(false);
    expect(isFiniteNumeric(-Infinity)).toBe(false);
    expect(isFiniteNumeric("")).toBe(false);
    expect(isFiniteNumeric("   ")).toBe(false);
    expect(isFiniteNumeric("abc")).toBe(false);
    expect(isFiniteNumeric(null)).toBe(false);
    expect(isFiniteNumeric(undefined)).toBe(false);
    expect(isFiniteNumeric({})).toBe(false);
  });

  it("safely coerces numbers and handles non-numeric inputs", () => {
    expect(safeNumber(100)).toBe(100);
    expect(safeNumber("250.5")).toBe(250.5);
    expect(safeNumber(0)).toBe(0);
    expect(safeNumber(NaN)).toBeNull();
    expect(safeNumber(Infinity)).toBeNull();
    expect(safeNumber(null)).toBeNull();
    expect(safeNumber(undefined)).toBeNull();
    expect(safeNumber("not_a_number")).toBeNull();
  });
});

describe("Declarative Formatters (Intl.NumberFormat with zero eval)", () => {
  it("formats standard numbers with default formatter", () => {
    const formatted = formatMetricValue(1234.56, "number");
    expect(formatted).toBe("1,234.56");
  });

  it("formats compact notation", () => {
    expect(formatMetricValue(1200000, "number_compact")).toBe("1.2M");
    expect(formatMetricValue(45000, "number_compact")).toBe("45K");
  });

  it("formats percentages accurately", () => {
    expect(formatMetricValue(0.155, "percentage")).toBe("15.5%");
    // Handles 0-100 scale gracefully
    expect(formatMetricValue(75, "percentage")).toBe("75%");
  });

  it("formats currency in USD and standard locale", () => {
    const formatted = formatMetricValue(1234.5, "currency_usd");
    expect(formatted).toBe("$1,234.50");

    const localFormatted = formatMetricValue(500, "currency_local");
    expect(localFormatted).toContain("500.00");
  });

  it("formats integer and fixed decimal_2", () => {
    expect(formatMetricValue(1234.67, "integer")).toBe("1,235");
    expect(formatMetricValue(1234, "decimal_2")).toBe("1,234.00");
  });

  it("handles null, undefined, empty, and non-numeric values safely", () => {
    expect(formatMetricValue(null)).toBe("—");
    expect(formatMetricValue(undefined)).toBe("—");
    expect(formatMetricValue("")).toBe("—");
    expect(formatMetricValue("arbitrary_string")).toBe("arbitrary_string");
  });
});

describe("Chart Theme System", () => {
  it("provides distinct light and dark tokens", () => {
    const light = getChartThemeTokens(false);
    const dark = getChartThemeTokens(true);

    expect(light.mode).toBe("light");
    expect(dark.mode).toBe("dark");
    expect(light.surface).toBe("#FFFFFF");
    expect(dark.surface).toBe("#111827");
    expect(light.palette.length).toBeGreaterThanOrEqual(6);
    expect(dark.palette.length).toBeGreaterThanOrEqual(6);
    expect(light.palette).toEqual(LIGHT_THEME_TOKENS.palette);
    expect(dark.palette).toEqual(DARK_THEME_TOKENS.palette);
  });
});

describe("buildChartOptions Configuration Engine", () => {
  const sampleData: TabularDataPayload = {
    columns: ["region", "revenue", "profit"],
    data: [
      { region: "North", revenue: 50000, profit: 12000 },
      { region: "South", revenue: 35000, profit: 8000 },
      { region: "East", revenue: 42000, profit: 11000 },
      { region: "West", revenue: 61000, profit: 15000 },
    ],
    total_rows: 4,
    summary: { total_revenue: 188000 },
  };

  it("builds a standard categorical Bar chart", () => {
    const spec: ChartConfigSpec = {
      chart_type: "bar",
      dimensions: ["region"],
      metrics: ["revenue", "profit"],
      formatter: "number_compact",
    };

    const res = buildChartOptions(spec, sampleData, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(true);
    expect(res.option).toBeDefined();

    const opt = res.option!;
    const series = opt.series as CustomSeriesOption[];
    const xAxis = opt.xAxis as CustomAxisOption;
    const yAxis = opt.yAxis as CustomAxisOption;

    expect(Array.isArray(series)).toBe(true);
    expect(series.length).toBe(2);
    expect(xAxis.type).toBe("category");
    expect(xAxis.data).toEqual(["North", "South", "East", "West"]);
    expect(yAxis.type).toBe("value");
  });

  it("builds a Stacked Bar chart", () => {
    const spec: ChartConfigSpec = {
      chart_type: "stacked_bar",
      dimensions: ["region"],
      metrics: ["revenue", "profit"],
    };

    const res = buildChartOptions(spec, sampleData, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(true);
    const series = res.option!.series as CustomSeriesOption[];
    expect(series[0].stack).toBe("total");
    expect(series[1].stack).toBe("total");
  });

  it("builds a Horizontal Bar chart with swapped category and value axes", () => {
    const spec: ChartConfigSpec = {
      chart_type: "horizontal_bar",
      dimensions: ["region"],
      metrics: ["revenue"],
    };

    const res = buildChartOptions(spec, sampleData, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(true);
    const opt = res.option!;
    const xAxis = opt.xAxis as CustomAxisOption;
    const yAxis = opt.yAxis as CustomAxisOption;

    expect(xAxis.type).toBe("value");
    expect(yAxis.type).toBe("category");
    expect(yAxis.data).toEqual(["North", "South", "East", "West"]);
  });

  it("builds a Line chart with smooth curves", () => {
    const spec: ChartConfigSpec = {
      chart_type: "line",
      dimensions: ["region"],
      metrics: ["revenue"],
      smooth: true,
    };

    const res = buildChartOptions(spec, sampleData, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(true);
    const series = res.option!.series as CustomSeriesOption[];
    expect(series[0].type).toBe("line");
    expect(series[0].smooth).toBe(true);
  });

  it("builds a Multi-Axis Line chart with dual Y-axes", () => {
    const spec: ChartConfigSpec = {
      chart_type: "multi_axis_line",
      dimensions: ["region"],
      metrics: [
        { column: "revenue", label: "Revenue ($)", axis_index: 0 },
        { column: "profit", label: "Profit ($)", axis_index: 1 },
      ],
      y_axis_label: "Revenue Axis",
      y2_axis_label: "Profit Axis",
    };

    const res = buildChartOptions(spec, sampleData, DARK_THEME_TOKENS);
    expect(res.isValid).toBe(true);
    const opt = res.option!;
    const yAxes = opt.yAxis as CustomAxisOption[];
    expect(Array.isArray(yAxes)).toBe(true);
    expect(yAxes.length).toBe(2);
    expect(yAxes[0].name).toBe("Revenue Axis");
    expect(yAxes[1].name).toBe("Profit Axis");

    const series = opt.series as CustomSeriesOption[];
    expect(series[0].yAxisIndex).toBe(0);
    expect(series[1].yAxisIndex).toBe(1);
  });

  it("builds a Stacked Area chart with gradient fill", () => {
    const spec: ChartConfigSpec = {
      chart_type: "stacked_area",
      dimensions: ["region"],
      metrics: ["revenue", "profit"],
    };

    const res = buildChartOptions(spec, sampleData, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(true);
    const series = res.option!.series as CustomSeriesOption[];
    expect(series[0].stack).toBe("total");
    expect(series[0].areaStyle).toBeDefined();
    expect(series[0].areaStyle?.opacity).toBe(0.35);
  });

  it("builds a Pie chart and Donut chart with proper inner radius", () => {
    const pieSpec: ChartConfigSpec = {
      chart_type: "pie",
      dimensions: ["region"],
      metrics: ["revenue"],
    };
    const pieRes = buildChartOptions(pieSpec, sampleData, LIGHT_THEME_TOKENS);
    expect(pieRes.isValid).toBe(true);
    const pieSeries = pieRes.option!.series as CustomSeriesOption[];
    expect(pieSeries[0].type).toBe("pie");
    expect(pieSeries[0].radius).toBe("72%");

    const donutSpec: ChartConfigSpec = {
      chart_type: "donut",
      dimensions: ["region"],
      metrics: ["revenue"],
    };
    const donutRes = buildChartOptions(donutSpec, sampleData, LIGHT_THEME_TOKENS);
    expect(donutRes.isValid).toBe(true);
    const donutSeries = donutRes.option!.series as CustomSeriesOption[];
    expect(donutSeries[0].radius).toEqual(["42%", "72%"]);
  });

  it("builds a Scatter chart with 2-variable coordinates", () => {
    const spec: ChartConfigSpec = {
      chart_type: "scatter",
      dimensions: ["region"],
      metrics: ["revenue", "profit"],
    };

    const res = buildChartOptions(spec, sampleData, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(true);
    const opt = res.option!;
    const xAxis = opt.xAxis as CustomAxisOption;
    const yAxis = opt.yAxis as CustomAxisOption;
    const series = opt.series as CustomSeriesOption[];

    expect(xAxis.type).toBe("value");
    expect(yAxis.type).toBe("value");
    expect(series[0].type).toBe("scatter");
    expect(series[0].data).toEqual([
      [50000, 12000, "North"],
      [35000, 8000, "South"],
      [42000, 11000, "East"],
      [61000, 15000, "West"],
    ]);
  });

  it("builds a Heatmap Matrix with VisualMap component", () => {
    const heatmapPayload: TabularDataPayload = {
      columns: ["day", "hour", "activity"],
      data: [
        { day: "Mon", hour: "10am", activity: 50 },
        { day: "Mon", hour: "11am", activity: 75 },
        { day: "Tue", hour: "10am", activity: 30 },
        { day: "Tue", hour: "11am", activity: 90 },
      ],
      total_rows: 4,
    };

    const spec: ChartConfigSpec = {
      chart_type: "heatmap",
      dimensions: ["day", "hour"],
      metrics: ["activity"],
    };

    const res = buildChartOptions(spec, heatmapPayload, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(true);
    const opt = res.option!;
    const xAxis = opt.xAxis as CustomAxisOption;
    const yAxis = opt.yAxis as CustomAxisOption;
    const series = opt.series as CustomSeriesOption[];

    expect(xAxis.type).toBe("category");
    expect(yAxis.type).toBe("category");
    expect(opt.visualMap).toBeDefined();
    expect(series[0].type).toBe("heatmap");
  });

  it("validates KPI card configuration", () => {
    const spec: ChartConfigSpec = {
      chart_type: "kpi_card",
      dimensions: ["region"],
      metrics: ["revenue"],
      kpi_target: 60000,
    };

    const res = buildChartOptions(spec, sampleData, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(true);
  });

  it("gracefully handles empty, null, or undefined data payloads", () => {
    const spec: ChartConfigSpec = { chart_type: "bar" };

    const nullRes = buildChartOptions(spec, null, LIGHT_THEME_TOKENS);
    expect(nullRes.isValid).toBe(false);
    expect(nullRes.emptyReason).toContain("No data available");

    const emptyRes = buildChartOptions(spec, { columns: [], data: [], total_rows: 0 }, LIGHT_THEME_TOKENS);
    expect(emptyRes.isValid).toBe(false);
    expect(emptyRes.emptyReason).toContain("No data available");
  });

  it("auto-discovers dimensions and metrics when not explicitly passed", () => {
    const spec: ChartConfigSpec = { chart_type: "bar" };
    const res = buildChartOptions(spec, sampleData, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(true);
    expect(res.option).toBeDefined();
    const xAxis = res.option!.xAxis as CustomAxisOption;
    expect(xAxis.data).toEqual(["North", "South", "East", "West"]);
  });

  it("handles null and NaN values inside data rows gracefully", () => {
    const dirtyData: TabularDataPayload = {
      columns: ["category", "value"],
      data: [
        { category: "A", value: 100 },
        { category: "B", value: null },
        { category: "C", value: NaN },
        { category: "D", value: "invalid" },
      ],
      total_rows: 4,
    };

    const spec: ChartConfigSpec = {
      chart_type: "bar",
      dimensions: ["category"],
      metrics: ["value"],
    };

    const res = buildChartOptions(spec, dirtyData, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(true);
    const series = res.option!.series as CustomSeriesOption[];
    // Non-numeric or null values are safely coerced to 0 without crashing
    expect(series[0].data).toEqual([100, 0, 0, 0]);
  });

  it("supports selectable grouped vs stacked behavior for Bar charts", () => {
    // 1. Grouped Bar: series have NO stack property, rendering side-by-side
    const groupedSpec: ChartConfigSpec = {
      chart_type: "bar",
      bar_mode: "grouped",
      dimensions: ["region"],
      metrics: ["revenue", "profit"],
    };
    const groupedRes = buildChartOptions(groupedSpec, sampleData, LIGHT_THEME_TOKENS);
    expect(groupedRes.isValid).toBe(true);
    const groupedSeries = groupedRes.option!.series as CustomSeriesOption[];
    expect(groupedSeries.length).toBe(2);
    expect(groupedSeries[0].stack).toBeUndefined();
    expect(groupedSeries[1].stack).toBeUndefined();

    // 2. Stacked Bar: series have stack: 'total'
    const stackedSpec: ChartConfigSpec = {
      chart_type: "bar",
      bar_mode: "stacked",
      dimensions: ["region"],
      metrics: ["revenue", "profit"],
    };
    const stackedRes = buildChartOptions(stackedSpec, sampleData, LIGHT_THEME_TOKENS);
    expect(stackedRes.isValid).toBe(true);
    const stackedSeries = stackedRes.option!.series as CustomSeriesOption[];
    expect(stackedSeries[0].stack).toBe("total");
    expect(stackedSeries[1].stack).toBe("total");
  });

  it("verifies Standard Line defaults to conventional non-smoothed straight lines", () => {
    const stdLineSpec: ChartConfigSpec = {
      chart_type: "line",
      dimensions: ["region"],
      metrics: ["revenue"],
    };
    const res = buildChartOptions(stdLineSpec, sampleData, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(true);
    const series = res.option!.series as CustomSeriesOption[];
    expect(series[0].smooth).toBe(false);
  });

  it("verifies Smooth Multi-Axis Line defaults to smooth curves and multiple Y axes", () => {
    const smoothSpec: ChartConfigSpec = {
      chart_type: "smooth_multi_axis_line",
      dimensions: ["region"],
      metrics: [
        { column: "revenue", label: "Revenue", axis_index: 0 },
        { column: "profit", label: "Profit Rate", axis_index: 1 },
      ],
      y_axis_label: "USD",
      y2_axis_label: "Ratio",
    };
    const res = buildChartOptions(smoothSpec, sampleData, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(true);
    const opt = res.option!;
    const yAxes = opt.yAxis as CustomAxisOption[];
    expect(Array.isArray(yAxes)).toBe(true);
    expect(yAxes.length).toBe(2);
    const series = opt.series as CustomSeriesOption[];
    expect(series[0].smooth).toBe(true);
    expect(series[0].yAxisIndex).toBe(0);
    expect(series[1].smooth).toBe(true);
    expect(series[1].yAxisIndex).toBe(1);
  });

  it("verifies Pie and Donut variants within the Pie/Donut chart family", () => {
    // Pie variant (full disk)
    const pieSpec: ChartConfigSpec = {
      chart_type: "pie",
      pie_variant: "pie",
      dimensions: ["region"],
      metrics: ["revenue"],
    };
    const pieRes = buildChartOptions(pieSpec, sampleData, LIGHT_THEME_TOKENS);
    expect(pieRes.isValid).toBe(true);
    const pieSeries = pieRes.option!.series as CustomSeriesOption[];
    expect(pieSeries[0].radius).toBe("72%");

    // Donut variant (inner ring)
    const donutSpec: ChartConfigSpec = {
      chart_type: "pie",
      pie_variant: "donut",
      dimensions: ["region"],
      metrics: ["revenue"],
    };
    const donutRes = buildChartOptions(donutSpec, sampleData, LIGHT_THEME_TOKENS);
    expect(donutRes.isValid).toBe(true);
    const donutSeries = donutRes.option!.series as CustomSeriesOption[];
    expect(donutSeries[0].radius).toEqual(["42%", "72%"]);
  });
});

describe("Backend WidgetDataQueryResponse API Contract Compatibility", () => {
  it("consumes real Polars responses with multiple categorical dimensions", () => {
    const multiDimResponse: TabularDataPayload = {
      columns: ["region", "channel", "sum_revenue"],
      data: [
        { region: "North", channel: "Online", sum_revenue: 45000.5 },
        { region: "North", channel: "Direct", sum_revenue: 12000.0 },
        { region: "South", channel: "Online", sum_revenue: 38000.25 },
        { region: "South", channel: "Direct", sum_revenue: 15500.0 },
      ],
      total_rows: 4,
      summary: { total_revenue: 110500.75 },
    };

    const spec: ChartConfigSpec = {
      chart_type: "bar",
      dimensions: ["region"],
      metrics: ["sum_revenue"],
    };

    const res = buildChartOptions(spec, multiDimResponse, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(true);
    const opt = res.option!;
    const xAxis = opt.xAxis as CustomAxisOption;
    expect(xAxis.data).toEqual(["North", "North", "South", "South"]);
    const series = opt.series as CustomSeriesOption[];
    expect(series[0].data).toEqual([45000.5, 12000, 38000.25, 15500]);
  });

  it("consumes real Polars responses with multiple metric series", () => {
    const multiMetricResponse: TabularDataPayload = {
      columns: ["quarter", "revenue", "gross_margin", "units_sold"],
      data: [
        { quarter: "Q1", revenue: 100000, gross_margin: 32000, units_sold: 450 },
        { quarter: "Q2", revenue: 125000, gross_margin: 41000, units_sold: 520 },
      ],
      total_rows: 2,
      summary: {},
    };

    const spec: ChartConfigSpec = {
      chart_type: "bar",
      bar_mode: "grouped",
      dimensions: ["quarter"],
      metrics: [
        { column: "revenue", label: "Revenue" },
        { column: "gross_margin", label: "Margin" },
        { column: "units_sold", label: "Units" },
      ],
    };

    const res = buildChartOptions(spec, multiMetricResponse, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(true);
    const series = res.option!.series as CustomSeriesOption[];
    expect(series.length).toBe(3);
    expect(series[0].name).toBe("Revenue");
    expect(series[1].name).toBe("Margin");
    expect(series[2].name).toBe("Units");
  });

  it("consumes ISO date-based dimensions cleanly", () => {
    const dateResponse: TabularDataPayload = {
      columns: ["date", "active_users"],
      data: [
        { date: "2026-01-01T00:00:00Z", active_users: 1250 },
        { date: "2026-01-02T00:00:00Z", active_users: 1340 },
        { date: "2026-01-03T00:00:00Z", active_users: 1410 },
      ],
      total_rows: 3,
    };

    const spec: ChartConfigSpec = {
      chart_type: "line",
      dimensions: ["date"],
      metrics: ["active_users"],
    };

    const res = buildChartOptions(spec, dateResponse, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(true);
    const opt = res.option!;
    const xAxis = opt.xAxis as CustomAxisOption;
    expect(xAxis.data).toEqual([
      "2026-01-01T00:00:00Z",
      "2026-01-02T00:00:00Z",
      "2026-01-03T00:00:00Z",
    ]);
  });

  it("handles null values across rows without throwing or generating bogus coordinates", () => {
    const nullRowResponse: TabularDataPayload = {
      columns: ["period", "val_a", "val_b"],
      data: [
        { period: "P1", val_a: 50, val_b: null },
        { period: "P2", val_a: null, val_b: 75 },
        { period: "P3", val_a: undefined, val_b: 90 },
      ],
      total_rows: 3,
    };

    const spec: ChartConfigSpec = {
      chart_type: "bar",
      dimensions: ["period"],
      metrics: ["val_a", "val_b"],
    };

    const res = buildChartOptions(spec, nullRowResponse, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(true);
    const series = res.option!.series as CustomSeriesOption[];
    expect(series[0].data).toEqual([50, 0, 0]);
    expect(series[1].data).toEqual([0, 75, 90]);
  });

  it("handles empty dataset responses cleanly", () => {
    const emptyResponse: TabularDataPayload = {
      columns: ["category", "value"],
      data: [],
      total_rows: 0,
      summary: {},
    };

    const spec: ChartConfigSpec = {
      chart_type: "bar",
      dimensions: ["category"],
      metrics: ["value"],
    };

    const res = buildChartOptions(spec, emptyResponse, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(false);
    expect(res.option).toBeNull();
    expect(res.emptyReason).toContain("No data available");
  });

  it("handles responses where optional summary field is omitted", () => {
    const noSummaryResponse: TabularDataPayload = {
      columns: ["segment", "count"],
      data: [{ segment: "SMB", count: 420 }],
      total_rows: 1,
    };

    const spec: ChartConfigSpec = {
      chart_type: "bar",
      dimensions: ["segment"],
      metrics: ["count"],
    };

    const res = buildChartOptions(spec, noSummaryResponse, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(true);
    expect(res.option).toBeDefined();
  });

  it("parses numeric strings and safely rejects unparseable strings without crashing", () => {
    const mixedTypeResponse: TabularDataPayload = {
      columns: ["label", "metric"],
      data: [
        { label: "ValidInt", metric: 100 },
        { label: "ValidStrFloat", metric: "250.75" },
        { label: "InvalidStr", metric: "N/A" },
        { label: "InfinityVal", metric: Infinity },
      ],
      total_rows: 4,
    };

    const spec: ChartConfigSpec = {
      chart_type: "bar",
      dimensions: ["label"],
      metrics: ["metric"],
    };

    const res = buildChartOptions(spec, mixedTypeResponse, LIGHT_THEME_TOKENS);
    expect(res.isValid).toBe(true);
    const series = res.option!.series as CustomSeriesOption[];
    expect(series[0].data).toEqual([100, 250.75, 0, 0]);
  });
});
