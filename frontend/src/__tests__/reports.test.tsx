import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ReportsPage from "@/app/reports/page";

describe("Reports Module Frontend Tests", () => {
  const mockWorkspaces = [
    { id: "ws-1", name: "Executive Suite", slug: "executive" },
  ];

  const mockDefinitions = [
    {
      id: "def-1",
      organization_id: "org-1",
      workspace_id: "ws-1",
      title: "Quarterly Board Review",
      description: "Executive presentation covering financials and metrics",
      format: "pdf",
      page_orientation: "portrait",
      page_size: "a4",
      header_text: "Confidential",
      footer_text: "Internal Audit",
      include_table_of_contents: true,
      include_cover_page: true,
      theme: "default",
      is_archived: false,
      sections: [
        {
          id: "sec-1",
          report_definition_id: "def-1",
          section_type: "summary",
          title: "Executive Summary",
          sort_order: 0,
          page_break_after: false,
        },
      ],
    },
  ];

  beforeEach(() => {
    vi.restoreAllMocks();
    global.fetch = vi.fn().mockImplementation((url: string, options?: RequestInit) => {
      if (url.includes("/api/v1/auth/csrf-token")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ csrf_token: "test-csrf-token" }),
        });
      }
      if (url.includes("/api/v1/workspaces")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockWorkspaces),
        });
      }
      if (url.includes("/api/v1/reports/definitions") && options?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              id: "def-2",
              organization_id: "org-1",
              workspace_id: "ws-1",
              title: "Monthly KPI Digest",
              format: "pdf",
              page_orientation: "portrait",
              page_size: "a4",
              include_table_of_contents: true,
              include_cover_page: true,
              theme: "default",
              is_archived: false,
              sections: [],
            }),
        });
      }
      if (url.includes("/api/v1/reports/definitions/def-1/run") && options?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              id: "run-101",
              report_definition_id: "def-1",
              report_title: "Quarterly Board Review",
              trigger_type: "manual",
              status: "COMPILING_PDF",
            }),
        });
      }
      if (url.includes("/api/v1/reports/runs/run-101/download-token")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              download_token: "signed-tok-123",
              download_url: "/api/v1/reports/runs/run-101/download?token=signed-tok-123",
              expires_in_seconds: 60,
            }),
        });
      }
      if (url.includes("/api/v1/reports/definitions")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockDefinitions),
        });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    });
  });

  it("renders reports page and displays definitions list with orientation and section count", async () => {
    render(<ReportsPage />);

    expect(screen.getByText("Reports")).toBeDefined();

    await waitFor(() => {
      expect(screen.getByText("Quarterly Board Review")).toBeDefined();
      expect(screen.getByText("Executive presentation covering financials and metrics")).toBeDefined();
      expect(screen.getByText("portrait")).toBeDefined();
      expect(screen.getByText("1 Sections")).toBeDefined();
    });
  });

  it("opens create report dialog and submits new definition", async () => {
    render(<ReportsPage />);

    await waitFor(() => {
      expect(screen.getByText("Quarterly Board Review")).toBeDefined();
    });

    const newBtn = screen.getByRole("button", { name: /new report/i });
    fireEvent.click(newBtn);

    expect(screen.getByRole("heading", { name: "New Report Definition" })).toBeDefined();

    const titleInput = screen.getByLabelText(/report title/i);
    fireEvent.change(titleInput, { target: { value: "Monthly KPI Digest" } });

    const createBtn = screen.getByRole("button", { name: "Create Report" });
    fireEvent.click(createBtn);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/reports/definitions",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining("Monthly KPI Digest"),
        })
      );
    });
  });

  it("triggers report run on demand and initiates execution polling", async () => {
    render(<ReportsPage />);

    await waitFor(() => {
      expect(screen.getByText("Quarterly Board Review")).toBeDefined();
    });

    const runBtn = screen.getByRole("button", { name: /run now/i });
    fireEvent.click(runBtn);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/reports/definitions/def-1/run",
        expect.objectContaining({
          method: "POST",
        })
      );
    });
  });
});

