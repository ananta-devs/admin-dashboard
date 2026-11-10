import { test, expect, createSyntheticUser, setupAuthenticatedSession } from "../fixtures/auth.fixtures";
import { registerEntity } from "../helpers/registry.helper";

test.describe("Workflow 1: Authentication & Session Lifecycle", () => {
  test("1.1 User registration creates account, organization, and authoritative session", async ({
    page,
    baseURL,
    runId,
    dbVerification: _db,
  }) => {
    const origin = baseURL || "http://localhost:3000";
    const syntheticUser = createSyntheticUser("admin", "auth1");

    // Perform registration via API to establish user & org
    const regRes = await page.context().request.post(`${origin}/api/v1/auth/register`, {
      data: {
        email: syntheticUser.email,
        password: syntheticUser.password,
        display_name: syntheticUser.displayName,
        organization_name: syntheticUser.organizationName,
        organization_slug: syntheticUser.organizationSlug,
      },
      headers: {
        "Content-Type": "application/json",
        Origin: origin,
      },
    });

    expect(regRes.status()).toBe(201);
    const regData = await regRes.json();
    expect(regData.user).toBeDefined();
    expect(regData.user.email).toBe(syntheticUser.email);
    expect(regData.organization.slug).toBe(syntheticUser.organizationSlug);

    // Register for cleanup
    registerEntity(runId, "user", regData.user.id);
    registerEntity(runId, "org", regData.organization.id);

    // Perform login
    const loginRes = await page.context().request.post(`${origin}/api/v1/auth/login`, {
      data: {
        email: syntheticUser.email,
        password: syntheticUser.password,
      },
      headers: {
        "Content-Type": "application/json",
        Origin: origin,
      },
    });

    expect(loginRes.status()).toBe(200);
    const loginData = await loginRes.json();
    expect(loginData.csrf_token).toBeTruthy();

    // Verify cookies in context include vibe_session
    const cookies = await page.context().cookies();
    const sessionCookie = cookies.find((c) => c.name === "vibe_session");
    expect(sessionCookie).toBeDefined();
    expect(sessionCookie?.httpOnly).toBe(true);

    // Navigate to dashboard and verify authenticated shell
    await page.goto("/");
    await expect(page.getByLabel("User Avatar")).toBeVisible({ timeout: 10000 });
  });

  test("1.2 Page reload and navigation maintains authenticated session persistence", async ({
    page,
    authenticatedUserPage: _authed,
    authenticatedUser,
  }) => {
    await page.goto("/");
    await expect(page.getByLabel("User Avatar")).toBeVisible({ timeout: 10000 });

    // Navigate to spreadsheets
    await page.goto("/spreadsheets");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

    // Reload page
    await page.reload();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByLabel("User Avatar")).toBeVisible();

    // Verify session user details via /auth/me
    const meRes = await page.context().request.get("/api/v1/auth/me");
    expect(meRes.status()).toBe(200);
    const meData = await meRes.json();
    expect(meData.user.email).toBe(authenticatedUser.email);
  });

  test("1.3 Explicit logout terminates session and invalidates cookies", async ({
    page,
    baseURL,
    runId,
    dbVerification: _db,
  }) => {
    const origin = baseURL || "http://localhost:3000";
    const syntheticUser = createSyntheticUser("admin", "logout");
    const { csrfToken } = await setupAuthenticatedSession(page.context(), origin, syntheticUser, runId);

    // Call logout endpoint
    const logoutRes = await page.context().request.post(`${origin}/api/v1/auth/logout`, {
      headers: {
        "X-CSRF-Token": csrfToken,
        Origin: origin,
      },
    });
    expect([200, 204]).toContain(logoutRes.status());

    // Subsequent call to /auth/me must return 401 Unauthorized
    const meRes = await page.context().request.get(`${origin}/api/v1/auth/me`);
    expect(meRes.status()).toBe(401);
  });

  test("1.4 Invalid credentials are rejected with HTTP 401", async ({
    page,
    baseURL,
    dbVerification: _db,
  }) => {
    const origin = baseURL || "http://localhost:3000";
    const badLoginRes = await page.context().request.post(`${origin}/api/v1/auth/login`, {
      data: {
        email: "nonexistent_attacker@vibe.internal",
        password: "IncorrectPassword123!",
      },
      headers: {
        "Content-Type": "application/json",
        Origin: origin,
      },
    });

    expect(badLoginRes.status()).toBe(401);
  });

  test("1.5 Missing or forged CSRF tokens reject mutating operations with HTTP 403", async ({
    page,
    baseURL,
    authenticatedUser: _user,
  }) => {
    const origin = baseURL || "http://localhost:3000";

    // Attempt mutating action without CSRF header
    const noCsrfRes = await page.context().request.post(`${origin}/api/v1/organization/roles`, {
      data: {
        name: "Malicious Role",
        slug: "malicious-role",
        permission_codes: ["roles.read"],
      },
      headers: {
        "Content-Type": "application/json",
        Origin: origin,
      },
    });
    expect(noCsrfRes.status()).toBe(403);

    // Attempt mutating action with forged CSRF header
    const forgedCsrfRes = await page.context().request.post(`${origin}/api/v1/organization/roles`, {
      data: {
        name: "Forged Role",
        slug: "forged-role",
        permission_codes: ["roles.read"],
      },
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": "forged_invalid_csrf_token_value_12345",
        Origin: origin,
      },
    });
    expect(forgedCsrfRes.status()).toBe(403);
  });
});
