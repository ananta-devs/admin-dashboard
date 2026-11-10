import { test, expect } from "../fixtures/auth.fixtures";
import { registerEntity } from "../helpers/registry.helper";

test.describe("Workflow 6: Analytics Subsystem & Python Sandbox Gate", () => {
  test("6.1 Analytics catalogue lists algorithms with parameter schemas", async ({
    page,
    authenticatedUserPage: _authed,
  }) => {
    await page.goto("/analytics");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 10000 });

    // Verify presence of Operations Catalogue metric card
    await expect(page.getByText(/Operations Catalogue/i).first()).toBeVisible({ timeout: 10000 });

    // Open "New Analysis" dialog to verify algorithm catalogue
    const newAnalysisBtn = page.getByRole("button", { name: /New Analysis/i });
    await expect(newAnalysisBtn).toBeVisible();
    await newAnalysisBtn.click();

    // Verify operations listed in dialogue matching backend registry
    await expect(page.getByText("Dataset Profiling").first()).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("Descriptive Statistics").first()).toBeVisible();
    await expect(page.getByText("Grouped Aggregation").first()).toBeVisible();

    // Verify API operations catalogue response schema
    const opsRes = await page.context().request.get("/api/v1/analytics/operations");
    expect(opsRes.status()).toBe(200);
    const ops: Record<string, unknown>[] = await opsRes.json();
    expect(Array.isArray(ops)).toBe(true);
    const profileOp = ops.find((o) => o.id === "profile_dataset");
    expect(profileOp).toBeDefined();
    expect(profileOp?.parameters_schema).toBeDefined();
  });

  test("6.2 Analytics job submission returns HTTP 202 and supports cooperative cancellation", async ({
    page,
    baseURL,
    runId,
    authenticatedUser,
    authenticatedUserPage: _authed,
  }) => {
    const origin = baseURL || "http://localhost:3000";

    // 1. Fetch CSRF token
    const csrfRes = await page.context().request.post(`${origin}/api/v1/auth/csrf-token`, {
      headers: { Origin: origin },
    });
    const { csrf_token: csrfToken } = await csrfRes.json();

    // 2. Create workbook and sheet as source dataset
    const wbRes = await page.context().request.post(`${origin}/api/v1/spreadsheets/workbooks`, {
      data: {
        workspace_id: authenticatedUser.workspaceId,
        name: `Analytics Source Dataset ${Date.now()}`,
        description: "Source dataset for analytics job pipeline",
      },
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
        Origin: origin,
      },
    });
    expect(wbRes.status()).toBe(201);
    const wb = await wbRes.json();
    registerEntity(runId, "workbook", wb.id);

    const sheetRes = await page.context().request.post(`${origin}/api/v1/spreadsheets/workbooks/${wb.id}/sheets`, {
      data: { name: "Data Sheet" },
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
        Origin: origin,
      },
    });
    expect(sheetRes.status()).toBe(201);
    const sheet = await sheetRes.json();

    // 3. Submit analytics job
    const jobRes = await page.context().request.post(`${origin}/api/v1/analytics/jobs`, {
      data: {
        source_workbook_id: wb.id,
        source_sheet_id: sheet.id,
        operation_id: "describe_columns",
        parameters: { columns: ["A", "B"] },
      },
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
        Origin: origin,
      },
    });

    // Backend must accept with HTTP 202 Accepted
    expect(jobRes.status()).toBe(202);
    const job = await jobRes.json();
    expect(job.id).toBeDefined();
    expect(["queued", "running", "succeeded"]).toContain(job.status);

    // 4. Test cooperative cancellation
    const cancelRes = await page.context().request.post(`${origin}/api/v1/analytics/jobs/${job.id}/cancel`, {
      headers: {
        "X-CSRF-Token": csrfToken,
        Origin: origin,
      },
    });
    expect([200, 204]).toContain(cancelRes.status());

    // Verify cancellation status
    const pollRes = await page.context().request.get(`${origin}/api/v1/analytics/jobs/${job.id}`);
    expect(pollRes.status()).toBe(200);
    const polledJob = await pollRes.json();
    expect(["cancelled", "succeeded"]).toContain(polledJob.status);
  });

  test("6.3 Mandatory Python Sandbox Gate: execution rejected when PYTHON_SANDBOX_ENABLED=false", async ({
    page,
    baseURL,
    authenticatedUser: _user,
    authenticatedUserPage: _authed,
  }) => {
    const origin = baseURL || "http://localhost:3000";

    // 1. Fetch CSRF token
    const csrfRes = await page.context().request.post(`${origin}/api/v1/auth/csrf-token`, {
      headers: { Origin: origin },
    });
    const { csrf_token: csrfToken } = await csrfRes.json();

    // 2. Author a governed Python script (authoring and versioning remain fully operational)
    const scriptTitle = `Custom Statistical Model ${Date.now()}`;
    const createScriptRes = await page.context().request.post(`${origin}/api/v1/scripts`, {
      data: {
        title: scriptTitle,
        description: "Linear regression model with confidence intervals",
        code: "def transform(df, params):\n    return {'status': 'processed', 'rows': len(df)}",
      },
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
        Origin: origin,
      },
    });

    expect(createScriptRes.status()).toBe(201);
    const script = await createScriptRes.json();
    expect(script.id).toBeDefined();
    expect(script.version).toBe(1);

    // 3. Attempt to execute the script in sandbox
    const runRes = await page.context().request.post(`${origin}/api/v1/scripts/${script.id}/run`, {
      data: {
        parameters: { test: true },
      },
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
        Origin: origin,
      },
    });

    expect(runRes.status()).toBe(200);
    const run = await runRes.json();

    // 4. Assert fail-closed administrative sandbox gate
    expect(run.status).toBe("disabled");
    expect(run.stderr_logs).toContain("Script execution is currently disabled pending administrator sandbox verification");
  });
});
