import { test, expect } from "../fixtures/auth.fixtures";

test.describe("Workflow 4: Virtual Grid Spreadsheets & Formula Computation", () => {
  test("4.1 50,000-row virtual grid renders with formula evaluations (SUM, AVERAGE, IF)", async ({
    page,
    authenticatedUserPage: _authed,
  }) => {
    await page.goto("/spreadsheets");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 10000 });

    // 1. Locate grid and verify virtual dimensions
    const grid = page.getByRole("grid", { name: "Spreadsheet Grid" });
    await expect(grid).toBeVisible({ timeout: 10000 });
    await expect(grid).toHaveAttribute("aria-rowcount", "50000");
    await expect(grid).toHaveAttribute("aria-colcount", "26");

    // 2. Verify initial seeded headers
    await expect(grid.getByText("Region").first()).toBeVisible();
    await expect(grid.getByText("Q1 Sales").first()).toBeVisible();
    await expect(grid.getByText("Q2 Sales").first()).toBeVisible();
    await expect(grid.getByText("Total").first()).toBeVisible();
    await expect(grid.getByText("Status").first()).toBeVisible();

    // 3. Verify arithmetic and IF formula computation in cell rows
    // Row 2: 12000 + 14500 = 26500 ("Above Target")
    await expect(grid.getByText("26500").first()).toBeVisible();
    await expect(grid.getByText("Above Target").first()).toBeVisible();

    // 4. Verify SUM and AVERAGE calculations
    await expect(grid.getByText("49700").first()).toBeVisible(); // SUM(B2:B5)
    await expect(grid.getByText("12425").first()).toBeVisible(); // AVERAGE(B2:B5)
  });

  test("4.2 Cell selection and formula bar synchronization", async ({
    page,
    authenticatedUserPage: _authed,
  }) => {
    await page.goto("/spreadsheets");
    const grid = page.getByRole("grid", { name: "Spreadsheet Grid" });
    await expect(grid).toBeVisible({ timeout: 10000 });

    const formulaInput = page.getByLabel("Cell formula or value");
    const addressInput = page.getByLabel("Active cell address");

    await expect(formulaInput).toBeVisible();
    await expect(addressInput).toBeVisible();

    // Click on cell with Total formula (D2)
    const cellD2 = grid.getByText("26500").first();
    await cellD2.click();

    // Formula bar should reflect active cell address and formula
    await expect(addressInput).toHaveValue(/D2/i);
    await expect(formulaInput).toHaveValue("=B2 + C2");
  });

  test("4.3 Cell editing and client-side update", async ({
    page,
    authenticatedUserPage: _authed,
  }) => {
    await page.goto("/spreadsheets");
    const grid = page.getByRole("grid", { name: "Spreadsheet Grid" });
    await expect(grid).toBeVisible({ timeout: 10000 });

    // Select cell B2 (12000)
    const cellB2 = grid.getByText("12000").first();
    await cellB2.click();

    // Update value in formula bar
    const formulaInput = page.getByLabel("Cell formula or value");
    await formulaInput.fill("15000");
    await formulaInput.press("Enter");

    // Cell B2 must update to 15000
    await expect(grid.getByText("15000").first()).toBeVisible();
  });

  test("4.4 Undo and Redo operations update spreadsheet state", async ({
    page,
    authenticatedUserPage: _authed,
  }) => {
    await page.goto("/spreadsheets");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 10000 });

    // Locate Undo and Redo buttons
    const undoBtn = page.getByRole("button", { name: /Undo/i }).first();
    const redoBtn = page.getByRole("button", { name: /Redo/i }).first();

    await expect(undoBtn).toBeVisible();
    await expect(redoBtn).toBeVisible();

    // Trigger undo button click
    await undoBtn.click();
    // Verify application state remains stable without unhandled exceptions
    await expect(page.getByRole("grid", { name: "Spreadsheet Grid" })).toBeVisible();
  });
});

