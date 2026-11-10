import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import Home from "@/app/page";
import { Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";

describe("Executive Command Center & Shell Tests", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the Executive Command Center home page with subsystems", async () => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/api/v1/spreadsheets/workbooks")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve([
              { id: "wb-1", title: "Q4 Financial Model", sheets: [{ id: "s-1", name: "Overview" }] },
            ]),
        });
      }
      if (url.includes("/api/v1/analytics/jobs")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              items: [
                { id: "job-1", operation: "DESCRIPTIVE_STATS", status: "COMPLETED", execution_time_ms: 120 },
              ],
            }),
        });
      }
      if (url.includes("/api/v1/analytics/storage")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              usage_bytes: 52428800,
              quota_bytes: 1073741824,
              available_bytes: 1021313024,
              utilization_percent: 4.88,
            }),
        });
      }
      if (url.includes("/api/v1/dashboards")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve([
              {
                id: "dash-1",
                title: "Executive KPI Cockpit",
                widgets: [{ id: "w-1", title: "Revenue", widget_type: "chart" }],
              },
            ]),
        });
      }
      if (url.includes("/api/v1/organization/audit-logs")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve([
              { id: "evt-1", action: "REPORT_RENDERED", actor: { display_name: "Lead Analyst" }, target_type: "Report" },
            ]),
        });
      }
      return Promise.resolve({ ok: false });
    });

    render(<Home />);

    expect(screen.getByText("Executive Command Center")).toBeDefined();
    expect(screen.getByText("Active Spreadsheets")).toBeDefined();
    expect(screen.getByText("Analytics Executions")).toBeDefined();
    expect(screen.getByText("Storage Utilization")).toBeDefined();
    expect(screen.getAllByText(/Pinned Dashboards/i).length).toBeGreaterThan(0);
    expect(screen.getByText("FastAPI Core API")).toBeDefined();
    expect(screen.getByText("PostgreSQL 16")).toBeDefined();
    expect(screen.getByText("Redis 7 State & Cache")).toBeDefined();

    await waitFor(() => {
      expect(screen.getByText("Q4 Financial Model")).toBeDefined();
      expect(screen.getByText("DESCRIPTIVE_STATS")).toBeDefined();
      expect(screen.getByText("50.0 MB of 1.0 GB used")).toBeDefined();
    });
  });

  it("renders the navigation sidebar with sections and items", () => {
    const handleToggle = () => {};
    render(<Sidebar collapsed={false} onToggle={handleToggle} />);
    expect(screen.getAllByText("ADMIN DASHBOARD").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Dashboard").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Spreadsheets").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Analytics").length).toBeGreaterThan(0);
    expect(screen.getAllByText("User Management").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Roles & RBAC").length).toBeGreaterThan(0);
  });

  it("renders the collapsed sidebar cleanly with icon view", () => {
    const handleToggle = () => {};
    const { container } = render(<Sidebar collapsed={true} onToggle={handleToggle} />);
    const desktopAside = container.querySelector("aside");
    expect(desktopAside).toBeDefined();
    expect(desktopAside?.className).toContain("w-16");
  });

  it("renders the topbar with command palette search trigger and controls", () => {
    let triggered = false;
    render(<Topbar onOpenCommandPalette={() => { triggered = true; }} />);
    const searchButton = screen.getByText("Search commands, workspaces...");
    expect(searchButton).toBeDefined();
    fireEvent.click(searchButton);
    expect(triggered).toBe(true);
  });
});
