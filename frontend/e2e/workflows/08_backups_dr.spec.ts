import { test, expect, createSyntheticUser, setupAuthenticatedSession } from "../fixtures/auth.fixtures";

test.describe("Workflow 8: Disaster Recovery & Backup Posture", () => {
  test("8.1 BackupRecoveryCard renders RPO, RTO, WAL spool and WORM immutability metrics", async ({
    page,
    authenticatedUserPage: _authed,
  }) => {
    await page.goto("/settings/backups");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("Automated Backup & Disaster Recovery")).toBeVisible({ timeout: 10000 });

    // Verify SLA Metric Grid
    await expect(page.getByText("Operational RPO").first()).toBeVisible();
    await expect(page.getByText(/SLA Target.*min/i).first()).toBeVisible();

    await expect(page.getByText("Operational RTO").first()).toBeVisible();
    await expect(page.getByText(/SLA Ceiling.*min/i).first()).toBeVisible();

    await expect(page.getByText("WAL Spool Capacity").first()).toBeVisible();
    await expect(page.getByText("WORM Immutability").first()).toBeVisible();

    // Verify Refresh Status button interaction
    const refreshBtn = page.getByRole("button", { name: /Refresh Status/i });
    await expect(refreshBtn).toBeVisible();
    await refreshBtn.click();
    await expect(page.getByText("Automated Backup & Disaster Recovery")).toBeVisible();
  });

  test("8.2 Administrator accesses backup status API and verifies RPO/RTO compliance", async ({
    page,
    baseURL,
    authenticatedUser: _user,
  }) => {
    const origin = baseURL || "http://localhost:3000";

    const res = await page.context().request.get(`${origin}/api/v1/backups/status`);
    expect(res.status()).toBe(200);
    const data = await res.json();

    expect(data.status).toBeDefined();
    expect(data.rpo).toBeDefined();
    expect(data.rpo.target_minutes).toBe(15.0);
    expect(data.rpo.sla_compliant).toBe(true);

    expect(data.rto).toBeDefined();
    expect(data.rto.target_minutes).toBe(60.0);
    expect(data.rto.sla_compliant).toBe(true);

    expect(data.spool).toBeDefined();
    expect(data.gfs_retention).toBeDefined();
    expect(data.gfs_retention.immutability_active).toBe(true);
  });

  test("8.3 Non-admin user and unauthenticated requests are strictly forbidden (401/403)", async ({
    page,
    baseURL,
    runId,
    dbVerification: _db,
  }) => {
    const origin = baseURL || "http://localhost:3000";

    // 1. Unauthenticated request must return 401 Unauthorized
    const unauthContext = await page.context().browser()!.newContext();
    const unauthRes = await unauthContext.request.get(`${origin}/api/v1/backups/status`);
    expect([401, 403]).toContain(unauthRes.status());
    await unauthContext.close();

    // 2. Setup an organization with Admin and invite a Viewer
    const adminUser = createSyntheticUser("admin", "bkporg");
    const adminContext = await page.context().browser()!.newContext();
    const { csrfToken: adminCsrf } = await setupAuthenticatedSession(adminContext, origin, adminUser, runId);

    const rolesRes = await adminContext.request.get(`${origin}/api/v1/organization/roles`);
    const roles: Record<string, unknown>[] = await rolesRes.json();
    const viewerRole = roles.find((r) => r.slug === "viewer" || (typeof r.name === "string" && r.name.toLowerCase() === "viewer"));
    if (!viewerRole) throw new Error("Viewer role not found");

    const viewerEmail = `viewer_${Date.now()}@vibe.internal`;
    const inviteRes = await adminContext.request.post(`${origin}/api/v1/organization/invitations`, {
      data: { email: viewerEmail, role_id: viewerRole.id },
      headers: { "Content-Type": "application/json", "X-CSRF-Token": adminCsrf, Origin: origin },
    });
    const inviteData = await inviteRes.json();
    const inviteToken = inviteData.data?.token || inviteData.token;

    // 3. Register and login Viewer
    const viewerUser = createSyntheticUser("viewer", "bkpvu");
    viewerUser.email = viewerEmail;
    const viewerContext = await page.context().browser()!.newContext();
    await setupAuthenticatedSession(viewerContext, origin, viewerUser, runId);

    const csrfRes = await viewerContext.request.post(`${origin}/api/v1/auth/csrf-token`, { headers: { Origin: origin } });
    const { csrf_token: viewerCsrf } = await csrfRes.json();

    // Accept invite & switch tenant
    await viewerContext.request.post(`${origin}/api/v1/organization/invitations/accept`, {
      data: { token: inviteToken },
      headers: { "Content-Type": "application/json", "X-CSRF-Token": viewerCsrf, Origin: origin },
    });

    await viewerContext.request.post(`${origin}/api/v1/auth/select-tenant`, {
      data: { organization_id: adminUser.organizationId },
      headers: { "Content-Type": "application/json", "X-CSRF-Token": viewerCsrf, Origin: origin },
    });

    // 4. Viewer requests /api/v1/backups/status -> MUST return 403 Forbidden!
    const backupRes = await viewerContext.request.get(`${origin}/api/v1/backups/status`);
    expect(backupRes.status()).toBe(403);

    await adminContext.close();
    await viewerContext.close();
  });
});
