import { test as base, Page, BrowserContext } from "@playwright/test";
import crypto from "crypto";
import { registerEntity, executeTeardownForRun } from "../helpers/registry.helper";

export interface TestUser {
  id?: string;
  email: string;
  password: string;
  displayName: string;
  organizationId?: string;
  organizationName: string;
  organizationSlug: string;
  workspaceId?: string;
  role: "admin" | "member" | "viewer";
}

export interface MultiTenantFixtures {
  runId: string;
  orgAlpha: {
    admin: TestUser;
    member: TestUser;
  };
  orgBeta: {
    admin: TestUser;
    member: TestUser;
  };
}

// Generate unique synthetic test user credentials
export function createSyntheticUser(role: "admin" | "member" | "viewer" = "admin", orgSuffix = ""): TestUser {
  const uid = crypto.randomBytes(4).toString("hex");
  const cleanSuffix = orgSuffix ? `-${orgSuffix.toLowerCase()}` : "";
  return {
    email: `e2e-${role}${cleanSuffix}-${uid}@vibe.internal`,
    password: `P@ssword_${uid}_Secure123!`,
    displayName: `E2E ${role.toUpperCase()} ${uid}`,
    organizationName: `Org ${orgSuffix.toUpperCase() || uid}`,
    organizationSlug: `org-${role}${cleanSuffix}-${uid}`,
    role,
  };
}

/**
 * Register a test user and establish authoritative session cookie in BrowserContext
 * Fail-closed: Throws immediately if backend registration or login fails.
 */
export async function setupAuthenticatedSession(
  context: BrowserContext,
  baseURL: string,
  user: TestUser,
  runId: string
): Promise<{ user: TestUser; csrfToken: string }> {
  const apiContext = context.request;
  const origin = baseURL || "http://localhost:3000";

  // 1. Register test user & organization
  const regRes = await apiContext.post(`${origin}/api/v1/auth/register`, {
    data: {
      email: user.email,
      password: user.password,
      display_name: user.displayName,
      organization_name: user.organizationName,
      organization_slug: user.organizationSlug,
    },
    headers: {
      "Content-Type": "application/json",
      Origin: origin,
    },
  });

  if (!regRes.ok()) {
    const errorBody = await regRes.text();
    throw new Error(
      `FATAL FAIL-CLOSED: Registration failed for user ${user.email} (Status: ${regRes.status()}): ${errorBody}`
    );
  }

  const regData = await regRes.json();
  user.id = regData.user?.id;
  user.organizationId = regData.organization?.id;
  user.workspaceId = regData.workspace?.id;

  if (user.id) {
    registerEntity(runId, "user", user.id);
  }
  if (user.organizationId) {
    registerEntity(runId, "org", user.organizationId);
  }

  // 2. Log in to establish authoritative session cookie in the context
  const loginRes = await apiContext.post(`${origin}/api/v1/auth/login`, {
    data: {
      email: user.email,
      password: user.password,
    },
    headers: {
      "Content-Type": "application/json",
      Origin: origin,
    },
  });

  if (!loginRes.ok()) {
    const loginError = await loginRes.text();
    throw new Error(
      `FATAL FAIL-CLOSED: Login failed for user ${user.email} (Status: ${loginRes.status()}): ${loginError}`
    );
  }

  const loginData = await loginRes.json();
  const csrfToken = loginData.csrf_token || "";
  if (loginData.active_organization?.id) {
    user.organizationId = loginData.active_organization.id;
  }

  return { user, csrfToken };
}

