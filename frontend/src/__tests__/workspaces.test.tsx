import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import WorkspacesPage from "@/app/workspaces/page";

describe("Workspaces Module Frontend Tests", () => {
  const mockWorkspaces = [
    {
      id: "ws-1",
      organization_id: "org-1",
      name: "Default Workspace",
      slug: "default",
      created_at: "2026-11-01T10:00:00Z",
      updated_at: "2026-11-01T10:00:00Z",
      resource_counts: {
        dashboards: 3,
        workbooks: 2,
        report_definitions: 1,
        analytics_jobs: 0,
      },
    },
    {
      id: "ws-2",
      organization_id: "org-1",
      name: "Research & Development",
      slug: "rnd",
      created_at: "2026-11-02T12:00:00Z",
      updated_at: "2026-11-02T12:00:00Z",
      resource_counts: {
        dashboards: 0,
        workbooks: 0,
        report_definitions: 0,
        analytics_jobs: 0,
      },
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
      if (url.includes("/api/v1/workspaces") && options?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              id: "ws-3",
              organization_id: "org-1",
              name: "Marketing Team",
              slug: "marketing-team",
              created_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
              resource_counts: { dashboards: 0, workbooks: 0, report_definitions: 0, analytics_jobs: 0 },
            }),
        });
      }
      if (url.includes("/api/v1/workspaces")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockWorkspaces),
        });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    });
  });

  it("renders workspace page and displays listed workspaces with badges and resource counts", async () => {
    render(<WorkspacesPage />);

    expect(screen.getByText("Workspaces")).toBeDefined();

    await waitFor(() => {
      expect(screen.getByText("2 Total")).toBeDefined();
      expect(screen.getByText("Default Workspace")).toBeDefined();
      expect(screen.getByText("Research & Development")).toBeDefined();
      expect(screen.getByText("Default")).toBeDefined();
      expect(screen.getByText("/default")).toBeDefined();
      expect(screen.getByText("/rnd")).toBeDefined();
    });
  });

  it("opens the create workspace dialog and submits a new workspace", async () => {
    render(<WorkspacesPage />);

    await waitFor(() => {
      expect(screen.getByText("Default Workspace")).toBeDefined();
    });

    const createBtn = screen.getByRole("button", { name: /new workspace/i });
    fireEvent.click(createBtn);

    expect(screen.getByRole("heading", { name: "Create Workspace" })).toBeDefined();

    const nameInput = screen.getByLabelText(/workspace name/i);
    fireEvent.change(nameInput, { target: { value: "Marketing Team" } });

    // Verify auto-generated slug
    const slugInput = screen.getByLabelText(/slug/i);
    expect((slugInput as HTMLInputElement).value).toBe("marketing-team");

    const submitBtn = screen.getByRole("button", { name: "Create Workspace" });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/workspaces",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ name: "Marketing Team", slug: "marketing-team" }),
        })
      );
    });
  });

  it("enforces protected deletion on the default workspace", async () => {
    render(<WorkspacesPage />);

    await waitFor(() => {
      expect(screen.getByText("Default Workspace")).toBeDefined();
    });

    const deleteBtn = screen.getByLabelText("Delete Default Workspace");
    fireEvent.click(deleteBtn);

    await waitFor(() => {
      expect(screen.getByText(/default workspace protected/i)).toBeDefined();
      expect(screen.getByText(/cannot be deleted/i)).toBeDefined();
      const permDeleteBtn = screen.getByRole("button", { name: /permanently delete/i });
      expect(permDeleteBtn.hasAttribute("disabled")).toBe(true);
    });
  });
});
