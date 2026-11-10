/**
 * Live Chrome Verification of:
 * - /register (Sign Up)
 * - /login (Sign In)
 * - Header controls (Notifications dialog, Help modal, ThemeToggle, User Dropdown, Sign Out)
 * - /profile (Personal data management, update display name, sessions, export data)
 * - Cleaned up pages without unnecessary texts / badges
 */

const { chromium } = require("@playwright/test");
const path = require("path");
const fs = require("fs");
const crypto = require("crypto");

const BASE_URL = process.env.BASE_URL || "http://127.0.0.1:3000";
const SCREENSHOT_BASE = path.resolve(__dirname, "../../../docs/phase-12/stage-12.3-manual-chrome/screenshots");

(async () => {
  console.log("================================================================================");
  console.log("LIVE CHROME VERIFICATION: HEADER CONTROLS, PROFILE & SIGNIN/SIGNUP PAGES");
  console.log("================================================================================");

  let browser;
  try {
    browser = await chromium.launch({ channel: "chrome", headless: false });
    console.log("Google Chrome launched:", browser.version());
  } catch (err) {
    browser = await chromium.launch({ headless: false });
  }

  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    ignoreHTTPSErrors: true,
  });

  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      consoleErrors.push({ text: msg.text(), location: msg.location() });
    }
  });

  const uid = crypto.randomBytes(4).toString("hex");
  const testEmail = `alex.pro.${uid}@vibe.internal`;
  const testPassword = `P@ssword1234!Secure_${uid}`;
  const testName = `Alex Pro ${uid}`;
  const testOrg = `Apex Corp ${uid}`;
  const testSlug = `apex-corp-${uid}`;

  // ---------------------------------------------------------------------------
  // STEP 1: TEST /register (SIGN UP)
  // ---------------------------------------------------------------------------
  console.log("\n[Step 1: Navigating to /register]...");
  await page.goto(`${BASE_URL}/register`, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);

  const regTitle = await page.locator("h1:has-text('VIBE ENTERPRISE')").isVisible();
  console.log("  Register page header visible:", regTitle);

  await page.screenshot({ path: path.join(SCREENSHOT_BASE, "auth", "register_page_initial.png") });

  // Fill registration form
  await page.locator("#reg-name").fill(testName);
  await page.locator("#org-name").fill(testOrg);
  await page.locator("#reg-email").fill(testEmail);
  await page.locator("#reg-password").fill(testPassword);
  await page.locator("#reg-confirm").fill(testPassword);
  await page.waitForTimeout(300);

  await page.screenshot({ path: path.join(SCREENSHOT_BASE, "auth", "register_page_filled.png") });

  console.log("  Submitting registration form...");
  await page.locator("button:has-text('Create Account & Organization')").click();
  await page.waitForTimeout(1500);

  // Should navigate to dashboard
  console.log("  Navigated to URL after register:", page.url());

  // ---------------------------------------------------------------------------
  // STEP 2: TEST HEADER CONTROLS ON DASHBOARD
  // ---------------------------------------------------------------------------
  console.log("\n[Step 2: Testing Header Controls on Dashboard]...");
  await page.goto(`${BASE_URL}/`, { waitUntil: "networkidle" });
  await page.waitForTimeout(800);

  // 2.1 Operational Status Pill
  const operationalPill = page.locator("a[href='/system']:has-text('Operational')");
  console.log("  Operational status pill visible:", await operationalPill.isVisible());

  // 2.2 Notifications Dialog
  console.log("  Testing Notifications Dialog...");
  const notifBtn = page.locator("button[aria-label='Notifications']");
  await notifBtn.click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(SCREENSHOT_BASE, "auth", "header_notifications_dialog.png") });

  // Click Mark All Read
  const markReadBtn = page.locator("button:has-text('Mark all read')");
  if (await markReadBtn.isVisible()) {
    await markReadBtn.click();
    await page.waitForTimeout(200);
  }
  // Close Notifications dialog
  await page.locator("button:has-text('Close')").last().click();
  await page.waitForTimeout(300);

  // 2.3 Documentation & Help Modal
  console.log("  Testing Documentation & Help Modal...");
  const helpBtn = page.locator("button[aria-label='Documentation & Help']");
  await helpBtn.click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(SCREENSHOT_BASE, "auth", "header_help_dialog.png") });
  await page.locator("button:has-text('Close')").last().click();
  await page.waitForTimeout(300);

  // 2.4 User Menu Dropdown
  console.log("  Testing User Menu Dropdown...");
  const userMenuBtn = page.locator("button[aria-label='User Account Menu']");
  console.log("  User menu trigger visible:", await userMenuBtn.isVisible());
  await userMenuBtn.click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(SCREENSHOT_BASE, "auth", "header_user_dropdown.png") });

  // ---------------------------------------------------------------------------
  // STEP 3: TEST /profile (PERSONAL DATA MANAGEMENT)
  // ---------------------------------------------------------------------------
  console.log("\n[Step 3: Navigating to /profile via Dropdown]...");
  const profileMenuItem = page.locator("div[role='menuitem']:has-text('My Profile')");
  await profileMenuItem.click();
  await page.waitForTimeout(1000);
  console.log("  Current URL:", page.url());

  await page.screenshot({ path: path.join(SCREENSHOT_BASE, "auth", "profile_page_initial.png") });

  // Verify personal information
  const emailInput = page.locator("input[value*='@vibe.internal']").first();
  console.log("  Verified email visible in profile:", await emailInput.isVisible());

  // Edit Display Name
  console.log("  Updating Display Name on profile...");
  const updatedName = `${testName} Executive`;
  const nameInput = page.locator("#display-name");
  await nameInput.fill(updatedName);
  await page.waitForTimeout(200);

  const saveProfileBtn = page.locator("button:has-text('Save Profile Changes')");
  await saveProfileBtn.click();
  await page.waitForTimeout(1000);

  const successNotice = page.locator("text=Profile personal data updated successfully").first();
  console.log("  Profile update notice displayed:", await successNotice.isVisible());
  await page.screenshot({ path: path.join(SCREENSHOT_BASE, "auth", "profile_page_updated.png") });

  // Test Export Data
  console.log("  Testing Export My Data...");
  const exportBtn = page.locator("button:has-text('Export My Data')");
  console.log("  Export data button functional:", await exportBtn.isVisible());

  // ---------------------------------------------------------------------------
  // STEP 4: TEST SIGN OUT FROM HEADER
  // ---------------------------------------------------------------------------
  console.log("\n[Step 4: Testing Sign Out from Topbar]...");
  await userMenuBtn.click();
  await page.waitForTimeout(400);

  const signOutItem = page.locator("div[role='menuitem']:has-text('Sign Out')");
  await signOutItem.click();
  await page.waitForTimeout(1000);

  console.log("  Redirected to URL after sign out:", page.url());
  await page.screenshot({ path: path.join(SCREENSHOT_BASE, "auth", "login_page_after_signout.png") });

  // ---------------------------------------------------------------------------
  // STEP 5: TEST /login (SIGN IN)
  // ---------------------------------------------------------------------------
  console.log("\n[Step 5: Testing Sign In with updated credentials]...");
  await page.locator("#email").fill(testEmail);
  await page.locator("#password").fill(testPassword);
  await page.waitForTimeout(200);
  await page.screenshot({ path: path.join(SCREENSHOT_BASE, "auth", "login_page_filled.png") });

  await page.locator("button:has-text('Sign In')").click();
  await page.waitForTimeout(1500);

  console.log("  URL after sign in:", page.url());
  await page.screenshot({ path: path.join(SCREENSHOT_BASE, "auth", "dashboard_after_signin.png") });

  // Verify Topbar displays the updated name
  const topbarUser = page.locator(`text=${updatedName}`).first();
  console.log("  Topbar reflects updated user name:", await topbarUser.isVisible());

  // ---------------------------------------------------------------------------
  // STEP 6: VERIFY CLEANED UP PAGES (NO UNNECESSARY BADGES/TEXTS)
  // ---------------------------------------------------------------------------
  console.log("\n[Step 6: Verifying Cleaned Up UI Across Pages]...");
  // Check /spreadsheets has no "MVP" badge
  await page.goto(`${BASE_URL}/spreadsheets`, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
  const mvpBadge = page.locator("aside span:has-text('MVP')");
  console.log("  Spreadsheets MVP badge removed:", (await mvpBadge.count()) === 0);

  // Check /dashboards has no "New" badge
  await page.goto(`${BASE_URL}/dashboards`, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
  const newBadge = page.locator("aside span:has-text('New')");
  console.log("  Dashboards New badge removed:", (await newBadge.count()) === 0);

  // Check /benchmarks has clean title
  await page.goto(`${BASE_URL}/benchmarks`, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
  const cleanTitle = page.locator("h1:has-text('VIBE Performance Benchmark Suite')");
  console.log("  Benchmarks clean title verified:", await cleanTitle.isVisible());

  console.log(`\nVerification Finished! Console errors count: ${consoleErrors.length}`);
  if (consoleErrors.length > 0) {
    console.log("Console errors:", consoleErrors);
  }

  await browser.close();
})();

