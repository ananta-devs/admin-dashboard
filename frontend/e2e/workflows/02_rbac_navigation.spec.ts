import { test, expect, createSyntheticUser, setupAuthenticatedSession } from "../fixtures/auth.fixtures";
import { registerEntity } from "../helpers/registry.helper";

test.describe("Workflow 2: Role-Based Access Control (RBAC) & Navigation", () => {
  test("2.1 Administrator accesses Roles page and inspects system roles & permissions catalogue", async ({
    page,
    authenticatedUserPage: _authed,
  }) => {
    // Navigate to /roles
    await page.goto("/roles");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

    // Verify presence of system roles (Owner, Admin, Member, Viewer)
    await expect(page.getByText("Owner", { exact: true }).first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("Admin", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Member", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Viewer", { exact: true }).first()).toBeVisible();

    // Verify permission catalogue sections
    await expect(page.getByText(/Organization Administration|Permissions/i).first()).toBeVisible();
  });

  test("2.2 Administrator creates custom role and verifies audit event generation", async ({
    page,
    baseURL,
    runId,
    dbVerification: _db,
  }) => {
    const origin = baseURL || "http://localhost:3000";
    const adminUser = createSyntheticUser("admin", "rbac");
    const { csrfToken } = await setupAuthenticatedSession(page.context(), origin, adminUser, runId);

    // Create custom role via API
    const customRoleName = `Custom Analyst ${Date.now()}`;
    const customRoleSlug = `analyst-${Date.now().toString(36)}`;

    const createRes = await page.context().request.post(`${origin}/api/v1/organization/roles`, {
      data: {
        name: customRoleName,
        slug: customRoleSlug,
        description: "Custom role created during E2E testing",
        permission_codes: ["organization.read", "workspaces.read"],
      },
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
        Origin: origin,
      },
    });

    expect(createRes.status()).toBe(201);
    const roleData = await createRes.json();
    expect(roleData.name).toBe(customRoleName);
    expect(roleData.is_system).toBe(false);

    // Navigate to /roles and verify the custom role is visible in UI
    await page.goto("/roles");
    await expect(page.getByText(customRoleName).first()).toBeVisible({ timeout: 10000 });

    // Verify audit log records role.created event
    const auditRes = await page.context().request.get(`${origin}/api/v1/organization/audit-logs?action=role.created`);
    if (auditRes.ok()) {
      const auditData = await auditRes.json();
      const events: Record<string, unknown>[] = Array.isArray(auditData) ? auditData : auditData.items || [];
      const match = events.find((e) => e.target_id === roleData.id || e.action === "role.created");
      expect(match).toBeDefined();
    }
  });

  test("2.3 Member/Viewer role is forbidden from administrative mutating endpoints", async ({
    page,
    baseURL,
    runId,
    dbVerification: _db,
  }) => {
    const origin = baseURL || "http://localhost:3000";

    // 1. Setup organization with Admin
    const adminUser = createSyntheticUser("admin", "rbacorg");
    const { csrfToken: adminCsrf } = await setupAuthenticatedSession(page.context(), origin, adminUser, runId);

    // 2. Fetch 'viewer' role ID
    const rolesRes = await page.context().request.get(`${origin}/api/v1/organization/roles`);
    expect(rolesRes.status()).toBe(200);
    const roles: Record<string, unknown>[] = await rolesRes.json();
    const viewerRole = roles.find((r) => r.slug === "viewer" || (typeof r.name === "string" && r.name.toLowerCase() === "viewer"));
    expect(viewerRole).toBeDefined();
    if (!viewerRole) throw new Error("Viewer role not found");

    // 3. Create invitation for viewer user
    const viewerEmail = `viewer_${Date.now()}@vibe.internal`;
    const inviteRes = await page.context().request.post(`${origin}/api/v1/organization/invitations`, {
      data: {
        email: viewerEmail,
        role_id: viewerRole.id,
      },
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": adminCsrf,
        Origin: origin,
      },
    });
    expect(inviteRes.status()).toBe(200);
    const inviteData = await inviteRes.json();
    const inviteToken = inviteData.data?.token || inviteData.token;
    expect(inviteToken).toBeTruthy();

    // 4. Register the new viewer user (in a separate browser context)
    const viewerUser = createSyntheticUser("viewer", "rbacu");
    viewerUser.email = viewerEmail;
    const viewerContext = await page.context().browser()!.newContext();

    const regRes = await viewerContext.request.post(`${origin}/api/v1/auth/register`, {
      data: {
        email: viewerUser.email,
        password: viewerUser.password,
        display_name: viewerUser.displayName,
        organization_name: viewerUser.organizationName,
        organization_slug: viewerUser.organizationSlug,
      },
      headers: {
        "Content-Type": "application/json",
        Origin: origin,
      },
    });
    expect(regRes.status()).toBe(201);
    const regData = await regRes.json();
    registerEntity(runId, "user", regData.user.id);
    registerEntity(runId, "org", regData.organization.id);

    // Log in viewer
    const loginRes = await viewerContext.request.post(`${origin}/api/v1/auth/login`, {
      data: { email: viewerUser.email, password: viewerUser.password },
      headers: { "Content-Type": "application/json", Origin: origin },
    });
    expect(loginRes.status()).toBe(200);
    const loginData = await loginRes.json();
    const viewerCsrf = loginData.csrf_token;

    // 5. Viewer accepts invitation to join Admin's organization
    const acceptRes = await viewerContext.request.post(`${origin}/api/v1/organization/invitations/accept`, {
      data: { token: inviteToken },
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": viewerCsrf,
        Origin: origin,
      },
    });
    expect(acceptRes.status()).toBe(200);

    // Switch viewer's active organization to the target org
    const switchRes = await viewerContext.request.post(`${origin}/api/v1/auth/select-tenant`, {
      data: { organization_id: adminUser.organizationId },
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": viewerCsrf,
        Origin: origin,
      },
    });
    expect(switchRes.status()).toBe(200);
    const switchData = await switchRes.json();
    const switchedCsrf = switchData.csrf_token || viewerCsrf;

    // 6. Viewer attempts to create a custom role -> must be rejected with 403 Forbidden
    const forbiddenCreateRes = await viewerContext.request.post(`${origin}/api/v1/organization/roles`, {
      data: {
        name: "Unauthorized Role",
        slug: "unauthorized-role",
        permission_codes: ["roles.read"],
      },
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": switchedCsrf,
        Origin: origin,
      },
    });
    expect(forbiddenCreateRes.status()).toBe(403);

    // 7. Viewer attempts to access backup recovery status -> must be rejected with 403
    const backupRes = await viewerContext.request.get(`${origin}/api/v1/backups/status`);
    expect(backupRes.status()).toBe(403);

    await viewerContext.close();
  });
});
