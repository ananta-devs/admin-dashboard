"use client";

import * as React from "react";
import * as echarts from "echarts/core";
import {
  BarChart,
  LineChart,
  PieChart,
  ScatterChart,
  HeatmapChart,
} from "echarts/charts";
import {
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent,
  DataZoomComponent,
  VisualMapComponent,
  DatasetComponent,
  AxisPointerComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { useTheme } from "next-themes";
import { cn } from "@/lib/utils";
import { buildChartOptions } from "./chart-options-builder";
import { getChartThemeTokens } from "./chart-theme";
import type { ChartConfigSpec, TabularDataPayload } from "./chart-types";
import { ChartSkeleton } from "./chart-skeleton";
import { KpiCard } from "./kpi-card";

// Register modular ECharts components once
echarts.use([
  BarChart,
  LineChart,
  PieChart,
  ScatterChart,
  HeatmapChart,
  TitleComponent,
  TooltipComponent,
  GridComponent,
  LegendComponent,
  DataZoomComponent,
  VisualMapComponent,
  DatasetComponent,
  AxisPointerComponent,
  CanvasRenderer,
]);

export interface EChartsWrapperProps {
  spec: ChartConfigSpec;
  dataPayload?: TabularDataPayload | null;
  height?: number | string;
  loading?: boolean;
  className?: string;
  onChartClick?: (params: unknown) => void;
}

export function EChartsWrapper({
  spec,
  dataPayload,
  height = 320,
  loading = false,
  className,
  onChartClick,
}: EChartsWrapperProps) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const chartInstanceRef = React.useRef<echarts.ECharts | null>(null);
  const clickHandlerRef = React.useRef<((params: unknown) => void) | undefined>(onChartClick);
  const [initError, setInitError] = React.useState<string | null>(null);

  // Keep latest click handler ref updated without re-triggering chart effects
  React.useEffect(() => {
    clickHandlerRef.current = onChartClick;
  }, [onChartClick]);

  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";

  // Generate chart options unconditionally for standard React hook rules
  const themeTokens = React.useMemo(() => getChartThemeTokens(isDark), [isDark]);
  const builderResult = React.useMemo(
    () => buildChartOptions(spec, dataPayload, themeTokens),
    [spec, dataPayload, themeTokens]
  );

  // Initialize and update ECharts instance
  React.useEffect(() => {
    if (spec.chart_type === "kpi_card") return;
    if (!containerRef.current || !builderResult.isValid || !builderResult.option) {
      return;
    }

    try {
      // Check if instance already exists or was disposed
      let instance = chartInstanceRef.current;
      if (!instance || instance.isDisposed()) {
        const existing = echarts.getInstanceByDom(containerRef.current);
        if (existing && !existing.isDisposed()) {
          instance = existing;
        } else {
          instance = echarts.init(containerRef.current, undefined, {
            renderer: "canvas",
          });
        }
        chartInstanceRef.current = instance;

        // Attach event listener once per instance
        instance.on("click", (params) => {
          if (clickHandlerRef.current) {
            clickHandlerRef.current(params);
          }
        });
      }

      // Apply options with notMerge = true to cleanly update on spec/data/theme change
      instance.setOption(builderResult.option, true);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to initialize chart engine";
      setTimeout(() => {
        setInitError(msg);
      }, 0);
    }
  }, [spec.chart_type, builderResult]);

  // Handle ResizeObserver with debouncing
  React.useEffect(() => {
    if (spec.chart_type === "kpi_card") return;
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === "undefined") return;

    let resizeTimer: ReturnType<typeof setTimeout> | null = null;
    const observer = new ResizeObserver(() => {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (chartInstanceRef.current && !chartInstanceRef.current.isDisposed()) {
          chartInstanceRef.current.resize();
        }
      }, 50);
    });

    observer.observe(container);

    return () => {
      observer.disconnect();
      if (resizeTimer) clearTimeout(resizeTimer);
    };
  }, [spec.chart_type]);

  // Cleanup chart instance on unmount
  React.useEffect(() => {
    return () => {
      if (chartInstanceRef.current && !chartInstanceRef.current.isDisposed()) {
        try {
          chartInstanceRef.current.off("click");
          chartInstanceRef.current.dispose();
        } catch {
          // Gracefully handle jsdom canvas disposal
        }
        chartInstanceRef.current = null;
      }
    };
  }, []);

  // KPI card delegation
  if (spec.chart_type === "kpi_card") {
    if (loading) {
      return <ChartSkeleton height={height} className={className} hasLegend={false} />;
    }
    const metricCol =
      spec.metrics && spec.metrics.length > 0
        ? typeof spec.metrics[0] === "string"
          ? spec.metrics[0]
          : spec.metrics[0].column
        : undefined;

    return (
      <div
        className={cn("w-full", className)}
        style={{ minHeight: typeof height === "number" ? `${height}px` : height }}
      >
        <KpiCard
          title={spec.title || "Metric"}
          dataPayload={dataPayload}
          metricColumn={metricCol}
          formatter={spec.formatter}
          target={spec.kpi_target}
          comparisonLabel={spec.kpi_comparison_label}
        />
      </div>
    );
  }

  if (loading) {
    return (
      <ChartSkeleton
        height={height}
        className={className}
        hasLegend={spec.show_legend !== false}
      />
    );
  }

  if (initError) {
    return (
      <div
        role="alert"
        className={cn(
          "flex flex-col items-center justify-center border border-destructive/40 bg-destructive/5 rounded-lg p-6 text-center text-sm text-destructive",
          className
        )}
        style={{ height: typeof height === "number" ? `${height}px` : height }}
      >
        <p className="font-semibold">Chart Initialization Error</p>
        <p className="mt-1 text-xs opacity-90">{initError}</p>
      </div>
    );
  }

  if (!builderResult.isValid || !builderResult.option) {
    return (
      <div
        className={cn(
          "flex flex-col items-center justify-center border border-dashed border-[var(--border)] rounded-lg p-6 text-center text-sm text-[var(--muted-foreground)]",
          className
        )}
        style={{ height: typeof height === "number" ? `${height}px` : height }}
      >
        <p className="font-medium text-[var(--foreground)]">No visualization data</p>
        <p className="mt-1 text-xs">
          {builderResult.emptyReason || "No data records available for this chart configuration."}
        </p>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className={cn("w-full transition-colors duration-200", className)}
      style={{ height: typeof height === "number" ? `${height}px` : height }}
      aria-label={spec.title || `${spec.chart_type} chart`}
      role="img"
    />
  );
}
