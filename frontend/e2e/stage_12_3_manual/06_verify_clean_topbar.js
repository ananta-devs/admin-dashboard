const { chromium } = require("playwright");
const path = require("path");

async function checkCleanTopbar() {
  console.log("Launching Chrome to verify clean topbar (no Sign In/Register buttons)...");
  const browser = await chromium.launch({
    headless: false,
    channel: "chrome",
    args: ["--start-maximized"],
  });

  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  
  await page.goto("http://localhost:3000/", { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);

  // Verify Sign In and Register are NOT in header
  const headerSignIn = await page.locator('header button:has-text("Sign In")').count();
  const headerRegister = await page.locator('header button:has-text("Register")').count();
  console.log("Header 'Sign In' button count:", headerSignIn);
  console.log("Header 'Register' button count:", headerRegister);

  if (headerSignIn > 0 || headerRegister > 0) {
    throw new Error("FAIL: Header still contains Sign In or Register button!");
  }

  // Verify User Account Menu is present
  const userMenuBtn = page.locator('header button[aria-label="User Account Menu"]');
  const userMenuVisible = await userMenuBtn.isVisible();
  console.log("Header User Account Menu visible:", userMenuVisible);

  // Take screenshot of clean dashboard
  const screenshotPath = path.resolve(__dirname, "../../../docs/phase-12/stage-12.3-manual-chrome/screenshots/spa-navigation/clean_topbar_dashboard.png");
  await page.screenshot({ path: screenshotPath });
  console.log("Saved screenshot to:", screenshotPath);

  // Click user menu to verify dropdown
  await userMenuBtn.click();
  await page.waitForTimeout(500);
  const dropdownPath = path.resolve(__dirname, "../../../docs/phase-12/stage-12.3-manual-chrome/screenshots/spa-navigation/clean_topbar_user_dropdown.png");
  await page.screenshot({ path: dropdownPath });
  console.log("Saved dropdown screenshot to:", dropdownPath);

  await browser.close();
  console.log("SUCCESS: Top bar is completely clean!");
}

checkCleanTopbar().catch((err) => {
  console.error("Test failed:", err);
  process.exit(1);
});

