const path = require('path');
const fs = require('fs');
const { chromium } = require(path.resolve(__dirname, '../frontend/node_modules/@playwright/test'));

async function main() {
  const imagesDir = path.resolve(__dirname, '../docs/images');
  if (!fs.existsSync(imagesDir)) {
    fs.mkdirSync(imagesDir, { recursive: true });
  }

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: 'dark',
  });

  const page = await context.newPage();

  // 1. Log in
  console.log('Navigating to login...');
  await page.goto('http://localhost:3000/login', { waitUntil: 'networkidle' });
  await page.fill('input#email', 'admin@vibe.enterprise');
  await page.fill('input#password', 'Password1234!');
  await page.click('button[type="submit"]');

  // Wait for redirect to home
  await page.waitForURL('http://localhost:3000/**', { timeout: 10000 });
  await page.waitForTimeout(2000);

  // Set dark theme if not already set
  await page.evaluate(() => {
    document.documentElement.classList.add('dark');
    localStorage.setItem('theme', 'dark');
  });
  await page.waitForTimeout(500);

  // 1. Executive Command Center
  console.log('Capturing Executive Command Center...');
  await page.goto('http://localhost:3000/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(imagesDir, '01-executive-command-center.png'), fullPage: false });

  // 2. Spreadsheets
  console.log('Capturing Spreadsheets...');
  await page.goto('http://localhost:3000/spreadsheets', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(imagesDir, '02-spreadsheets-grid.png'), fullPage: false });

  // 3. Dashboards & ECharts Studio
  console.log('Capturing Dashboards...');
  await page.goto('http://localhost:3000/dashboards', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(imagesDir, '03-analytics-dashboards.png'), fullPage: false });

  // 4. System Health & Disaster Recovery
  console.log('Capturing System Health...');
  await page.goto('http://localhost:3000/system', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(imagesDir, '04-system-health-dr.png'), fullPage: false });

  // 5. Audit Ledger & Governance
  console.log('Capturing Audit Ledger...');
  await page.goto('http://localhost:3000/audit', { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(imagesDir, '05-audit-ledger.png'), fullPage: false });

  console.log('All screenshots captured successfully in docs/images!');
  await browser.close();
}

main().catch(err => {
  console.error('Failed to capture screenshots:', err);
  process.exit(1);
});
