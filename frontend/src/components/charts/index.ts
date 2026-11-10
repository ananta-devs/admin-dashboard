import * as React from "react";
import dynamic from "next/dynamic";
import { ChartSkeleton } from "./chart-skeleton";

export * from "./chart-types";
export * from "./chart-theme";
export * from "./chart-options-builder";
export * from "./chart-skeleton";
export * from "./kpi-card";

export const DynamicEChartsWrapper = dynamic(
  () => import("./echarts-wrapper").then((mod) => mod.EChartsWrapper),
  {
    ssr: false,
    loading: () => React.createElement(ChartSkeleton),
  }
);
