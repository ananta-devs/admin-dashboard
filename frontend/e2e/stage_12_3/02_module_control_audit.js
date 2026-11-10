/**
 * Stage 12.3: Section 4 & 5 - Comprehensive Route, Feature & Control Inventory
 * Deep inspection of every interactive UI element across all 14 routes.
 */

const { chromium } = require("@playwright/test");
const path = require("path");
const fs = require("fs");
const crypto = require("crypto");

const BASE_URL = process.env.BASE_URL || "http://localhost:3000";

const ROUTES = [
  { path: "/", name: "Dashboard / Executive Overview" },
  { path: "/workspaces", name: "Workspaces Directory" },
  { path: "/reports", name: "Enterprise Reports Subsystem" },
  { path: "/files", name: "File Storage & Artifacts" },
  { path: "/system", name: "System Health & Diagnostic Telemetry" },
  { path: "/spreadsheets", name: "Virtual Grid Spreadsheets" },
  { path: "/analytics", name: "Analytics & Job Execution Studio" },
  { path: "/dashboards", name: "Dashboard Studio & KPI Cards" },
  { path: "/users", name: "User Administration & Directory" },
  { path: "/roles", name: "Roles & RBAC Permission Catalog" },
  { path: "/audit", name: "Security Audit Logs & Compliance" },
  { path: "/settings", name: "System & Organization Settings" },
  { path: "/settings/backups", name: "Disaster Recovery & Backups" },
  { path: "/integrations", name: "Connected Integrations" },
];

async function createAuthenticatedSession(context) {
  const uid = crypto.randomBytes(4).toString("hex");
  const email = `audit-admin-${uid}@vibe.internal`;
  const password = `P@ssword_${uid}_Secure123!`;

  await context.request.post(`${BASE_URL}/api/v1/auth/register`, {
    data: {
      email,
      password,
      display_name: `Audit Admin ${uid}`,
      organization_name: `Audit Org ${uid}`,
      organization_slug: `audit-org-${uid}`,
    },
    headers: { "Content-Type": "application/json" },
  });

  await context.request.post(`${BASE_URL}/api/v1/auth/login`, {
    data: { email, password },
    headers: { "Content-Type": "application/json" },
  });

  return { email };
}

(async () => {
  console.log("================================================================================");
  console.log("STAGE 12.3: ROUTE, FEATURE & INTERACTIVE CONTROL AUDIT");
  console.log("================================================================================");

  let browser;
  try {
    browser = await chromium.launch({ channel: "chrome", headless: false });
  } catch (e) {
    browser = await chromium.launch({ headless: false });
  }

  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await createAuthenticatedSession(context);
  const page = await context.newPage();

  const auditReport = [];

  for (const r of ROUTES) {
    console.log(`\nAuditing Route: ${r.path} (${r.name})...`);
    await page.goto(`${BASE_URL}${r.path}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(500);

    const title = await page.title();
    const heading = await page.locator("h1, h2, h3").allInnerTexts();

    // Inspect buttons
    const buttons = await page.$$eval("button", (elements) =>
      elements.map((el) => ({
        text: el.innerText.trim().replace(/\n/g, " ") || el.getAttribute("aria-label") || el.getAttribute("title") || "<icon>",
        ariaLabel: el.getAttribute("aria-label"),
        disabled: el.disabled || el.getAttribute("aria-disabled") === "true",
        type: el.getAttribute("type") || "button",
      }))
    );

    // Inspect inputs
    const inputs = await page.$$eval("input, select, textarea", (elements) =>
      elements.map((el) => ({
        tag: el.tagName.toLowerCase(),
        type: el.getAttribute("type") || "text",
        placeholder: el.getAttribute("placeholder") || "",
        disabled: el.disabled,
        name: el.getAttribute("name") || el.getAttribute("id") || "",
      }))
    );

    // Inspect links
    const links = await page.$$eval("a[href]", (elements) =>
      elements.map((el) => ({
        text: el.innerText.trim().replace(/\n/g, " ") || el.getAttribute("aria-label") || "<link>",
        href: el.getAttribute("href"),
      }))
    );

    // Inspect tables
    const tableCount = await page.locator("table").count();
    const tableHeaders = await page.$$eval("th", (elements) => elements.map((e) => e.innerText.trim()));

    // Inspect dialog triggers / badges
    const badges = await page.$$eval("[class*='badge']", (elements) => elements.map((e) => e.innerText.trim()));

    const record = {
      path: r.path,
      name: r.name,
      title,
      headings: heading.slice(0, 5),
      buttonCount: buttons.length,
      sampleButtons: buttons.slice(0, 10),
      disabledButtons: buttons.filter((b) => b.disabled).length,
      inputCount: inputs.length,
      sampleInputs: inputs.slice(0, 8),
      linkCount: links.length,
      sampleLinks: links.slice(0, 8),
      tableCount,
      tableHeaders: tableHeaders.slice(0, 15),
      badgeCount: badges.length,
    };

    console.log(`  Buttons: ${buttons.length} (Disabled: ${record.disabledButtons}) | Inputs: ${inputs.length} | Links: ${links.length} | Tables: ${tableCount}`);
    auditReport.push(record);
  }

  await browser.close();

  const outPath = path.resolve(__dirname, "../../../docs/phase-12/stage-12.3/control_audit_results.json");
  fs.writeFileSync(outPath, JSON.stringify(auditReport, null, 2));
  console.log(`\nAudit finished! Written ${auditReport.length} route audits to ${outPath}`);
})();

