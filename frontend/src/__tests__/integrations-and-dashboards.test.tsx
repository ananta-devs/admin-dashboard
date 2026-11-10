import * as React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import IntegrationsPage from "../app/integrations/page";
import DashboardsPage from "../app/dashboards/page";

describe("Phase 8 Frontend Tests: Integrations & Dashboards", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/api/v1/auth/csrf-token")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ csrf_token: "mock-csrf-token" }),
        });
      }
      if (url.includes("/api/v1/integrations")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve([
              {
                id: "int-1",
                provider: "google",
                account_email: "executive@vibe.ai",
                status: "connected",
                authorized_scopes: ["https://www.googleapis.com/auth/spreadsheets"],
                created_at: new Date().toISOString(),
              },
            ]),
        });
      }
      if (url.includes("/api/v1/sync/mappings")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve([
              {
                id: "map-1",
                workbook_id: "wb-1",
                sheet_id: "sheet-1",
                external_spreadsheet_id: "ext-1",
                external_sheet_name: "Financials",
                direction: "bidirectional",
                status: "idle",
                conflict_policy: "manual",
                last_sync_at: new Date().toISOString(),
              },
            ]),
        });
      }
      if (url.endsWith("/api/v1/dashboards")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve([
              {
                id: "dash-1",
                workspace_id: "ws-1",
                title: "Global Sales Dashboard",
                description: "Executive metrics",
                layout_config: {},
                is_shared: true,
                widget_count: 1,
              },
            ]),
        });
      }
      if (url.includes("/api/v1/dashboards/dash-1")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              id: "dash-1",
              workspace_id: "ws-1",
              title: "Global Sales Dashboard",
              description: "Executive metrics",
              layout_config: {},
              is_shared: true,
              widgets: [
                {
                  id: "w-1",
                  dashboard_id: "dash-1",
                  title: "Quarterly Revenue",
                  widget_type: "bar",
                  source_type: "sheet",
                  source_id: "sheet-1",
                  query_config: {},
                  position_x: 0,
                  position_y: 0,
                  width: 6,
                  height: 4,
                  sort_order: 0,
                },
              ],
            }),
        });
      }
      if (url.includes("/api/v1/workspaces")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve([{ id: "ws-1", name: "Default Workspace" }]),
        });
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({}),
      });
    }) as unknown as typeof fetch;
  });

  it("renders Integrations page with connected Google account and sync mappings", async () => {
    render(<IntegrationsPage />);
    expect(await screen.findByText("External Integrations & Sync")).toBeDefined();
    expect(await screen.findByText("executive@vibe.ai")).toBeDefined();
    expect(await screen.findByText("Spreadsheet Synchronization Mappings")).toBeDefined();
    expect(await screen.findByText(/Financials/i)).toBeDefined();
  });

  it("renders Dashboards page with active dashboard and widgets", async () => {
    render(<DashboardsPage />);
    expect(await screen.findByText("Analytics Dashboards")).toBeDefined();
    expect(await screen.findByRole("heading", { name: "Global Sales Dashboard" })).toBeDefined();
    expect(await screen.findByText("Quarterly Revenue")).toBeDefined();
  });
});
