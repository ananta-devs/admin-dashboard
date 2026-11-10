import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { TabularDataPayload } from "../components/charts/chart-types";

const mockInit = vi.fn();
const mockGetInstanceByDom = vi.fn();

vi.mock("echarts/core", () => ({
  use: vi.fn(),
  init: (...args: unknown[]) => mockInit(...args),
  getInstanceByDom: (...args: unknown[]) => mockGetInstanceByDom(...args),
}));

vi.mock("echarts/charts", () => ({
  BarChart: {},
  LineChart: {},
  PieChart: {},
  ScatterChart: {},
  HeatmapChart: {},
}));

vi.mock("echarts/components", () => ({
  TitleComponent: {},
  TooltipComponent: {},
  GridComponent: {},
  LegendComponent: {},
  DataZoomComponent: {},
  VisualMapComponent: {},
  DatasetComponent: {},
  AxisPointerComponent: {},
}));

vi.mock("echarts/renderers", () => ({
  CanvasRenderer: {},
}));

// Import component under test after vi.mock declarations
import { EChartsWrapper } from "../components/charts/echarts-wrapper";

describe("EChartsWrapper Component Lifecycle & Cleanup", () => {
  const samplePayload: TabularDataPayload = {
    columns: ["category", "sales"],
    data: [
      { category: "Alpha", sales: 100 },
      { category: "Beta", sales: 200 },
    ],
    total_rows: 2,
  };

  let mockInstance: {
    setOption: ReturnType<typeof vi.fn>;
    resize: ReturnType<typeof vi.fn>;
    dispose: ReturnType<typeof vi.fn>;
    on: ReturnType<typeof vi.fn>;
    off: ReturnType<typeof vi.fn>;
    isDisposed: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    vi.clearAllMocks();
    class MockResizeObserver {
      observe = vi.fn();
      unobserve = vi.fn();
      disconnect = vi.fn();
    }
    vi.stubGlobal("ResizeObserver", MockResizeObserver);

    mockInstance = {
      setOption: vi.fn(),
      resize: vi.fn(),
      dispose: vi.fn(),
      on: vi.fn(),
      off: vi.fn(),
      isDisposed: vi.fn(() => false),
    };
    mockInit.mockReturnValue(mockInstance);
    mockGetInstanceByDom.mockReturnValue(null);
  });

  it("initializes chart instance once per mounted container and disposes on unmount", () => {
    const { unmount } = render(
      <EChartsWrapper
        spec={{ chart_type: "bar", title: "Sales Chart" }}
        dataPayload={samplePayload}
      />
    );

    expect(mockInit).toHaveBeenCalledTimes(1);
    expect(mockInstance.setOption).toHaveBeenCalledTimes(1);

    // Unmount
    unmount();
    expect(mockInstance.off).toHaveBeenCalledWith("click");
    expect(mockInstance.dispose).toHaveBeenCalledTimes(1);
  });

  it("observes container with ResizeObserver and disconnects on unmount", () => {
    const observeSpy = vi.fn();
    const disconnectSpy = vi.fn();

    class MockResizeObserver {
      observe = observeSpy;
      unobserve = vi.fn();
      disconnect = disconnectSpy;
    }

    vi.stubGlobal("ResizeObserver", MockResizeObserver);

    const { unmount } = render(
      <EChartsWrapper
        spec={{ chart_type: "line", title: "Trend" }}
        dataPayload={samplePayload}
      />
    );

    expect(observeSpy).toHaveBeenCalledTimes(1);

    unmount();
    expect(disconnectSpy).toHaveBeenCalledTimes(1);
  });

  it("updates options without creating duplicate instances on re-render", () => {
    const { rerender } = render(
      <EChartsWrapper
        spec={{ chart_type: "bar", title: "First Title" }}
        dataPayload={samplePayload}
      />
    );

    expect(mockInit).toHaveBeenCalledTimes(1);

    // Re-render with new title/spec
    rerender(
      <EChartsWrapper
        spec={{ chart_type: "bar", title: "Second Title" }}
        dataPayload={samplePayload}
      />
    );

    // ECharts.init must NOT be called again
    expect(mockInit).toHaveBeenCalledTimes(1);
    // setOption must be called again with updated configuration
    expect(mockInstance.setOption).toHaveBeenCalledTimes(2);
  });

  it("mounts cleanly in React.StrictMode without lifecycle errors", () => {
    const { unmount } = render(
      <React.StrictMode>
        <EChartsWrapper
          spec={{ chart_type: "bar" }}
          dataPayload={samplePayload}
        />
      </React.StrictMode>
    );

    expect(mockInit).toHaveBeenCalled();
    unmount();
  });

  it("produces a controlled error state when initialization fails", async () => {
    mockInit.mockImplementation(() => {
      throw new Error("Canvas context creation failed");
    });

    render(
      <EChartsWrapper
        spec={{ chart_type: "bar" }}
        dataPayload={samplePayload}
      />
    );

    const errorAlert = await screen.findByRole("alert");
    expect(errorAlert).toBeDefined();
    expect(screen.getByText(/Chart Initialization Error/i)).toBeDefined();
    expect(screen.getByText(/Canvas context creation failed/i)).toBeDefined();
  });
});
