import { test, expect } from "../fixtures/auth.fixtures";
import { registerEntity } from "../helpers/registry.helper";

test.describe("Workflow 5: Dashboard Studio & Visualizations", () => {
  test("5.1 Create dashboard, configure widgets, and render visualizations", async ({
    page,
    baseURL,
    runId,
    authenticatedUser,
    authenticatedUserPage: _authed,
  }) => {
    const origin = baseURL || "http://localhost:3000";
    const dashTitle = `BI Executive Studio ${Date.now()}`;

    // 1. Fetch CSRF token
    const csrfRes = await page.context().request.post(`${origin}/api/v1/auth/csrf-token`, {
      headers: { Origin: origin },
    });
    const csrfData = await csrfRes.json();
    const csrfToken = csrfData.csrf_token;

    // 2. Create dashboard directly with user's validated workspace_id
    const createDashRes = await page.context().request.post(`${origin}/api/v1/dashboards`, {
      data: {
        workspace_id: authenticatedUser.workspaceId,
        title: dashTitle,
        description: "Executive analytical KPI and revenue dashboard",
        layout_config: { version: 1, timezone: "UTC" },
      },
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
        Origin: origin,
      },
    });
    expect(createDashRes.status()).toBe(201);
    const createdDash = await createDashRes.json();
    registerEntity(runId, "dashboard", createdDash.id);

    // 3. Create a workbook and sheet to obtain a real source sheet for the widget
    const createWbRes = await page.context().request.post(`${origin}/api/v1/spreadsheets/workbooks`, {
      data: {
        workspace_id: authenticatedUser.workspaceId,
        name: `Dashboard Data Source ${Date.now()}`,
        description: "Backing data source for dashboard widgets",
      },
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
        Origin: origin,
      },
    });
    expect(createWbRes.status()).toBe(201);
    const createdWb = await createWbRes.json();
    registerEntity(runId, "workbook", createdWb.id);

    const createSheetRes = await page.context().request.post(`${origin}/api/v1/spreadsheets/workbooks/${createdWb.id}/sheets`, {
      data: { name: "Sheet 1" },
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
        Origin: origin,
      },
    });
    expect(createSheetRes.status()).toBe(201);
    const createdSheet = await createSheetRes.json();
    const sourceSheetId = createdSheet.id;

    // 4. Add KPI Card widget to dashboard
    const addWidgetRes = await page.context().request.post(
      `${origin}/api/v1/dashboards/${createdDash.id}/widgets`,
      {
        data: {
          title: "Total Enterprise Revenue",
          widget_type: "kpi_card",
          source_type: "sheet",
          source_id: sourceSheetId,
          query_config: {
            metrics: ["revenue"],
            target: 1000000,
            comparison_label: "vs Q1 Target",
          },
          position_x: 0,
          position_y: 0,
          width: 6,
          height: 4,
          sort_order: 0,
        },
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Origin: origin,
        },
      }
    );
    expect(addWidgetRes.status()).toBe(201);

    // 5. Navigate to /dashboards and verify dashboard and widget rendering
    await page.goto("/dashboards");
    const dashboardSelect = page.getByLabel("Select Active Dashboard");
    await expect(dashboardSelect).toBeVisible({ timeout: 10000 });
    await expect(dashboardSelect).toHaveValue(createdDash.id);
    await expect(page.getByText("Total Enterprise Revenue").first()).toBeVisible({ timeout: 10000 });

    // 5. Global Filter Bar presence
    await expect(page.getByText(/Filters|UTC/i).first()).toBeVisible();

    // 6. Duplicate Dashboard via API contract & verify UI sync
    const dupRes = await page.context().request.post(`${origin}/api/v1/dashboards/${createdDash.id}/duplicate`, {
      data: { title: `${dashTitle} (Copy)` },
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
        Origin: origin,
      },
    });
    expect(dupRes.status()).toBe(201);
    const clonedDash = await dupRes.json();
    registerEntity(runId, "dashboard", clonedDash.id);

    // Reload page to verify duplicated dashboard is listed in the dropdown
    await page.reload();
    await expect(dashboardSelect).toBeVisible({ timeout: 10000 });
    await expect(dashboardSelect.locator("option")).toHaveCount(2);

    // 7. Delete Dashboard via Toolbar
    const deleteBtn = page.getByTitle(/Delete dashboard permanently/i).or(page.getByRole("button", { name: /Delete/i }).first());
    if (await deleteBtn.isVisible()) {
      page.once("dialog", (dialog) => dialog.accept());
      await deleteBtn.click();
    }
  });
});
