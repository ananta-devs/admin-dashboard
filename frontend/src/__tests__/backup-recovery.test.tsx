import * as React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { BackupRecoveryCard } from "@/components/admin/BackupRecoveryCard";
import BackupsSettingsPage from "@/app/settings/backups/page";

describe("Phase 10: Backup & Disaster Recovery Monitoring UI", () => {
  it("renders the Disaster Recovery Posture card with HEALTHY status and SLA metrics", () => {
    render(<BackupRecoveryCard />);

    // Dashboard root container
    expect(screen.getByTestId("backup-recovery-dashboard")).toBeDefined();

    // Title and Health Badge
    expect(screen.getByText("Disaster Recovery Posture")).toBeDefined();
    expect(screen.getByText("HEALTHY")).toBeDefined();

    // RPO & RTO values
    expect(screen.getByText("Operational RPO")).toBeDefined();
    expect(screen.getByText("2.1 min")).toBeDefined();
    expect(screen.getByText("Operational RTO")).toBeDefined();
    expect(screen.getByText("7.87 min")).toBeDefined();

    // Spool & WORM Immutability
    expect(screen.getByText("WAL Spool Capacity")).toBeDefined();
    expect(screen.getByText("WORM Immutability")).toBeDefined();
    expect(screen.getByText("COMPLIANCE")).toBeDefined();

    // 16-point matrix
    expect(screen.getByText("16-Point Integrity Matrix")).toBeDefined();
    expect(screen.getByText("16 / 16 Passed (100%)")).toBeDefined();
  });

  it("renders the BackupsSettingsPage with title and description", () => {
    render(<BackupsSettingsPage />);
    expect(screen.getByText("Automated Backup & Disaster Recovery")).toBeDefined();
  });
});

