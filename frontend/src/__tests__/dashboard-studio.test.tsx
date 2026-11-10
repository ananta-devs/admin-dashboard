import * as React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  DashboardToolbar,
  FilterBar,
  DashboardGrid,
  Dashboard,
  Widget,
  QueryResult,
  DashboardFilterState,
} from "../components/dashboard";
import DashboardsPage from "../app/dashboards/page";

describe("Phase 9 Gate 2: Dashboard Studio & Grid Component Tests", () => {
  const mockDashboard: Dashboard = {
    id: "dash-1",
    workspace_id: "ws-1",
    title: "Executive Intelligence Studio",
    description: "Multi-widget analytics",
    layout_config: { version: 1, timezone: "UTC" },
    is_shared: true,
    widget_count: 2,
  };

  const mockWidgets: Widget[] = [
    {
      id: "w-1",
      dashboard_id: "dash-1",
      title: "Revenue by Region",
      widget_type: "bar",
      source_type: "sheet",
      source_id: "s-1",
      query_config: {
        dimensions: ["Region"],
        metrics: [{ column: "Revenue", agg: "sum" }],
      },
      position_x: 0,
      position_y: 0,
      width: 6,
      height: 4,
      sort_order: 0,
    },
    {
      id: "w-2",
      dashboard_id: "dash-1",
      title: "Quarterly Target KPI",
      widget_type: "kpi_card",
      source_type: "sheet",
      source_id: "s-1",
      query_config: {
        target: 100000,
        local_filters: [
          { id: "lf-1", column: "Status", operator: "eq", value: "active", enabled: true },
        ],
      },
      position_x: 6,
      position_y: 0,
      width: 6,
      height: 4,
      sort_order: 1,
    },
  ];

  const mockData: Record<string, QueryResult> = {
    "w-1": {
      columns: ["Region", "sum_Revenue"],
      data: [{ Region: "East", sum_Revenue: 50000 }, { Region: "West", sum_Revenue: 75000 }],
      total_rows: 2,
      summary: {},
    },
    "w-2": {
      columns: ["Total"],
      data: [{ Total: 85000 }],
      total_rows: 1,
      summary: {},
    },
  };

  describe("DashboardToolbar", () => {
    it("renders View Mode by default with Edit Dashboard button", () => {
      render(
        <DashboardToolbar
          dashboards={[mockDashboard]}
          activeDashboard={mockDashboard}
          isEditMode={false}
          isDirty={false}
          loading={false}
          onSelectDashboard={vi.fn()}
          onToggleEditMode={vi.fn()}
          onSaveChanges={vi.fn()}
          onCancelChanges={vi.fn()}
          onNewDashboard={vi.fn()}
          onAddWidget={vi.fn()}
          onDuplicateDashboard={vi.fn()}
          onDeleteDashboard={vi.fn()}
          onRefreshData={vi.fn()}
          onUpdateMetadata={vi.fn()}
        />
      );

      expect(screen.getByRole("heading", { name: "Executive Intelligence Studio" })).toBeDefined();
      expect(screen.getByRole("button", { name: /Edit Dashboard/i })).toBeDefined();
      expect(screen.getByRole("button", { name: /Duplicate/i })).toBeDefined();
      expect(screen.getByRole("button", { name: /Delete/i })).toBeDefined();
    });

    it("renders Edit Mode controls and dirty state indicator", () => {
      render(
        <DashboardToolbar
          dashboards={[mockDashboard]}
          activeDashboard={mockDashboard}
          isEditMode={true}
          isDirty={true}
          loading={false}
          onSelectDashboard={vi.fn()}
          onToggleEditMode={vi.fn()}
          onSaveChanges={vi.fn()}
          onCancelChanges={vi.fn()}
          onNewDashboard={vi.fn()}
          onAddWidget={vi.fn()}
          onDuplicateDashboard={vi.fn()}
          onDeleteDashboard={vi.fn()}
          onRefreshData={vi.fn()}
          onUpdateMetadata={vi.fn()}
        />
      );

      expect(screen.getByText("Unsaved Changes")).toBeDefined();
      expect(screen.getByRole("button", { name: /Add Widget/i })).toBeDefined();
      expect(screen.getByRole("button", { name: /Save Changes/i })).toBeDefined();
      expect(screen.getByRole("button", { name: /Cancel/i })).toBeDefined();
    });
  });

  describe("FilterBar", () => {
    it("renders date preset and timezone dropdowns", () => {
      const state: DashboardFilterState = {
        timezone: "America/New_York",
        date_preset: "last_7_days",
        date_range: { start: "2026-10-26T00:00:00Z", end: "2026-11-02T23:59:59Z" },
        criteria: [
          { id: "c1", column: "Region", operator: "eq", value: "East", enabled: true },
        ],
      };

      render(
        <FilterBar
          filterState={state}
          onFilterChange={vi.fn()}
          availableColumns={["Region", "Category", "Sales"]}
        />
      );

      expect(screen.getByLabelText("Date Range Preset")).toBeDefined();
      expect(screen.getByLabelText("Dashboard Timezone")).toBeDefined();
      expect(screen.getAllByText(/Last 7 Days/i).length).toBeGreaterThan(0);
      expect(screen.getByText(/Region/i)).toBeDefined();
      expect(screen.getByText("2 Active Filters")).toBeDefined();
    });
  });

  describe("DashboardGrid", () => {
    it("renders responsive 12-column items with local filter badge and controls", () => {
      const handleLayoutChange = vi.fn();
      render(
        <DashboardGrid
          widgets={mockWidgets}
          widgetData={mockData}
          isEditMode={true}
          onLayoutChange={handleLayoutChange}
          onEditWidget={vi.fn()}
          onDeleteWidget={vi.fn()}
          renderVisualization={(w) => <div data-testid={`viz-${w.id}`}>{w.title} Chart</div>}
        />
      );

      expect(screen.getByText("Revenue by Region")).toBeDefined();
      expect(screen.getByText("Quarterly Target KPI")).toBeDefined();
      // Widget 2 has 1 local filter
      expect(screen.getByTitle("1 local filters active")).toBeDefined();

      // Test width cycle
      const resizeButtons = screen.getAllByLabelText(/Resize width for/i);
      expect(resizeButtons).toHaveLength(2);
      fireEvent.click(resizeButtons[0]);
      expect(handleLayoutChange).toHaveBeenCalled();
    });
  });

  describe("DashboardsPage Full Integration", () => {
    beforeEach(() => {
      vi.restoreAllMocks();
      global.fetch = vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/v1/auth/csrf-token")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ csrf_token: "mock-csrf" }),
          });
        }
        if (url.endsWith("/api/v1/dashboards")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve([mockDashboard]),
          });
        }
        if (url.includes("/api/v1/dashboards/dash-1/widgets/w-1/query")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(mockData["w-1"]),
          });
        }
        if (url.includes("/api/v1/dashboards/dash-1/widgets/w-2/query")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve(mockData["w-2"]),
          });
        }
        if (url.includes("/api/v1/dashboards/dash-1")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ ...mockDashboard, widgets: mockWidgets }),
          });
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      }) as unknown as typeof fetch;
    });

    it("renders Dashboards page and preserves existing regression heading", async () => {
      render(<DashboardsPage />);
      expect(await screen.findByText("Analytics Dashboards")).toBeDefined();
      expect(await screen.findByRole("heading", { name: "Executive Intelligence Studio" })).toBeDefined();
      expect(await screen.findByText("Revenue by Region")).toBeDefined();
    });
  });
});
