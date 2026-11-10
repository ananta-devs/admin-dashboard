import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import AnalyticsPage from "@/app/analytics/page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/analytics",
}));

// Mock global fetch for API calls
beforeEach(() => {
  global.fetch = vi.fn().mockImplementation((url: string) => {
    if (url.includes("/api/v1/analytics/operations")) {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve([
            {
              id: "profile_dataset",
              name: "Dataset Profiling",
              description: "Generate structural summaries",
              category: "descriptive",
              parameters_schema: {},
              supported_column_types: ["numeric", "string"],
              produces_tabular_output: false,
              version: "1.0.0",
            },
            {
              id: "group_by_aggregate",
              name: "Group By & Aggregate",
              description: "Group records and compute metrics",
              category: "aggregation",
              parameters_schema: {},
              supported_column_types: ["numeric", "string"],
              produces_tabular_output: true,
              version: "1.0.0",
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
                id: "job-test-12345678",
                organization_id: "org-1",
                workspace_id: "ws-1",
                source_workbook_id: "wb-1",
                source_sheet_id: "sheet-1",
                operation_id: "group_by_aggregate",
                parameters: { group_by: ["Region"], aggregations: [] },
                status: "succeeded",
                progress_percent: 100,
                current_stage: "completed",
                started_at: new Date().toISOString(),
                completed_at: new Date().toISOString(),
                error_code: null,
                error_message: null,
                attempt_count: 1,
                max_attempts: 3,
                result_id: "res-test-1234",
                created_at: new Date().toISOString(),
                updated_at: new Date().toISOString(),
              },
            ],
            total: 1,
            limit: 50,
            offset: 0,
          }),
      });
    }

    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({}),
    });
  });
});

describe("AnalyticsPage", () => {
  it("renders page title, badges, stats cards, and action buttons", async () => {
    render(<AnalyticsPage />);

    expect(await screen.findByText("Analytics Engine")).toBeDefined();
    expect(screen.getByText("Polars & Celery")).toBeDefined();
    expect(screen.getByText("New Analysis")).toBeDefined();
    expect(screen.getByText("Refresh")).toBeDefined();

    // Verify stats cards
    expect(screen.getByText("Total Jobs")).toBeDefined();
    expect(screen.getByText("Completed")).toBeDefined();
    expect(screen.getByText("In Progress")).toBeDefined();
    expect(screen.getByText("Operations Catalogue")).toBeDefined();
  });

  it("opens Create Analysis modal when clicking New Analysis button", async () => {
    render(<AnalyticsPage />);

    const newBtn = screen.getByText("New Analysis");
    fireEvent.click(newBtn);

    expect(await screen.findByText("Submit Analytics Job")).toBeDefined();
    expect(screen.getByText("Select Analytical Operation")).toBeDefined();
    expect(screen.getByText("Workbook ID")).toBeDefined();
    expect(screen.getByText("Sheet ID")).toBeDefined();
  });

  it("displays filtered jobs and status badges", async () => {
    render(<AnalyticsPage />);

    expect(await screen.findByText("Job ID & Operation")).toBeDefined();
    expect(screen.getByText("Progress / Stage")).toBeDefined();
    expect(screen.getByText("Attempts")).toBeDefined();
  });
});
