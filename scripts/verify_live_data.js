const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  console.log('1. Navigating to login...');
  await page.goto('http://localhost:3000/login');
  await page.fill('input[type="email"]', 'admin@vibe.enterprise');
  await page.fill('input[type="password"]', 'Password1234!');
  await page.click('button[type="submit"]');

  await page.waitForURL('http://localhost:3000/**', { timeout: 10000 });
  console.log('Logged in. Current URL:', page.url());

  const pagesToTest = [
    { name: 'Dashboard', url: 'http://localhost:3000/' },
    { name: 'Spreadsheets', url: 'http://localhost:3000/spreadsheets' },
    { name: 'Analytics', url: 'http://localhost:3000/analytics' },
    { name: 'Users', url: 'http://localhost:3000/users' },
    { name: 'Roles', url: 'http://localhost:3000/roles' },
    { name: 'Audit', url: 'http://localhost:3000/audit' },
    { name: 'Settings', url: 'http://localhost:3000/settings' },
  ];

  for (const p of pagesToTest) {
    console.log(`Checking ${p.name}...`);
    await page.goto(p.url, { waitUntil: 'networkidle' });
    const content = await page.textContent('body');

    if (p.name === 'Spreadsheets') {
      const hasWb = content.includes('Enterprise Financial Model');
      console.log(' - Spreadsheets renders live workbook:', hasWb);
    } else if (p.name === 'Analytics') {
      const hasJob = content.includes('profile_dataset') || content.includes('completed') || content.includes('queued') || content.includes('running');
      console.log(' - Analytics renders live jobs or operations:', hasJob);
    } else if (p.name === 'Users') {
      const hasAdmin = content.includes('admin@vibe.enterprise') || content.includes('Enterprise Admin');
      console.log(' - Users renders live Enterprise Admin:', hasAdmin);
    } else if (p.name === 'Roles') {
      const hasCustom = content.includes('Data Operations Specialist');
      console.log(' - Roles renders live custom role Data Operations Specialist:', hasCustom);
    } else if (p.name === 'Audit') {
      const hasAudit = content.includes('role.created') || content.includes('invitation.created') || content.includes('organization.settings_updated');
      console.log(' - Audit renders live events:', hasAudit);
    } else if (p.name === 'Settings') {
      const hasOrg = content.includes('Acme Corporation');
      console.log(' - Settings renders live organization Acme Corporation:', hasOrg);
    }
  }

  // Take screenshot of Users page
  await page.goto('http://localhost:3000/users', { waitUntil: 'networkidle' });
  await page.screenshot({ path: 'C:/Users/anant/.gemini/antigravity/brain/e16570f2-b8e0-438c-9c36-08751fbe4162/live_users_page.png' });

  // Take screenshot of Roles page
  await page.goto('http://localhost:3000/roles', { waitUntil: 'networkidle' });
  await page.screenshot({ path: 'C:/Users/anant/.gemini/antigravity/brain/e16570f2-b8e0-438c-9c36-08751fbe4162/live_roles_page.png' });

  // Take screenshot of Audit page
  await page.goto('http://localhost:3000/audit', { waitUntil: 'networkidle' });
  await page.screenshot({ path: 'C:/Users/anant/.gemini/antigravity/brain/e16570f2-b8e0-438c-9c36-08751fbe4162/live_audit_page.png' });

  await browser.close();
  console.log('All live page inspections finished successfully!');
})();

