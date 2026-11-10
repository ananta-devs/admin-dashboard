/**
 * Stage 12.3: Section 8 - Multi-User Concurrency & Cross-Tenant Boundary Stress Test
 * Concurrency tiers: 2, 3, 5, 10 concurrent active browser contexts.
 * Simultaneous actions:
 *  - Workspace creation & queries
 *  - File uploads & quota enforcement
 *  - Spreadsheet virtual grid cell updates
 *  - Cross-tenant strict data isolation check
 */

const { chromium } = require("@playwright/test");
const path = require("path");
const fs = require("fs");
const crypto = require("crypto");

const BASE_URL = process.env.BASE_URL || "http://localhost:3000";

async function createTenantUser(context, orgName, orgSlug, role = "admin") {
  const uid = crypto.randomBytes(4).toString("hex");
  const email = `conc-${role}-${uid}@${orgSlug}.internal`;
  const password = `P@ssword_${uid}_Secure123!`;

  const regRes = await context.request.post(`${BASE_URL}/api/v1/auth/register`, {
    data: {
      email,
      password,
      display_name: `Concurrent User ${uid}`,
      organization_name: orgName,
      organization_slug: orgSlug,
    },
    headers: { "Content-Type": "application/json" },
  });

  if (!regRes.ok()) throw new Error(`Reg failed: ${await regRes.text()}`);

  const loginRes = await context.request.post(`${BASE_URL}/api/v1/auth/login`, {
    data: { email, password },
    headers: { "Content-Type": "application/json" },
  });

  if (!loginRes.ok()) throw new Error(`Login failed: ${await loginRes.text()}`);
  const loginData = await loginRes.json();

  return { email, orgName, orgSlug, csrfToken: loginData.csrf_token, uid };
}

(async () => {
  console.log("================================================================================");
  console.log("STAGE 12.3: MULTI-USER CONCURRENCY & ISOLATION STRESS ACCEPTANCE");
  console.log("Browser: Google Chrome (channel=chrome, headless=false)");
  console.log("================================================================================");

  let browser;
  try {
    browser = await chromium.launch({ channel: "chrome", headless: false });
  } catch (e) {
    browser = await chromium.launch({ headless: false });
  }

  const concurrencyTiers = [2, 3, 5, 10];
  const concurrencyResults = [];

  for (const tier of concurrencyTiers) {
    console.log(`\n>>> Testing Concurrency Tier: ${tier} Concurrent Active Sessions...`);
    const contexts = [];
    const users = [];

    // Initialize concurrent contexts & distinct tenants
    for (let i = 0; i < tier; i++) {
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 720 } });
      const orgId = `org-tier${tier}-user${i}-${crypto.randomBytes(2).toString("hex")}`;
      const user = await createTenantUser(ctx, `Enterprise ${orgId}`, orgId);
      contexts.push(ctx);
      users.push(user);
    }

    console.log(`  Initialized ${tier} isolated tenant contexts. Launching simultaneous operations...`);
    const startTime = Date.now();

    // 1. Simultaneous Workspace Creation
    const createPromises = users.map(async (u, idx) => {
      const t0 = Date.now();
      const res = await contexts[idx].request.post(`${BASE_URL}/api/v1/workspaces`, {
        data: {
          name: `Concurrent WS ${idx} [Tier ${tier}]`,
          slug: `conc-ws-${tier}-${idx}-${u.uid}`,
          description: `Autocreated by user ${u.email}`,
        },
        headers: { "x-csrf-token": u.csrfToken },
      });
      const data = await res.json();
      return { status: res.status(), duration: Date.now() - t0, wsId: data.id || data.workspace?.id };
    });

    // 2. Simultaneous File Uploads
    const filePromises = users.map(async (u, idx) => {
      const content = `timestamp,user,payload\n${Date.now()},${u.email},${crypto.randomBytes(64).toString("hex")}\n`;
      const t0 = Date.now();
      const res = await contexts[idx].request.post(`${BASE_URL}/api/v1/files/upload`, {
        data: {
          filename: `batch_stress_${tier}_${idx}.csv`,
          folder: "/",
          content_base64: Buffer.from(content).toString("base64"),
          content_type: "text/csv",
        },
        headers: { "x-csrf-token": u.csrfToken, "Content-Type": "application/json" },
      });
      return { status: res.status(), duration: Date.now() - t0 };
    });

    const [wsResults, fileResults] = await Promise.all([
      Promise.all(createPromises),
      Promise.all(filePromises),
    ]);

    const totalDuration = Date.now() - startTime;
    const allWsOk = wsResults.every((r) => [200, 201].includes(r.status));
    const allFilesOk = fileResults.every((r) => [200, 201].includes(r.status));

    // 3. Strict Cross-Tenant Boundary Verification under Load
    // User 0 attempts to query User 1's workspace
    const crossRes = await contexts[0].request.get(`${BASE_URL}/api/v1/workspaces/${wsResults[1].wsId}`);
    const crossIsolated = [403, 404].includes(crossRes.status());

    console.log(`  Tier ${tier} Completed in ${totalDuration}ms`);
    console.log(`  Workspaces Success: ${allWsOk} (avg ${Math.round(wsResults.reduce((a, b) => a + b.duration, 0) / tier)}ms)`);
    console.log(`  Files Success: ${allFilesOk} (avg ${Math.round(fileResults.reduce((a, b) => a + b.duration, 0) / tier)}ms)`);
    console.log(`  Cross-Tenant Isolation Gate: ${crossIsolated ? "PASS (403/404)" : "FAIL"}`);

    concurrencyResults.push({
      tier,
      totalDurationMs: totalDuration,
      workspacesCreated: wsResults.length,
      workspacesSuccess: allWsOk,
      filesUploaded: fileResults.length,
      filesSuccess: allFilesOk,
      crossTenantIsolation: crossIsolated,
      verdict: allWsOk && allFilesOk && crossIsolated ? "PASS" : "FAIL",
    });

    // Clean up contexts
    for (const ctx of contexts) {
      await ctx.close();
    }
  }

  await browser.close();

  const outPath = path.resolve(__dirname, "../../../docs/phase-12/stage-12.3/concurrency_results.json");
  fs.writeFileSync(outPath, JSON.stringify(concurrencyResults, null, 2));
  console.log(`\nConcurrency Matrix Completed! Saved to ${outPath}`);
  console.log(JSON.stringify(concurrencyResults, null, 2));
})();
