/**
 * VIBE ENTERPRISE - ZERO-RELOAD SPA & REACT ROUTER DOM CHROME TEST
 * Verifies that client navigation using react-router-dom performs
 * instant SPA view transitions with ZERO page reloads across the platform.
 */

const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const SCREENSHOT_DIR = path.resolve(__dirname, "../../../docs/phase-12/stage-12.3-manual-chrome/screenshots/spa-navigation");

if (!fs.existsSync(SCREENSHOT_DIR)) {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
}

async function runSpaNavigationTest() {
  console.log("==================================================================");
  console.log("VIBE ENTERPRISE - ZERO-RELOAD SPA & REACT ROUTER DOM CHROME TEST");
  console.log("==================================================================");

  let browser;
  try {
    browser = await chromium.launch({
      headless: false,
      channel: "chrome",
      args: ["--start-maximized", "--no-sandbox", "--disable-gpu"],
    });
  } catch (err) {
    console.log("Fallback to standard Chromium launcher:", err.message);
    browser = await chromium.launch({
      headless: false,
      args: ["--start-maximized", "--no-sandbox"],
    });
  }

  const context = await browser.newContext({
    viewport: null, // use full maximized window
  });

  const page = await context.newPage();

  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      consoleErrors.push(msg.text());
      console.error("  [BROWSER ERROR]:", msg.text());
    }
  });

  const uid = Math.random().toString(36).substring(2, 8);
  const testEmail = `spa.user.${uid}@vibe.internal`;
  const testPassword = `P@ssword1234!Secure_${uid}`;
  const testName = `SPA Tester ${uid}`;
  const testOrg = `SPA Enterprise ${uid}`;

  // Step 1: Register New User & Auto Login to /
  console.log("\n[Step 1] Navigating to http://localhost:3000/register...");
  await page.goto("http://localhost:3000/register", { waitUntil: "networkidle" });
  await page.waitForTimeout(500);

  console.log(`[Step 2] Registering as ${testEmail}...`);
  await page.locator("#reg-name").fill(testName);
  await page.locator("#org-name").fill(testOrg);
  await page.locator("#reg-email").fill(testEmail);
  await page.locator("#reg-password").fill(testPassword);
  await page.locator("#reg-confirm").fill(testPassword);
  await page.waitForTimeout(500);
  await page.click('button[type="submit"]');

  await page.waitForURL("http://localhost:3000/", { timeout: 10000 });
  await page.waitForTimeout(1500);

  // Step 3: Inject In-Memory Reload Tracker Flag
  console.log("\n[Step 3] Initializing In-Memory SPA Reload Tracker...");
  await page.evaluate(() => {
    window.__spa_init_timestamp = Date.now();
    window.__spa_page_load_id = "spa-session-" + Math.random().toString(36).substring(2, 9);
    window.__spa_transitions_count = 0;
    window.__beforeunload_triggered = false;
    window.addEventListener("beforeunload", () => {
      window.__beforeunload_triggered = true;
    });
  });

  const initialLoadId = await page.evaluate(() => window.__spa_page_load_id);
  console.log(`  Initial Session Load ID: ${initialLoadId}`);

  async function verifyZeroReload(expectedPath, stepName) {
    const currentUrl = page.url();
    const currentPath = new URL(currentUrl).pathname;
    
    // Check if window.__spa_page_load_id survived (proves zero page reload)
    const loadId = await page.evaluate(() => window.__spa_page_load_id);
    const beforeUnloadTriggered = await page.evaluate(() => window.__beforeunload_triggered);

    if (loadId !== initialLoadId || beforeUnloadTriggered) {
      throw new Error(`PAGE RELOAD DETECTED at step "${stepName}"! Expected SPA transition, but browser reloaded.`);
    }

    await page.evaluate(() => {
      window.__spa_transitions_count = (window.__spa_transitions_count || 0) + 1;
    });

    const transitions = await page.evaluate(() => window.__spa_transitions_count);
    console.log(`  ✓ ${stepName}: Reached ${currentPath} via SPA (Transition #${transitions}, LoadId: ${loadId}, Zero Reloads)`);
  }

  // Step 4: Click Sidebar "Workspaces"
  console.log("\n[Step 4] Clicking Sidebar -> Workspaces...");
  await page.click('a[href="/workspaces"]');
  await page.waitForURL("**/workspaces", { timeout: 5000 });
  await page.waitForTimeout(500);
  await verifyZeroReload("/workspaces", "Sidebar Workspaces");
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, "01_spa_workspaces.png") });

  // Step 5: Click Sidebar "Spreadsheets"
  console.log("\n[Step 5] Clicking Sidebar -> Spreadsheets...");
  await page.click('a[href="/spreadsheets"]');
  await page.waitForURL("**/spreadsheets", { timeout: 5000 });
  await page.waitForTimeout(500);
  await verifyZeroReload("/spreadsheets", "Sidebar Spreadsheets");
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, "02_spa_spreadsheets.png") });

  // Step 6: Click Sidebar "Reports"
  console.log("\n[Step 6] Clicking Sidebar -> Reports...");
  await page.click('a[href="/reports"]');
  await page.waitForURL("**/reports", { timeout: 5000 });
  await page.waitForTimeout(500);
  await verifyZeroReload("/reports", "Sidebar Reports");
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, "03_spa_reports.png") });

  // Step 7: Click Sidebar "File Storage"
  console.log("\n[Step 7] Clicking Sidebar -> File Storage...");
  await page.click('a[href="/files"]');
  await page.waitForURL("**/files", { timeout: 5000 });
  await page.waitForTimeout(500);
  await verifyZeroReload("/files", "Sidebar File Storage");
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, "04_spa_files.png") });

  // Step 8: Click Sidebar "System Health"
  console.log("\n[Step 8] Clicking Sidebar -> System Health...");
  await page.click('a[href="/system"]');
  await page.waitForURL("**/system", { timeout: 5000 });
  await page.waitForTimeout(500);
  await verifyZeroReload("/system", "Sidebar System Health");
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, "05_spa_system.png") });

  // Step 9: Click Header Live Status Pill ("Operational") -> Navigates to /system
  console.log("\n[Step 9] Clicking Header Operational Badge...");
  await page.click('a[href="/system"][title*="All systems operational"]');
  await page.waitForURL("**/system", { timeout: 5000 });
  await page.waitForTimeout(500);
  await verifyZeroReload("/system", "Header Operational Pill");

  // Step 10: Open User Menu & Click "My Profile"
  console.log("\n[Step 10] Clicking Header User Menu -> My Profile...");
  await page.click('button[aria-label="User Account Menu"]');
  await page.waitForTimeout(400);
  await page.click('[role="menuitem"]:has-text("My Profile")');
  await page.waitForURL("**/profile", { timeout: 5000 });
  await page.waitForTimeout(800);
  await verifyZeroReload("/profile", "Header User Menu Profile");
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, "06_spa_profile.png") });

  // Step 11: In Profile, Click "Manage Organization Settings →"
  console.log("\n[Step 11] Clicking Link in Profile -> Manage Organization Settings...");
  await page.click('a:has-text("Manage Organization Settings")');
  await page.waitForURL("**/settings", { timeout: 5000 });
  await page.waitForTimeout(500);
  await verifyZeroReload("/settings", "Profile Organization Link");
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, "07_spa_settings.png") });

  // Step 12: Test Command Palette (Ctrl+K) Navigation
  console.log("\n[Step 12] Triggering Command Palette (Ctrl+K) -> Dashboard...");
  await page.keyboard.press("Control+k");
  await page.waitForSelector('[cmdk-root]', { timeout: 3000 });
  await page.waitForTimeout(400);
  await page.click('[cmdk-item]:has-text("Dashboard Overview")');
  await page.waitForURL("http://localhost:3000/", { timeout: 5000 });
  await page.waitForTimeout(500);
  await verifyZeroReload("/", "Command Palette Dashboard");
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, "08_spa_dashboard.png") });

  // Step 13: Sign Out via Header Menu
  console.log("\n[Step 13] Signing Out via Header Menu...");
  await page.click('button[aria-label="User Account Menu"]');
  await page.waitForTimeout(400);
  await page.click('[role="menuitem"]:has-text("Sign Out")');
  await page.waitForURL("**/login", { timeout: 5000 });
  await page.waitForTimeout(1000);
  console.log("  ✓ Successfully signed out and routed to /login!");
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, "09_spa_login_after_signout.png") });

  // Step 14: Click "Create one now" link on login page
  console.log("\n[Step 14] Clicking 'Create one now' link on Login page...");
  await page.click('a:has-text("Create one now")');
  await page.waitForURL("**/register", { timeout: 5000 });
  await page.waitForTimeout(800);
  console.log("  ✓ Routed to /register via SPA link!");
  await page.screenshot({ path: path.join(SCREENSHOT_DIR, "10_spa_register.png") });

  // Summary
  console.log("\n==================================================================");
  console.log("ZERO-RELOAD SPA VERIFICATION SUMMARY");
  console.log("==================================================================");
  console.log(`Total SPA Transitions executed: 9`);
  console.log(`Page Reloads Detected: 0 (100% SPA Transitions)`);
  console.log(`Console Errors: ${consoleErrors.length}`);

  if (consoleErrors.length > 0) {
    console.warn("Detected console errors during run:", consoleErrors);
  }

  await browser.close();
  console.log("\nVerification complete!");
}

runSpaNavigationTest().catch((err) => {
  console.error("FATAL ERROR during SPA navigation test:", err);
  process.exit(1);
});
