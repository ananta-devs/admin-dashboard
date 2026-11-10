import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import UsersPage from "@/app/users/page";
import RolesPage from "@/app/roles/page";
import AuditPage from "@/app/audit/page";
import SettingsPage from "@/app/settings/page";

describe("Phase 5 Organization Administration Pages", () => {
  it("renders the Users & Invitations administration page", () => {
    render(<UsersPage />);
    expect(screen.getByText("Members & Invitations")).toBeDefined();
    expect(screen.getByText("Alex Mercer")).toBeDefined();
    expect(screen.getByText("alex.mercer@enterprise.io")).toBeDefined();
    expect(screen.getByText("Invite Member")).toBeDefined();

    // Switch to Pending Invitations tab
    const invitesTab = screen.getByText(/Pending Invitations/i);
    fireEvent.click(invitesTab);
    expect(screen.getByText("dev.candidate@partner.com")).toBeDefined();
  });

  it("renders the 403 simulated state on Users page", () => {
    render(<UsersPage />);
    const simButton = screen.getByText("Simulate 403");
    fireEvent.click(simButton);
    expect(screen.getByText(/403 Forbidden: Insufficient Permissions/i)).toBeDefined();
  });

  it("renders the Roles & Permissions administration page", () => {
    render(<RolesPage />);
    expect(screen.getByText("Roles & Permissions")).toBeDefined();
    expect(screen.getAllByText("Owner").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Admin").length).toBeGreaterThan(0);
    expect(screen.getByText("Create Custom Role")).toBeDefined();
    expect(screen.getByText("organization Capabilities")).toBeDefined();
  });

  it("renders the Audit Ledger page and metadata inspector", () => {
    render(<AuditPage />);
    expect(screen.getByText("Administrative Audit Ledger")).toBeDefined();
    expect(screen.getByText("organization.ownership_transferred")).toBeDefined();
    expect(screen.getByText("role.created")).toBeDefined();

    // Inspect metadata
    const inspectButtons = screen.getAllByText("Inspect");
    expect(inspectButtons.length).toBeGreaterThan(0);
    fireEvent.click(inspectButtons[0]);
    expect(screen.getByText(/Sanitized Event Metadata/i)).toBeDefined();
  });

  it("renders the 403 simulated state on Audit Ledger page", () => {
    render(<AuditPage />);
    const simButton = screen.getByText("Simulate 403");
    fireEvent.click(simButton);
    expect(screen.getByText(/403 Forbidden: Insufficient Permissions/i)).toBeDefined();
    expect(screen.getByText(/Only organization Owners and Admins may access this ledger/i)).toBeDefined();
  });

  it("renders the Organization Settings & Ownership Transfer page", () => {
    render(<SettingsPage />);
    expect(screen.getByText("Organization Settings")).toBeDefined();
    expect(screen.getByText("Transfer Primary Organization Ownership")).toBeDefined();
    expect(screen.getByText("Transfer Ownership")).toBeDefined();

    // Open transfer modal
    const transferBtn = screen.getByText("Transfer Ownership");
    fireEvent.click(transferBtn);
    expect(screen.getByText(/Confirm Organization Ownership Transfer/i)).toBeDefined();
  });
});

