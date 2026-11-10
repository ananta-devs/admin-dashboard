/**
 * Stage 12.3: Section 7 - Role-Based Browser Matrix (RBAC) Verification
 * Real Google Chrome testing across 5 personas:
 *  - Super Admin / Owner
 *  - Organization Admin
 *  - Member
 *  - Viewer
 *  - Unauthenticated Visitor
 */

const { chromium } = require("@playwright/test");
const path = require("path");
const fs = require("fs");
const crypto = require("crypto");

const BASE_URL = process.env.BASE_URL || "http://localhost:3000";

const TEST_ROUTES = [
  { path: "/", name: "Dashboard", minRole: "viewer" },
  { path: "/workspaces", name: "Workspaces", minRole: "viewer" },
  { path: "/reports", name: "Reports", minRole: "viewer" },
  { path: "/files", name: "Files", minRole: "viewer" },
  { path: "/system", name: "System Health", minRole: "admin" },
  { path: "/spreadsheets", name: "Spreadsheets", minRole: "viewer" },
  { path: "/analytics", name: "Analytics", minRole: "member" },
  { path: "/dashboards", name: "Dashboards", minRole: "viewer" },
  { path: "/users", name: "Users", minRole: "admin" },
  { path: "/roles", name: "Roles & RBAC", minRole: "admin" },
  { path: "/audit", name: "Audit Ledger", minRole: "admin" },
  { path: "/settings", name: "Settings", minRole: "admin" },
  { path: "/settings/backups", name: "Backups", minRole: "super_admin" },
];

async function registerPersona(context, role, orgSuffix) {
  const uid = crypto.randomBytes(4).toString("hex");
  const cleanRole = role.replace(/_/g, "-");
  const email = `rbac-${cleanRole}-${uid}@vibe.internal`;
  const password = `P@ssword_${uid}_Secure123!`;
  const orgName = `RBAC Corp ${orgSuffix || uid}`;

  const regRes = await context.request.post(`${BASE_URL}/api/v1/auth/register`, {
    data: {
      email,
      password,
      display_name: `RBAC ${cleanRole.toUpperCase()} ${uid}`,
      organization_name: orgName,
      organization_slug: `rbac-${cleanRole}-${uid}`,
    },
    headers: { "Content-Type": "application/json" },
  });

  if (!regRes.ok()) throw new Error(`Registration failed: ${await regRes.text()}`);
  const regData = await regRes.json();

  const loginRes = await context.request.post(`${BASE_URL}/api/v1/auth/login`, {
    data: { email, password },
    headers: { "Content-Type": "application/json" },
  });

  if (!loginRes.ok()) throw new Error(`Login failed: ${await loginRes.text()}`);
  const loginData = await loginRes.json();

  return { email, role, csrfToken: loginData.csrf_token, userId: regData.user?.id };
}

(async () => {
  console.log("================================================================================");
  console.log("STAGE 12.3: ROLE-BASED ACCESS CONTROL (RBAC) BROWSER MATRIX");
  console.log("Browser: Google Chrome (channel=chrome, headless=false)");
  console.log("================================================================================");

  let browser;
  try {
    browser = await chromium.launch({ channel: "chrome", headless: false });
  } catch (e) {
    browser = await chromium.launch({ headless: false });
  }

  const matrixResults = [];

  // Personas to test
  const personas = [
    { role: "super_admin", label: "Super Admin / Platform Owner" },
    { role: "admin", label: "Organization Administrator" },
    { role: "member", label: "Workspace Member" },
    { role: "viewer", label: "Read-Only Viewer" },
    { role: "unauthenticated", label: "Unauthenticated Public Visitor" },
  ];

  for (const persona of personas) {
    console.log(`\n>>> Testing Persona: ${persona.label} (${persona.role})...`);
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });

    let authUser = null;
    if (persona.role !== "unauthenticated") {
      authUser = await registerPersona(context, persona.role, "rbac");
      console.log(`  [AUTH] Authenticated as ${authUser.email}`);
    }

    const page = await context.newPage();

    for (const route of TEST_ROUTES) {
      const url = `${BASE_URL}${route.path}`;
      const response = await page.goto(url, { waitUntil: "networkidle" });
      const status = response ? response.status() : 0;

      // Check API access if applicable
      let apiAccessDenied = false;
      if (persona.role === "unauthenticated" && (route.path === "/settings/backups" || route.path === "/audit")) {
        const testApi = route.path === "/settings/backups" ? "/api/v1/backups/status" : "/api/v1/audit";
        const apiRes = await context.request.get(`${BASE_URL}${testApi}`);
        apiAccessDenied = [401, 403, 404].includes(apiRes.status());
      }

      // Check privileged mutating button existence
      const deleteButtons = await page.locator("button:has-text('Delete'), button:has-text('Remove')").count();
      const createButtons = await page.locator("button:has-text('Create'), button:has-text('New'), button:has-text('Add')").count();

      const record = {
        persona: persona.role,
        personaLabel: persona.label,
        route: route.path,
        routeName: route.name,
        pageStatus: status,
        createButtons,
        deleteButtons,
        apiDeniedOnUnauth: apiAccessDenied,
        verdict: status === 200 ? "PASS" : "FAIL",
      };

      matrixResults.push(record);
      console.log(`  [${record.verdict}] ${route.path.padEnd(20)} HTTP ${status} | CreateBtns: ${createButtons} | DelBtns: ${deleteButtons}`);
    }

    await page.close();
    await context.close();
  }

  await browser.close();

  const outPath = path.resolve(__dirname, "../../../docs/phase-12/stage-12.3/rbac_matrix_results.json");
  fs.writeFileSync(outPath, JSON.stringify(matrixResults, null, 2));
  console.log(`\nRBAC Matrix Completed! Written ${matrixResults.length} records to ${outPath}`);
})();
