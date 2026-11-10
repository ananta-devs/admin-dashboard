import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import SystemHealthPage from "@/app/system/page";

describe("System Health Module Frontend Tests", () => {
  const mockTelemetry = {
    timestamp: "2026-11-04T16:00:00Z",
    overall_status: "Healthy",
    subsystems: {
      backend: {
        status: "Healthy",
        uptime_seconds: 3600,
        version: "1.0.0",
        environment: "production",
        timestamp: "2026-11-04T16:00:00Z",
      },
      database: {
        status: "Healthy",
        latency_ms: 2.45,
        engine_version: "PostgreSQL 16.2",
        pool: {
          pool_size: 10,
          checked_in: 8,
          checked_out: 2,
          overflow: 0,
        },
      },
      redis: {
        status: "Healthy",
        latency_ms: 0.85,
        connected_clients: 5,
        used_memory_human: "12.5M",
      },
      celery: {
        status: "Healthy",
        active_queues: {
          default: 0,
          analytics: 1,
          reporting: 0,
        },
        total_queued_tasks: 1,
      },
      analytics: {
        status: "Healthy",
        active_jobs: 1,
        completed_24h: 42,
        failed_24h: 0,
      },
      storage: {
        status: "Healthy",
        tenant_files_stored: 8,
        tenant_storage_bytes: 52428800,
        host_free_disk_bytes: 107374182400,
        host_free_disk_gb: 100.0,
      },
      backup_recovery: {
        status: "Healthy",
        rpo: {
          target_minutes: 15.0,
          measured_nominal_minutes: 2.1,
          sla_compliant: true,
        },
        rto: {
          target_minutes: 60.0,
          measured_duration_minutes: 7.87,
          sla_compliant: true,
        },
        spool: {
          total_batches: 12,
          uncommitted_files: 0,
          spool_directory: "./uploads/wal_spool",
        },
      },
    },
  };

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders all subsystem diagnostics cards when telemetry is loaded", async () => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/api/v1/system/telemetry")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(mockTelemetry),
        });
      }
      return Promise.reject(new Error("Unknown route"));
    });

    render(<SystemHealthPage />);

    expect(screen.getByTestId("system-page-container")).toBeDefined();
    await waitFor(() => {
      expect(screen.getByTestId("subsystem-card-backend")).toBeDefined();
      expect(screen.getByTestId("subsystem-card-database")).toBeDefined();
      expect(screen.getByTestId("subsystem-card-redis")).toBeDefined();
      expect(screen.getByTestId("subsystem-card-celery")).toBeDefined();
      expect(screen.getByTestId("subsystem-card-analytics")).toBeDefined();
      expect(screen.getByTestId("subsystem-card-storage")).toBeDefined();
      expect(screen.getByTestId("subsystem-card-backup")).toBeDefined();
    });

    expect(screen.getByText("Backend Core")).toBeDefined();
    expect(screen.getByText("PostgreSQL Engine")).toBeDefined();
    expect(screen.getByText("Redis Cache & Locks")).toBeDefined();
    expect(screen.getByText("Celery Task Workers")).toBeDefined();
  });

  it("handles 403 Forbidden with administrative privilege alert banner", async () => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/api/v1/system/telemetry")) {
        return Promise.resolve({
          ok: false,
          status: 403,
          json: () => Promise.resolve({ message: "Forbidden" }),
        });
      }
      return Promise.reject(new Error("Unknown route"));
    });

    render(<SystemHealthPage />);

    await waitFor(() => {
      expect(screen.getByTestId("system-forbidden-banner")).toBeDefined();
    });
    expect(screen.getByText(/Administrative Privilege Required/i)).toBeDefined();
  });

  it("triggers refresh diagnostics when manual refresh button is clicked", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/api/v1/system/telemetry")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(mockTelemetry),
        });
      }
      return Promise.reject(new Error("Unknown route"));
    });
    global.fetch = fetchMock;

    render(<SystemHealthPage />);

    await waitFor(() => {
      expect(screen.getByTestId("refresh-telemetry-btn")).toBeDefined();
    });

    const refreshBtn = screen.getByTestId("refresh-telemetry-btn");
    fireEvent.click(refreshBtn);

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
  });
});

