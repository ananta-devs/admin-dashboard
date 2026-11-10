import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ExecutiveCommandCenter from "@/app/page";

describe("Executive Command Center - Full Functional & Integration Tests", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  const mockSuccessfulApis = () => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/api/v1/spreadsheets/workbooks")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve([
              {
                id: "wb-alpha",
                title: "FY26 Consolidated Forecast",
                description: "Multi-entity revenue projections",
                sheets: [{ id: "s1", name: "Executive Summary" }, { id: "s2", name: "P&L Detail" }],
                updated_at: new Date().toISOString(),
              },
            ]),
        });
      }
      if (url.includes("/api/v1/analytics/jobs")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              items: [
                {
                  id: "job-101",
                  operation: "CORRELATION_MATRIX",
                  status: "COMPLETED",
                  execution_time_ms: 345,
                  created_at: new Date().toISOString(),
                },
                {
                  id: "job-102",
                  operation: "REGRESSION_ANALYSIS",
                  status: "RUNNING",
                  execution_time_ms: null,
                  created_at: new Date().toISOString(),
                },
              ],
            }),
        });
      }
      if (url.includes("/api/v1/analytics/storage")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              usage_bytes: 8589934592, // 8.0 GB
              quota_bytes: 10737418240, // 10.0 GB
              available_bytes: 2147483648, // 2.0 GB
              utilization_percent: 80.0,
            }),
        });
      }
      if (url.includes("/api/v1/dashboards")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve([
              {
                id: "dash-kpi",
                title: "Board Operational KPIs",
                description: "Monthly board reporting package",
                widgets: [{ id: "w1", title: "Cash Flow", widget_type: "chart" }],
              },
              {
                id: "dash-sec",
                title: "Security & Compliance Cockpit",
                description: "Access controls and tenant isolation monitor",
                widgets: [{ id: "w2", title: "Audit Volume", widget_type: "metric" }],
              },
            ]),
        });
      }
      if (url.includes("/api/v1/organization/audit-logs")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve([
              {
                id: "evt-99",
                action: "ORGANIZATION_SETTINGS_UPDATED",
                actor: { display_name: "Chief Security Officer" },
                target_type: "OrganizationPolicy",
                occurred_at: new Date().toISOString(),
              },
            ]),
        });
      }
      return Promise.resolve({ ok: false });
    });
  };

  it("renders loaded data across all Executive Command Center sections", async () => {
    mockSuccessfulApis();
    render(<ExecutiveCommandCenter />);

    // Header & Subsystems
    expect(screen.getByText("Executive Command Center")).toBeDefined();
    expect(screen.getByText(/All Subsystems Active/i)).toBeDefined();

    // Data verification
    await waitFor(() => {
      // Workbooks
      expect(screen.getByText("FY26 Consolidated Forecast")).toBeDefined();
      expect(screen.getByText("2 sheets")).toBeDefined();

      // Jobs
      expect(screen.getByText("CORRELATION_MATRIX")).toBeDefined();
      expect(screen.getByText("REGRESSION_ANALYSIS")).toBeDefined();
      expect(screen.getByText("345 ms")).toBeDefined();

      // Storage
      expect(screen.getByTestId("storage-metric-percent").textContent).toContain("80.0%");
      expect(screen.getByText("8.0 GB of 10.0 GB used")).toBeDefined();

      // Subsystems
      expect(screen.getByText("FastAPI Core API")).toBeDefined();
      expect(screen.getByText("PostgreSQL 16")).toBeDefined();
      expect(screen.getByText("Redis 7 State & Cache")).toBeDefined();

      // Audit Log
      expect(screen.getByText("ORGANIZATION_SETTINGS_UPDATED")).toBeDefined();
      expect(screen.getByText("Chief Security Officer")).toBeDefined();
    });
  });

  it("allows pinning and unpinning dashboards with localStorage persistence", async () => {
    mockSuccessfulApis();
    render(<ExecutiveCommandCenter />);

    await waitFor(() => {
      expect(screen.getByText(/Board Operational KPIs/)).toBeDefined();
    });

    // Initially no dashboards are pinned
    expect(screen.getByText("No dashboards pinned yet")).toBeDefined();

    // Pin the first dashboard using the quick pin button
    const pinBtn = screen.getByRole("button", { name: /Pin Board Operational KPIs/i });
    fireEvent.click(pinBtn);

    // Verify localStorage was updated
    await waitFor(() => {
      const stored = localStorage.getItem("vibe_pinned_dashboards");
      expect(stored).toContain("dash-kpi");
    });

    // Now it should be displayed in the pinned gallery
    await waitFor(() => {
      expect(screen.queryByText("No dashboards pinned yet")).toBeNull();
      expect(screen.getByText("Board Operational KPIs")).toBeDefined();
    });

    // Unpin it
    const unpinBtn = screen.getByTitle("Unpin dashboard");
    fireEvent.click(unpinBtn);

    await waitFor(() => {
      const stored = localStorage.getItem("vibe_pinned_dashboards");
      expect(stored).toBe("[]");
      expect(screen.getByText("No dashboards pinned yet")).toBeDefined();
    });
  });

  it("gracefully handles 403 Forbidden for audit logs without breaking other widgets", async () => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/api/v1/organization/audit-logs")) {
        return Promise.resolve({
          status: 403,
          ok: false,
          json: () => Promise.resolve({ detail: "audit.read permission required" }),
        });
      }
      if (url.includes("/api/v1/spreadsheets/workbooks")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([]),
        });
      }
      if (url.includes("/api/v1/analytics/jobs")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ items: [] }),
        });
      }
      if (url.includes("/api/v1/analytics/storage")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              usage_bytes: 1048576,
              quota_bytes: 1073741824,
              available_bytes: 1072693248,
              utilization_percent: 0.1,
            }),
        });
      }
      if (url.includes("/api/v1/dashboards")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([]),
        });
      }
      return Promise.resolve({ ok: false });
    });

    render(<ExecutiveCommandCenter />);

    // Audit logs section shows clean warning banner instead of crashing
    await waitFor(() => {
      expect(screen.getByText("Administrative Permission Required")).toBeDefined();
      expect(
        screen.getByText(/Your current role does not have the/i)
      ).toBeDefined();
      // Storage and other widgets still rendered properly
      expect(screen.getByTestId("storage-metric-percent").textContent).toContain("0.1%");
    });
  });

  it("handles empty states gracefully across all sections", async () => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/api/v1/spreadsheets/workbooks")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      if (url.includes("/api/v1/analytics/jobs")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ items: [] }) });
      }
      if (url.includes("/api/v1/analytics/storage")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              usage_bytes: 0,
              quota_bytes: 1073741824,
              available_bytes: 1073741824,
              utilization_percent: 0.0,
            }),
        });
      }
      if (url.includes("/api/v1/dashboards")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      if (url.includes("/api/v1/organization/audit-logs")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      return Promise.resolve({ ok: false });
    });

    render(<ExecutiveCommandCenter />);

    await waitFor(() => {
      expect(screen.getByText("No workbooks found")).toBeDefined();
      expect(screen.getByText("No analytics jobs executed yet")).toBeDefined();
      expect(screen.getByText("No dashboards pinned yet")).toBeDefined();
      expect(screen.getByText("No recent audit activity recorded.")).toBeDefined();
    });
  });

  it("triggers manual telemetry refresh when refresh button is clicked", async () => {
    mockSuccessfulApis();
    render(<ExecutiveCommandCenter />);

    const refreshBtn = screen.getByRole("button", { name: /Refresh Telemetry/i });
    expect(refreshBtn).toBeDefined();

    fireEvent.click(refreshBtn);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledTimes(10); // Initial 5 + Refresh 5
    });
  });
});
