/**
 * Stage 12.3.2: Viewport Matrix & Visual Responsiveness Verification
 * Primary Browser: Google Chrome (headed/visible mode)
 * Viewports:
 *  - Desktop: 1920 x 1080
 *  - Laptop: 1366 x 768
 *  - Compact Desktop: 1280 x 720
 *  - Tablet Portrait: 768 x 1024
 *  - Mobile Portrait: 393 x 851
 */

const { chromium } = require("@playwright/test");
const path = require("path");
const fs = require("fs");
const crypto = require("crypto");

const BASE_URL = process.env.BASE_URL || "http://localhost:3000";
const SCREENSHOT_DIR = path.resolve(__dirname, "../../../docs/phase-12/stage-12.3/screenshots");

if (!fs.existsSync(SCREENSHOT_DIR)) {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
}

const VIEWPORTS = [
  { name: "desktop_1080p", width: 1920, height: 1080, isMobile: false },
  { name: "laptop_768p", width: 1366, height: 768, isMobile: false },
  { name: "compact_720p", width: 1280, height: 720, isMobile: false },
  { name: "tablet_1024p", width: 768, height: 1024, isMobile: false },
  { name: "mobile_851p", width: 393, height: 851, isMobile: true, hasTouch: true },
];

const ROUTES = [
  { path: "/", name: "dashboard" },
  { path: "/workspaces", name: "workspaces" },
  { path: "/reports", name: "reports" },
  { path: "/files", name: "files" },
  { path: "/system", name: "system" },
  { path: "/spreadsheets", name: "spreadsheets" },
  { path: "/analytics", name: "analytics" },
  { path: "/dashboards", name: "dashboards" },
  { path: "/users", name: "users" },
  { path: "/roles", name: "roles" },
  { path: "/audit", name: "audit" },
  { path: "/settings", name: "settings" },
  { path: "/settings/backups", name: "backups" },
  { path: "/integrations", name: "integrations" },
];

async function createAuthenticatedSession(context) {
  const uid = crypto.randomBytes(4).toString("hex");
  const email = `chrome-vp-${uid}@vibe.internal`;
  const password = `P@ssword_${uid}_Secure123!`;
  const orgName = `VP Org ${uid}`;

  // 1. Register
  const regRes = await context.request.post(`${BASE_URL}/api/v1/auth/register`, {
    data: {
      email,
      password,
      display_name: `Chrome VP Admin ${uid}`,
      organization_name: orgName,
      organization_slug: `vp-org-${uid}`,
    },
    headers: { "Content-Type": "application/json" },
  });

  if (!regRes.ok()) {
    throw new Error(`Registration failed: ${await regRes.text()}`);
  }

  // 2. Login
  const loginRes = await context.request.post(`${BASE_URL}/api/v1/auth/login`, {
    data: { email, password },
    headers: { "Content-Type": "application/json" },
  });

  if (!loginRes.ok()) {
    throw new Error(`Login failed: ${await loginRes.text()}`);
  }

  console.log(`[AUTH] Successfully established session for ${email}`);
  return { email, orgName };
}

(async () => {
  console.log("================================================================================");
  console.log("STAGE 12.3.2: REAL GOOGLE CHROME VIEWPORT MATRIX VERIFICATION");
  console.log("Browser: Google Chrome (channel=chrome, headless=false)");
  console.log("================================================================================");

  let browser;
  try {
    browser = await chromium.launch({
      channel: "chrome",
      headless: false,
      args: ["--disable-blink-features=AutomationControlled"],
    });
  } catch (e) {
    console.warn("Falling back to bundled chromium:", e.message);
    browser = await chromium.launch({ headless: false });
  }

  const results = [];

  for (const vp of VIEWPORTS) {
    console.log(`\n>>> Testing Viewport: ${vp.name} (${vp.width}x${vp.height}, isMobile=${vp.isMobile})`);
    const context = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      isMobile: vp.isMobile,
      hasTouch: vp.hasTouch || false,
    });

    try {
      await createAuthenticatedSession(context);
      const page = await context.newPage();

      for (const route of ROUTES) {
        const url = `${BASE_URL}${route.path}`;
        const startTime = Date.now();
        const response = await page.goto(url, { waitUntil: "networkidle", timeout: 15000 });
        const latency = Date.now() - startTime;
        const status = response ? response.status() : 0;

        // Verify Brand Title
        const title = await page.title();

        // Check layout integrity
        let sidebarOrMenuVisible = false;
        let brandVisible = false;

        if (vp.isMobile) {
          // Check mobile hamburger button
          const menuBtn = page.getByRole("button", { name: /open mobile navigation menu/i });
          sidebarOrMenuVisible = await menuBtn.isVisible();
          if (sidebarOrMenuVisible) {
            // Open mobile drawer
            await menuBtn.click();
            await page.waitForTimeout(200);
            brandVisible = await page.locator("[role='dialog']").getByText("VIBE ENTERPRISE").first().isVisible();
            // Close mobile drawer by pressing Escape
            await page.keyboard.press("Escape");
            await page.waitForTimeout(100);
          }
        } else {
          // Check desktop sidebar
          const sidebar = page.locator("aside").first();
          sidebarOrMenuVisible = await sidebar.isVisible();
          brandVisible = await page.locator("aside").getByText("VIBE ENTERPRISE").first().isVisible();
        }

        // Take screenshot
        const screenshotFile = `vp_${vp.name}_${route.name}.png`;
        const screenshotPath = path.join(SCREENSHOT_DIR, screenshotFile);
        await page.screenshot({ path: screenshotPath, fullPage: false });

        const record = {
          viewport: vp.name,
          resolution: `${vp.width}x${vp.height}`,
          route: route.path,
          name: route.name,
          status,
          latencyMs: latency,
          title,
          brandVisible,
          navigationAccessible: sidebarOrMenuVisible,
          screenshot: screenshotFile,
          verdict: status === 200 && brandVisible ? "PASS" : "FAIL",
        };

        results.push(record);
        console.log(`  [${record.verdict}] ${route.path.padEnd(20)} HTTP ${status} | Latency: ${latency}ms | Nav: ${sidebarOrMenuVisible}`);
      }
      await page.close();
    } catch (err) {
      console.error(`  [ERROR] Viewport ${vp.name} failed:`, err.message);
    } finally {
      await context.close();
    }
  }

  await browser.close();

  // Save audit log
  const summaryPath = path.resolve(__dirname, "../../../docs/phase-12/stage-12.3/viewport_matrix_results.json");
  fs.writeFileSync(summaryPath, JSON.stringify(results, null, 2));
  console.log(`\nSaved ${results.length} viewport records to ${summaryPath}`);

  const passed = results.filter((r) => r.verdict === "PASS").length;
  console.log(`TOTAL: ${results.length} | PASSED: ${passed} | FAILED: ${results.length - passed}`);
})();
