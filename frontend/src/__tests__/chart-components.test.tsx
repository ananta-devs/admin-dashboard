import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChartSkeleton } from "../components/charts/chart-skeleton";
import { KpiCard } from "../components/charts/kpi-card";
import { EChartsWrapper } from "../components/charts/echarts-wrapper";
import type { TabularDataPayload } from "../components/charts/chart-types";

describe("ChartSkeleton Component", () => {
  it("renders with accessible status role and default styles", () => {
    render(<ChartSkeleton height={320} />);
    const skeleton = screen.getByRole("status", { name: /loading chart/i });
    expect(skeleton).toBeDefined();
    expect(skeleton.getAttribute("style")).toContain("height: 320px");
  });

  it("renders without legend when hasLegend is false", () => {
    const { container } = render(<ChartSkeleton hasLegend={false} />);
    // The top legend element shouldn't contain the 3 items
    const legendSkeletons = container.querySelectorAll(".flex.items-center.space-x-2");
    expect(legendSkeletons.length).toBe(0);
  });
});

describe("KpiCard Component", () => {
  const payload: TabularDataPayload = {
    columns: ["month", "revenue"],
    data: [
      { month: "Jan", revenue: 10000 },
      { month: "Feb", revenue: 12000 },
      { month: "Mar", revenue: 15000 },
    ],
    total_rows: 3,
  };

  it("renders formatted metric value and title", () => {
    render(
      <KpiCard
        title="Total Revenue"
        dataPayload={payload}
        metricColumn="revenue"
        formatter="currency_usd"
        target={14000}
      />
    );

    expect(screen.getByText("Total Revenue")).toBeDefined();
    expect(screen.getByText("$15,000.00")).toBeDefined();
    expect(screen.getByText(/vs target/i)).toBeDefined();
  });

  it("calculates positive delta against target", () => {
    render(
      <KpiCard
        title="Total Revenue"
        dataPayload={payload}
        metricColumn="revenue"
        target={12000}
      />
    );

    // Delta: (15000 - 12000) / 12000 = +25.0%
    expect(screen.getByText("25.0%")).toBeDefined();
  });

  it("renders SVG sparkline for multi-point historical data", () => {
    const { container } = render(
      <KpiCard
        title="MRR"
        dataPayload={payload}
        metricColumn="revenue"
      />
    );

    const polyline = container.querySelector("polyline");
    expect(polyline).toBeDefined();
    expect(polyline?.getAttribute("points")).toBeTruthy();
  });
});

describe("EChartsWrapper Component Delegation", () => {
  it("delegates kpi_card to KpiCard component seamlessly", () => {
    const payload: TabularDataPayload = {
      columns: ["metric", "value"],
      data: [{ metric: "Active Users", value: 45000 }],
      total_rows: 1,
    };

    render(
      <EChartsWrapper
        spec={{
          chart_type: "kpi_card",
          title: "Active Users",
          metrics: ["value"],
          formatter: "number_compact",
        }}
        dataPayload={payload}
      />
    );

    expect(screen.getByText("Active Users")).toBeDefined();
    expect(screen.getByText("45K")).toBeDefined();
  });

  it("renders loading skeleton when loading is true", () => {
    render(
      <EChartsWrapper
        spec={{ chart_type: "bar" }}
        loading={true}
      />
    );

    expect(screen.getByRole("status", { name: /loading chart/i })).toBeDefined();
  });

  it("renders fallback empty state when dataPayload is empty", () => {
    render(
      <EChartsWrapper
        spec={{ chart_type: "bar" }}
        dataPayload={{ columns: [], data: [], total_rows: 0 }}
      />
    );

    expect(screen.getByText("No visualization data")).toBeDefined();
  });
});
