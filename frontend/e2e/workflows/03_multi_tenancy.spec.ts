import { test, expect, createSyntheticUser, setupAuthenticatedSession } from "../fixtures/auth.fixtures";
import { registerEntity } from "../helpers/registry.helper";

test.describe("Workflow 3: Multi-Tenant Boundary Isolation", () => {
  test("3.1 Strict boundary isolation between Org Alpha and Org Beta resources", async ({
    browser,
    baseURL,
    runId,
    dbVerification: _db,
  }) => {
    const origin = baseURL || "http://localhost:3000";

    // 1. Setup Tenant Alpha
    const alphaAdmin = createSyntheticUser("admin", "talpha");
    const alphaContext = await browser.newContext();
    const { csrfToken: alphaCsrf } = await setupAuthenticatedSession(
      alphaContext,
      origin,
      alphaAdmin,
      runId
    );

    // 2. Setup Tenant Beta
    const betaAdmin = createSyntheticUser("admin", "tbeta");
    const betaContext = await browser.newContext();
    const { csrfToken: betaCsrf } = await setupAuthenticatedSession(
      betaContext,
      origin,
      betaAdmin,
      runId
    );

    // 3. Org Alpha creates a private Workbook
    const createWbRes = await alphaContext.request.post(`${origin}/api/v1/spreadsheets/workbooks`, {
      data: {
        workspace_id: alphaAdmin.workspaceId,
        name: `Alpha Confidential Model ${Date.now()}`,
        description: "Strictly confidential Org Alpha financials",
      },
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": alphaCsrf,
        Origin: origin,
      },
    });
    expect(createWbRes.status()).toBe(201);
    const alphaWb = await createWbRes.json();
    registerEntity(runId, "workbook", alphaWb.id);

    // 4. Org Alpha creates a private Dashboard
    const createDashRes = await alphaContext.request.post(`${origin}/api/v1/dashboards`, {
      data: {
        workspace_id: alphaWb.workspace_id,
        title: `Alpha Executive Dashboard ${Date.now()}`,
        description: "Confidential executive overview",
        layout_config: { version: 1, timezone: "UTC" },
      },
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": alphaCsrf,
        Origin: origin,
      },
    });
    expect(createDashRes.status()).toBe(201);
    const alphaDash = await createDashRes.json();
    registerEntity(runId, "dashboard", alphaDash.id);

    // 5. Org Beta queries workbooks list -> must not contain Org Alpha's workbook
    const betaWbsRes = await betaContext.request.get(`${origin}/api/v1/spreadsheets/workbooks`);
    expect(betaWbsRes.status()).toBe(200);
    const betaWbs: Record<string, unknown>[] = await betaWbsRes.json();
    const leakedWb = betaWbs.find((w) => w.id === alphaWb.id || w.name === alphaWb.name);
    expect(leakedWb).toBeUndefined();

    // 6. Org Beta queries dashboards list -> must not contain Org Alpha's dashboard
    const betaDashesRes = await betaContext.request.get(`${origin}/api/v1/dashboards`);
    expect(betaDashesRes.status()).toBe(200);
    const betaDashes: Record<string, unknown>[] = await betaDashesRes.json();
    const leakedDash = betaDashes.find((d) => d.id === alphaDash.id || d.title === alphaDash.title);
    expect(leakedDash).toBeUndefined();

    // 7. Direct resource access attack: Org Beta requests Org Alpha workbook by ID -> must fail closed (404 or 403)
    const directWbAccess = await betaContext.request.get(`${origin}/api/v1/spreadsheets/workbooks/${alphaWb.id}`);
    expect([403, 404]).toContain(directWbAccess.status());

    // 8. Direct resource access attack: Org Beta requests Org Alpha dashboard by ID -> must fail closed (404 or 403)
    const directDashAccess = await betaContext.request.get(`${origin}/api/v1/dashboards/${alphaDash.id}`);
    expect([403, 404]).toContain(directDashAccess.status());

    // 9. Direct mutation attack: Org Beta attempts to delete Org Alpha dashboard -> must fail closed (403 or 404)
    const attackDeleteRes = await betaContext.request.delete(`${origin}/api/v1/dashboards/${alphaDash.id}`, {
      headers: {
        "X-CSRF-Token": betaCsrf,
        Origin: origin,
      },
    });
    expect([403, 404]).toContain(attackDeleteRes.status());

    // 10. Verify Org Alpha dashboard was unaffected by Org Beta's attack
    const alphaDashVerify = await alphaContext.request.get(`${origin}/api/v1/dashboards/${alphaDash.id}`);
    expect(alphaDashVerify.status()).toBe(200);

    await alphaContext.close();
    await betaContext.close();
  });
});