// Extend base test with typed enterprise fixtures
export const test = base.extend<{
  runId: string;
  dbVerification: { backendVerified: boolean; expectedDatabase: string };
  anonymousPage: Page;
  authenticatedUserPage: Page;
  authenticatedUser: TestUser;
  csrfToken: string;
  orgAdminPage: Page;
  orgMemberPage: Page;
  restrictedUserPage: Page;
  multiTenantContext: MultiTenantFixtures;
}>({
  runId: [
    async ({}, use) => {
      const id = `run_${Date.now()}_${crypto.randomBytes(4).toString("hex")}`;
      try {
        await use(id);
      } finally {
        executeTeardownForRun(id);
      }
    },
    { auto: true },
  ],

  dbVerification: async ({ request }, use) => {
    // Fail-closed gate: verify dedicated test backend is live in e2e_mode
    const res = await request.get("http://127.0.0.1:8000/api/v1/ready");
    if (!res.ok()) {
      throw new Error(
        `FATAL: Dedicated E2E backend on port 8000 is not ready! Status: ${res.status()}. ` +
        "Ensure dedicated test backend is launched targeting vibe_enterprise_test."
      );
    }
    const data = await res.json();
    if (!data.e2e_mode) {
      throw new Error(
        "FATAL REFUSAL: Dedicated backend is NOT in verified E2E test mode! " +
        `Response: ${JSON.stringify(data)}. Aborting tests to protect development database.`
      );
    }
    await use({
      backendVerified: true,
      expectedDatabase: "vibe_enterprise_test",
    });
  },

  anonymousPage: async ({ page }, use) => {
    await page.context().clearCookies();
    await use(page);
  },

  authenticatedUser: async ({ page, baseURL, runId, dbVerification: _db }, use) => {
    const syntheticUser = createSyntheticUser("admin");
    const { user } = await setupAuthenticatedSession(
      page.context(),
      baseURL || "http://localhost:3000",
      syntheticUser,
      runId
    );
    await use(user);
  },

  csrfToken: async ({ authenticatedUser: _user, page, baseURL }, use) => {
    // Fetch csrf-token for state-changing requests
    const res = await page.context().request.post(`${baseURL || "http://localhost:3000"}/api/v1/auth/csrf-token`, {
      headers: { Origin: baseURL || "http://localhost:3000" },
    });
    if (res.ok()) {
      const data = await res.json();
      await use(data.csrf_token || "");
    } else {
      await use("");
    }
  },

  authenticatedUserPage: async ({ page, authenticatedUser: _user }, use) => {
    await use(page);
  },

  orgAdminPage: async ({ page, baseURL, runId, dbVerification: _db }, use) => {
    const adminUser = createSyntheticUser("admin", "alpha");
    await setupAuthenticatedSession(
      page.context(),
      baseURL || "http://localhost:3000",
      adminUser,
      runId
    );
    await use(page);
  },

  orgMemberPage: async ({ page, baseURL, runId, dbVerification: _db }, use) => {
    const memberUser = createSyntheticUser("member", "alpha");
    await setupAuthenticatedSession(
      page.context(),
      baseURL || "http://localhost:3000",
      memberUser,
      runId
    );
    await use(page);
  },

  restrictedUserPage: async ({ page, baseURL, runId, dbVerification: _db }, use) => {
    const viewerUser = createSyntheticUser("viewer", "beta");
    await setupAuthenticatedSession(
      page.context(),
      baseURL || "http://localhost:3000",
      viewerUser,
      runId
    );
    await use(page);
  },

  multiTenantContext: async ({ browser, baseURL, runId, dbVerification: _db }, use) => {
    const origin = baseURL || "http://localhost:3000";

    // Setup Org Alpha
    const alphaAdmin = createSyntheticUser("admin", "alpha");
    const alphaMember = createSyntheticUser("member", "alpha");
    const alphaContext = await browser.newContext();
    await setupAuthenticatedSession(alphaContext, origin, alphaAdmin, runId);
    await setupAuthenticatedSession(alphaContext, origin, alphaMember, runId);

    // Setup Org Beta
    const betaAdmin = createSyntheticUser("admin", "beta");
    const betaMember = createSyntheticUser("member", "beta");
    const betaContext = await browser.newContext();
    await setupAuthenticatedSession(betaContext, origin, betaAdmin, runId);
    await setupAuthenticatedSession(betaContext, origin, betaMember, runId);

    await use({
      runId,
      orgAlpha: {
        admin: alphaAdmin,
        member: alphaMember,
      },
      orgBeta: {
        admin: betaAdmin,
        member: betaMember,
      },
    });

    await alphaContext.close();
    await betaContext.close();
  },
});

export { expect } from "@playwright/test";
