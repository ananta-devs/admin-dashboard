import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import FileStoragePage from "@/app/files/page";

describe("File Storage Module Frontend Tests", () => {
  const mockQuota = {
    organization_id: "org-1",
    usage_bytes: 52428800, // 50 MB
    quota_bytes: 524288000, // 500 MB
    available_bytes: 471859200,
    file_count: 2,
    utilization_percent: 10.0,
  };

  const mockFiles = [
    {
      id: "file-1",
      organization_id: "org-1",
      filename: "financial_report.pdf",
      content_type: "application/pdf",
      size_bytes: 1048576, // 1 MB
      storage_path: "user_files/org-1/file-1.dat",
      checksum_sha256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      folder: "/",
      created_at: "2026-11-03T10:00:00Z",
      updated_at: "2026-11-03T10:00:00Z",
    },
    {
      id: "file-2",
      organization_id: "org-1",
      filename: "revenue_data.csv",
      content_type: "text/csv",
      size_bytes: 204800, // 200 KB
      storage_path: "user_files/org-1/file-2.dat",
      checksum_sha256: "ca978112ca1bbdcafac231b39a23dc4da7860819c1966102297864483724a805",
      folder: "/",
      created_at: "2026-11-04T12:00:00Z",
      updated_at: "2026-11-04T12:00:00Z",
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
      if (url.includes("/quota/usage")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockQuota),
        });
      }
      if (url.includes("/api/v1/files") && (!options || !options.method || options.method === "GET")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockFiles),
        });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    });
  });

  it("renders file storage page and displays quota banner and files table", async () => {
    render(<FileStoragePage />);

    expect(screen.getByText("File Storage")).toBeDefined();

    await waitFor(() => {
      expect(screen.getByText("Storage Quota Utilization")).toBeDefined();
      expect(screen.getByText("financial_report.pdf")).toBeDefined();
      expect(screen.getByText("revenue_data.csv")).toBeDefined();
      expect(screen.getByText("1 MB")).toBeDefined();
      expect(screen.getByText("200 KB")).toBeDefined();
    });
  });

  it("opens the upload file modal", async () => {
    render(<FileStoragePage />);

    await waitFor(() => {
      expect(screen.getByText("financial_report.pdf")).toBeDefined();
    });

    const uploadBtn = screen.getByRole("button", { name: /upload file/i });
    fireEvent.click(uploadBtn);

    expect(screen.getByRole("heading", { name: "Upload File" })).toBeDefined();
    expect(screen.getByLabelText(/select file/i)).toBeDefined();
  });
});
