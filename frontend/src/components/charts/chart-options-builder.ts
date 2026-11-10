import type { EChartsOption, SeriesOption } from "echarts";
import type {
  ChartConfigSpec,
  ChartThemeTokens,
  FormatterToken,
  OptionBuilderResult,
  QueryDimensionConfig,
  QueryMetricConfig,
  TabularDataPayload,
} from "./chart-types";

/**
 * Checks if a value is a finite number or can be parsed as a finite number.
 */
export function isFiniteNumeric(val: unknown): val is number {
  if (typeof val === "number") {
    return Number.isFinite(val);
  }
  if (typeof val === "string" && val.trim() !== "") {
    const parsed = Number(val);
    return Number.isFinite(parsed);
  }
  return false;
}

/**
 * Coerce a value safely to a finite number, or null if invalid.
 */
export function safeNumber(val: unknown): number | null {
  if (typeof val === "number" && Number.isFinite(val)) {
    return val;
  }
  if (typeof val === "string" && val.trim() !== "") {
    const parsed = Number(val);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return null;
}

/**
 * Format a number safely using declarative tokens and Intl.NumberFormat.
 * Zero eval or arbitrary code execution.
 */
export function formatMetricValue(
  value: unknown,
  token?: FormatterToken,
  locale = "en-US"
): string {
  if (value === null || value === undefined || value === "") {
    return "—";
  }

  const num = safeNumber(value);
  if (num === null) {
    return String(value);
  }

  try {
    switch (token) {
      case "number_compact":
        return new Intl.NumberFormat(locale, {
          notation: "compact",
          compactDisplay: "short",
          maximumFractionDigits: 1,
        }).format(num);

      case "percentage":
        return new Intl.NumberFormat(locale, {
          style: "percent",
          minimumFractionDigits: 0,
          maximumFractionDigits: 2,
        }).format(num > 1 && num <= 100 ? num / 100 : num);

      case "currency_usd":
        return new Intl.NumberFormat(locale, {
          style: "currency",
          currency: "USD",
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        }).format(num);

      case "currency_local":
        return new Intl.NumberFormat(locale, {
          style: "currency",
          currency: "USD", // Default standard currency
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        }).format(num);

      case "integer":
        return new Intl.NumberFormat(locale, {
          maximumFractionDigits: 0,
        }).format(Math.round(num));

      case "decimal_2":
        return new Intl.NumberFormat(locale, {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        }).format(num);

      case "number":
      default:
        return new Intl.NumberFormat(locale, {
          maximumFractionDigits: 2,
        }).format(num);
    }
  } catch {
    return String(num);
  }
}

/**
 * Extract column name from dimension configuration.
 */
function getDimensionKey(dim: string | QueryDimensionConfig | undefined): string | null {
  if (!dim) return null;
  return typeof dim === "string" ? dim : dim.column;
}

/**
 * Extract column name and metadata from metric configuration.
 */
function getMetricSpec(metric: string | QueryMetricConfig | undefined): {
  key: string;
  label: string;
  formatter?: FormatterToken;
  axisIndex?: number;
} | null {
  if (!metric) return null;
  if (typeof metric === "string") {
    return { key: metric, label: metric };
  }
  return {
    key: metric.column,
    label: metric.label || metric.column,
    formatter: metric.formatter,
    axisIndex: metric.axis_index,
  };
}

/**
 * Builds standard ECharts options based on high-level ChartConfigSpec, TabularDataPayload, and theme tokens.
 */
export function buildChartOptions(
  spec: ChartConfigSpec,
  dataPayload: TabularDataPayload | null | undefined,
  theme: ChartThemeTokens
): OptionBuilderResult {
  if (!dataPayload || !Array.isArray(dataPayload.data) || dataPayload.data.length === 0) {
    return {
      option: null,
      isValid: false,
      emptyReason: "No data available to display.",
    };
  }

  const rows = dataPayload.data;
  const palette = spec.color_palette && spec.color_palette.length > 0
    ? spec.color_palette
    : theme.palette;

  // Extract dimensions and metrics
  const dimSpecs = (spec.dimensions || []).map(getDimensionKey).filter((d): d is string => !!d);
  const metricSpecs = (spec.metrics || []).map(getMetricSpec).filter((m): m is NonNullable<typeof m> => !!m);

  // Fallback dimension and metric discovery if not explicitly passed
  let primaryDim = dimSpecs[0];
  let metrics = metricSpecs;

  if (!primaryDim || metrics.length === 0) {
    const availableCols = dataPayload.columns.length > 0
      ? dataPayload.columns
      : Object.keys(rows[0] || {});

    if (!primaryDim) {
      // Find first non-numeric or string column
      const sample = rows[0] || {};
      const stringCol = availableCols.find((col) => typeof sample[col] === "string");
      primaryDim = stringCol || availableCols[0] || "category";
    }

    if (metrics.length === 0) {
      // Find numeric columns
      const sample = rows[0] || {};
      const numericCols = availableCols.filter(
        (col) => col !== primaryDim && isFiniteNumeric(sample[col])
      );
      if (numericCols.length > 0) {
        metrics = numericCols.map((col) => ({
          key: col,
          label: col.replace(/_/g, " "),
          formatter: spec.formatter,
        }));
      } else {
        const remainingCols = availableCols.filter((col) => col !== primaryDim);
        metrics = (remainingCols.length > 0 ? remainingCols : [primaryDim]).map((col) => ({
          key: col,
          label: col,
          formatter: spec.formatter,
        }));
      }
    }
  }

  // Base chart layout & styling
  const commonGrid = {
    top: spec.title ? 45 : 25,
    right: 25,
    bottom: spec.show_data_zoom ? 60 : 35,
    left: 45,
    containLabel: true,
  };

  const commonTooltip = {
    trigger: "axis" as const,
    backgroundColor: theme.tooltipBackground,
    borderColor: theme.tooltipBorder,
    borderWidth: 1,
    textStyle: { color: theme.tooltipText, fontSize: 12 },
    axisPointer: {
      type: "cross" as const,
      lineStyle: { color: theme.mutedForeground, type: "dashed" as const },
      crossStyle: { color: theme.mutedForeground },
    },
    formatter: (params: unknown) => {
      if (!Array.isArray(params) || params.length === 0) return "";
      const header = `<div style="font-weight:600;margin-bottom:4px;">${params[0].axisValueLabel || params[0].name}</div>`;
      const lines = params.map((item: { marker?: string; seriesName?: string; value?: unknown }) => {
        const rawVal = Array.isArray(item.value) ? item.value[1] : item.value;
        const formatted = formatMetricValue(rawVal, spec.formatter);
        return `<div>${item.marker || "•"} ${item.seriesName}: <span style="font-weight:600">${formatted}</span></div>`;
      });
      return `${header}${lines.join("")}`;
    },
  };

  const categories = rows.map((r, i) => {
    const val = r[primaryDim];
    return val !== undefined && val !== null ? String(val) : `Item ${i + 1}`;
  });

  const baseAxisLine = {
    lineStyle: { color: theme.border },
  };

  const baseAxisLabel = {
    color: theme.mutedForeground,
    fontSize: 11,
  };

  const baseSplitLine = {
    lineStyle: { color: theme.gridLineColor, type: "dashed" as const },
  };

  // Build specific chart options
  switch (spec.chart_type) {
    case "bar":
    case "grouped_bar":
    case "stacked_bar":
    case "horizontal_bar": {
      const isHorizontal = spec.chart_type === "horizontal_bar" || spec.horizontal === true;
      const isStacked =
        spec.bar_mode === "stacked" ||
        spec.chart_type === "stacked_bar" ||
        (spec.bar_mode !== "grouped" && spec.stacked === true);

      const series: SeriesOption[] = metrics.map((m, idx) => ({
        name: m.label,
        type: "bar",
        stack: isStacked ? "total" : undefined,
        data: rows.map((r) => safeNumber(r[m.key]) ?? 0),
        itemStyle: {
          color: palette[idx % palette.length],
          borderRadius: isStacked ? 0 : [4, 4, 0, 0],
        },
      }));

      const categoryAxis = {
        type: "category" as const,
        data: categories,
        axisLine: baseAxisLine,
        axisLabel: baseAxisLabel,
      };

      const valueAxis = {
        type: "value" as const,
        axisLine: baseAxisLine,
        axisLabel: {
          ...baseAxisLabel,
          formatter: (v: number) => formatMetricValue(v, spec.formatter || "number_compact"),
        },
        splitLine: baseSplitLine,
      };

      const option: EChartsOption = {
        color: palette,
        grid: commonGrid,
        tooltip: commonTooltip,
        legend: spec.show_legend !== false && metrics.length > 1 ? {
          textStyle: { color: theme.foreground },
          top: 0,
        } : undefined,
        xAxis: isHorizontal ? valueAxis : categoryAxis,
        yAxis: isHorizontal ? categoryAxis : valueAxis,
        series,
        dataZoom: spec.show_data_zoom ? [{ type: "slider" }, { type: "inside" }] : undefined,
      };

      return { option, isValid: true };
    }

    case "line":
    case "stacked_area": {
      const isArea = spec.chart_type === "stacked_area" || spec.is_area === true;
      const isStacked = spec.chart_type === "stacked_area" || spec.stacked === true;
      const isSmooth = spec.smooth ?? (isArea ? true : false);

      const series: SeriesOption[] = metrics.map((m, idx) => {
        const color = palette[idx % palette.length];
        return {
          name: m.label,
          type: "line",
          stack: isStacked ? "total" : undefined,
          smooth: isSmooth,
          showSymbol: rows.length < 50,
          data: rows.map((r) => safeNumber(r[m.key]) ?? 0),
          itemStyle: { color },
          lineStyle: { width: 2, color },
          areaStyle: isArea
            ? {
                color: {
                  type: "linear",
                  x: 0,
                  y: 0,
                  x2: 0,
                  y2: 1,
                  colorStops: [
                    { offset: 0, color: color },
                    { offset: 1, color: "transparent" },
                  ],
                },
                opacity: 0.35,
              }
            : undefined,
        };
      });

      const option: EChartsOption = {
        color: palette,
        grid: commonGrid,
        tooltip: commonTooltip,
        legend: spec.show_legend !== false && metrics.length > 1 ? {
          textStyle: { color: theme.foreground },
          top: 0,
        } : undefined,
        xAxis: {
          type: "category",
          data: categories,
          axisLine: baseAxisLine,
          axisLabel: baseAxisLabel,
        },
        yAxis: {
          type: "value",
          axisLine: baseAxisLine,
          axisLabel: {
            ...baseAxisLabel,
            formatter: (v: number) => formatMetricValue(v, spec.formatter || "number_compact"),
          },
          splitLine: baseSplitLine,
        },
        series,
        dataZoom: spec.show_data_zoom ? [{ type: "slider" }, { type: "inside" }] : undefined,
      };

      return { option, isValid: true };
    }

    case "smooth_multi_axis_line":
    case "multi_axis_line": {
      const series: SeriesOption[] = metrics.map((m, idx) => {
        const color = palette[idx % palette.length];
        const axisIndex = m.axisIndex ?? (idx > 0 ? 1 : 0);
        return {
          name: m.label,
          type: "line",
          yAxisIndex: axisIndex,
          smooth: spec.smooth ?? true,
          showSymbol: rows.length < 50,
          data: rows.map((r) => safeNumber(r[m.key]) ?? 0),
          itemStyle: { color },
          lineStyle: { width: 2, color },
        };
      });

      const yAxes = [
        {
          type: "value" as const,
          name: spec.y_axis_label || metrics[0]?.label || "",
          nameTextStyle: { color: theme.mutedForeground, fontSize: 10 },
          axisLine: baseAxisLine,
          axisLabel: {
            ...baseAxisLabel,
            formatter: (v: number) => formatMetricValue(v, spec.formatter || "number_compact"),
          },
          splitLine: baseSplitLine,
        },
        {
          type: "value" as const,
          name: spec.y2_axis_label || metrics[1]?.label || "",
          nameTextStyle: { color: theme.mutedForeground, fontSize: 10 },
          axisLine: baseAxisLine,
          axisLabel: {
            ...baseAxisLabel,
            formatter: (v: number) => formatMetricValue(v, "number_compact"),
          },
          splitLine: { show: false },
        },
      ];

      const option: EChartsOption = {
        color: palette,
        grid: commonGrid,
        tooltip: commonTooltip,
        legend: spec.show_legend !== false ? {
          textStyle: { color: theme.foreground },
          top: 0,
        } : undefined,
        xAxis: {
          type: "category",
          data: categories,
          axisLine: baseAxisLine,
          axisLabel: baseAxisLabel,
        },
        yAxis: yAxes,
        series,
        dataZoom: spec.show_data_zoom ? [{ type: "slider" }, { type: "inside" }] : undefined,
      };

      return { option, isValid: true };
    }

    case "pie":
    case "donut": {
      const isDonut =
        spec.pie_variant === "donut" ||
        spec.chart_type === "donut" ||
        spec.is_donut === true;
      const primaryMetric = metrics[0] || { key: "value", label: "Value" };

      const pieData = rows.map((r, i) => ({
        name: r[primaryDim] !== undefined && r[primaryDim] !== null ? String(r[primaryDim]) : `Slice ${i + 1}`,
        value: safeNumber(r[primaryMetric.key]) ?? 0,
      }));

      const option: EChartsOption = {
        color: palette,
        tooltip: {
          trigger: "item",
          backgroundColor: theme.tooltipBackground,
          borderColor: theme.tooltipBorder,
          borderWidth: 1,
          textStyle: { color: theme.tooltipText, fontSize: 12 },
          formatter: (params: unknown) => {
            const p = params as { name: string; value: number; percent: number };
            const formatted = formatMetricValue(p.value, spec.formatter);
            return `<div style="font-weight:600;">${p.name}</div><div>Value: ${formatted} (${p.percent}%)</div>`;
          },
        },
        legend: spec.show_legend !== false ? {
          orient: "horizontal",
          bottom: 0,
          textStyle: { color: theme.foreground, fontSize: 11 },
        } : undefined,
        series: [
          {
            name: primaryMetric.label,
            type: "pie",
            radius: isDonut ? ["42%", "72%"] : "72%",
            center: ["50%", spec.show_legend !== false ? "45%" : "50%"],
            avoidLabelOverlap: true,
            itemStyle: {
              borderRadius: isDonut ? 4 : 0,
              borderColor: theme.surface,
              borderWidth: 2,
            },
            label: {
              show: !isDonut,
              color: theme.foreground,
              formatter: "{b}: {d}%",
            },
            data: pieData,
          },
        ],
      };

      return { option, isValid: true };
    }

    case "scatter": {
      const xMetric = metrics[0] || { key: "x", label: "X" };
      const yMetric = metrics[1] || metrics[0] || { key: "y", label: "Y" };

      const scatterData = rows.map((r) => [
        safeNumber(r[xMetric.key]) ?? 0,
        safeNumber(r[yMetric.key]) ?? 0,
        r[primaryDim] ? String(r[primaryDim]) : "",
      ]);

      const isLargeDataset = rows.length >= 2000;

      const option: EChartsOption = {
        color: palette,
        grid: commonGrid,
        animation: !isLargeDataset,
        tooltip: {
          trigger: "item",
          backgroundColor: theme.tooltipBackground,
          borderColor: theme.tooltipBorder,
          borderWidth: 1,
          textStyle: { color: theme.tooltipText, fontSize: 12 },
          formatter: (params: unknown) => {
            const p = params as { value: [number, number, string] };
            const name = p.value[2] ? `<b>${p.value[2]}</b><br/>` : "";
            const formattedX = formatMetricValue(p.value[0], spec.formatter);
            const formattedY = formatMetricValue(p.value[1], spec.formatter);
            return `${name}${xMetric.label}: ${formattedX}<br/>${yMetric.label}: ${formattedY}`;
          },
        },
        xAxis: {
          type: "value",
          name: xMetric.label,
          axisLine: baseAxisLine,
          axisLabel: {
            ...baseAxisLabel,
            formatter: (v: number) => formatMetricValue(v, "number_compact"),
          },
          splitLine: baseSplitLine,
        },
        yAxis: {
          type: "value",
          name: yMetric.label,
          axisLine: baseAxisLine,
          axisLabel: {
            ...baseAxisLabel,
            formatter: (v: number) => formatMetricValue(v, "number_compact"),
          },
          splitLine: baseSplitLine,
        },
        series: [
          {
            type: "scatter",
            symbolSize: isLargeDataset ? 4 : 12,
            large: isLargeDataset,
            largeThreshold: 2000,
            data: scatterData,
            itemStyle: {
              color: palette[0],
              opacity: 0.8,
            },
          },
        ],
      };

      return { option, isValid: true };
    }

    case "heatmap": {
      // Expect 2 dimensions (x, y) and 1 metric (value)
      const xDim = dimSpecs[0] || "x";
      const yDim = dimSpecs[1] || (dataPayload.columns.length > 1 ? dataPayload.columns[1] : "y");
      const valueMetric = metrics[0] || { key: "value", label: "Value" };

      // Get unique x and y values preserving order
      const xValues = Array.from(new Set(rows.map((r) => String(r[xDim] ?? ""))));
      const yValues = Array.from(new Set(rows.map((r) => String(r[yDim] ?? ""))));

      const heatmapData: [number, number, number][] = [];
      let minVal = Infinity;
      let maxVal = -Infinity;

      rows.forEach((r) => {
        const xIndex = xValues.indexOf(String(r[xDim] ?? ""));
        const yIndex = yValues.indexOf(String(r[yDim] ?? ""));
        const rawVal = safeNumber(r[valueMetric.key]) ?? 0;
        if (xIndex >= 0 && yIndex >= 0) {
          heatmapData.push([xIndex, yIndex, rawVal]);
          if (rawVal < minVal) minVal = rawVal;
          if (rawVal > maxVal) maxVal = rawVal;
        }
      });

      if (minVal === Infinity) minVal = 0;
      if (maxVal === -Infinity) maxVal = 100;

      const option: EChartsOption = {
        grid: {
          ...commonGrid,
          right: 80, // Allow space for visualMap
        },
        tooltip: {
          position: "top",
          backgroundColor: theme.tooltipBackground,
          borderColor: theme.tooltipBorder,
          borderWidth: 1,
          textStyle: { color: theme.tooltipText, fontSize: 12 },
          formatter: (params: unknown) => {
            const p = params as { value: [number, number, number] };
            const xLabel = xValues[p.value[0]] || "";
            const yLabel = yValues[p.value[1]] || "";
            const formatted = formatMetricValue(p.value[2], spec.formatter);
            return `<div><b>${xLabel} / ${yLabel}</b></div><div>${valueMetric.label}: ${formatted}</div>`;
          },
        },
        xAxis: {
          type: "category",
          data: xValues,
          splitArea: { show: true },
          axisLine: baseAxisLine,
          axisLabel: baseAxisLabel,
        },
        yAxis: {
          type: "category",
          data: yValues,
          splitArea: { show: true },
          axisLine: baseAxisLine,
          axisLabel: baseAxisLabel,
        },
        visualMap: {
          min: minVal,
          max: maxVal === minVal ? minVal + 1 : maxVal,
          calculable: true,
          orient: "vertical",
          right: 0,
          top: "center",
          inRange: {
            color: [theme.surface, theme.primary],
          },
          textStyle: { color: theme.mutedForeground, fontSize: 10 },
        },
        series: [
          {
            name: valueMetric.label,
            type: "heatmap",
            data: heatmapData,
            label: {
              show: rows.length < 50,
              formatter: (params: unknown) => {
                const p = params as { value: [number, number, number] };
                return formatMetricValue(p.value[2], "number_compact");
              },
              color: theme.foreground,
              fontSize: 10,
            },
            emphasis: {
              itemStyle: {
                shadowBlur: 10,
                shadowColor: "rgba(0, 0, 0, 0.5)",
              },
            },
          },
        ],
      };

      return { option, isValid: true };
    }

    case "kpi_card": {
      // KPI cards do not render via standard chart canvas, but OptionBuilder validates data presence
      return {
        option: {},
        isValid: true,
      };
    }

    default: {
      return {
        option: null,
        isValid: false,
        emptyReason: `Unsupported visualization type: ${spec.chart_type}`,
      };
    }
  }
}

