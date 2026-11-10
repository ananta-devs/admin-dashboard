# VIBE — HUMAN TESTER HANDOVER GUIDE
## Comprehensive Manual Exploratory Testing Protocol

**Workspace:** `D:\vibe`  
**Governing Document:** `docs/IMPLEMENTATION_PLAN.md` v2.0.0  
**Phase:** 12 — Production Readiness  
**Target Environment:** Local Standalone Cluster (`http://127.0.0.1:3000`)  
**Recommended Browser:** Google Chrome Version 120+ (Desktop Resolution 1440x900 or 1920x1080)  
**Handover Status:** READY FOR IMMEDIATE TESTING  

---

## 1. QUICK-START ENVIRONMENT VERIFICATION

Before beginning manual exploration, verify that the local services are running:

1. **Check Backend API Health:**
   ```bash
   curl.exe http://127.0.0.1:3000/api/v1/health
   # Expected output: {"status":"ok","version":"1.0.0",...}
   ```
2. **Open Google Chrome:**
   - Launch Chrome and navigate to: `http://127.0.0.1:3000/`
   - Open Developer Tools (`F12` or `Ctrl+Shift+I`) to observe Console and Network tabs.

---

## 2. SUGGESTED MANUAL EXPLORATORY TEST SCENARIOS

### Scenario 1: Fresh Administrative Onboarding & Navigation
1. Open `http://127.0.0.1:3000/`.
2. Inspect the **Executive Command Center**:
   - Check storage utilization card and percentage.
   - Click the "Refresh" telemetry button and verify instant card updates without page refresh.
   - Click the "Phase 2 Active" badge in the top bar.
   - Click the Theme Toggle button (sun/moon) to verify instantaneous switching between light and dark modes.
3. Test Global Search:
   - Press `Ctrl + K` or click the search bar in the topbar.
   - Type `workspaces` or `reports` and press `Enter` to navigate.

### Scenario 2: Multi-Tenant Workspace Lifecycle
1. Click **Workspaces** in the left sidebar (or navigate to `/workspaces`).
2. Click the blue **New Workspace** button.
3. In the modal:
   - Type `Operations Alpha` into the Workspace Name field.
   - Notice that the URL Slug automatically fills with `operations-alpha`.
   - Click **Create Workspace**.
4. Confirm that the new workspace card appears in the list with zero-count resource badges.
5. Click the **Edit** icon on the card, change the name to `Operations Alpha Prime`, and click **Save Changes**.
6. Click the **Delete** icon on the card. Notice that the confirmation button is disabled until you type `operations-alpha`. Type it and click **Delete Workspace**.

### Scenario 3: Virtual Grid Spreadsheets & Formula Calculation
1. Click **Spreadsheets** in the left sidebar (`/spreadsheets`).
2. Scroll through the grid: observe that scrolling is smooth and fast despite the 50,000 virtual rows.
3. Click cell `A2` (contains `North`).
4. Click cell `D2` (contains `=B2 + C2` with computed value `26500`).
5. Double-click cell `B2` and change `12000` to `20000`. Press `Enter`.
   - Observe that cell `D2` immediately recalculates to `34500`!
6. Click the **+** button at the bottom to add a new worksheet tab (`Sheet2`).
7. Click the **Export CSV** button in the toolbar to download the current grid as a file.

### Scenario 4: Dashboard Studio & Dynamic Widgets
1. Click **Dashboards** in the left sidebar (`/dashboards`).
2. Click **New Dashboard**, type `Q4 Operations Dashboard`, and submit.
3. Click **Add Widget**:
   - Choose chart type: Bar Chart or Line Chart.
   - Set title: `Regional Revenue Trends`.
   - Click **Save Widget**.
4. Adjust the Date Range filter and Timezone dropdown in the top filter bar.
5. Click **Duplicate Dashboard** to verify cloning.

### Scenario 5: Enterprise Reports Generation
1. Click **Reports** in the left sidebar (`/reports`).
2. Click **New Report**:
   - Enter title `Monthly Executive Summary`.
   - Set page orientation to Portrait and page size to A4.
   - Click **Create Definition**.
3. In the created definition card, click **Add Section** to attach a KPI Summary or Markdown text block.
4. Click **Run Report**: observe the run status transition from `Queued` to `Completed`.
5. Click **Download PDF** to inspect the generated report output.

### Scenario 6: File Storage, Quota & In-Browser Preview
1. Click **File Storage** in the left sidebar (`/files`).
2. Observe the Storage Quota progress bar at the top.
3. Click **Upload File**:
   - Drag or select any local `.csv`, `.txt`, or image file (`< 25 MB`).
   - Click **Start Upload**.
4. Verify the uploaded file appears in the file table with its exact byte size and SHA-256 checksum.
5. Click the **Preview** (eye) icon: verify the file contents render inside the modal.
6. Click the **Download** icon to test local retrieval.
7. Click the **Delete** icon: confirm deletion and observe the quota bar update.

### Scenario 7: User Administration & Granular RBAC
1. Click **User Management** in the left sidebar (`/users`).
2. Click **Invite User**, enter an email address (e.g. `colleague@enterprise.io`), select the "Member" role, and submit.
3. Click **Roles & RBAC** in the left sidebar (`/roles`).
4. Click **Create Custom Role**:
   - Name: `Financial Analyst`.
   - Slug: `financial-analyst`.
   - Check specific permissions across the 16 available items.
   - Click **Save Custom Role**.

### Scenario 8: System Health Diagnostics & Disaster Recovery
1. Click **System Health** in the left sidebar (`/system`).
2. Inspect the real-time telemetry cards:
   - Backend API uptime and version.
   - PostgreSQL connection pool (pool size, checked in/out).
   - Redis cache latency and memory.
   - Celery background queue counters.
3. Click **Refresh Telemetry** to poll fresh data.
4. Click **Settings -> Automated Backups & DR** (`/settings/backups`):
   - Review PITR RPO/RTO metrics and SLA compliance status.

### Scenario 9: Audit Ledger & Compliance
1. Click **Audit Ledger** in the left sidebar (`/audit`).
2. Observe that every action executed in Scenarios 1-8 generated an immutable audit event in the table.
3. Type a keyword into the Search box to filter logs.
4. Click **View Details** on any row to inspect the tamper-evident JSON metadata.

---

## 3. WHAT TO DO IF AN ISSUE IS OBSERVED

If you observe an unexpected layout glitch, unhandled dialog, or network failure during manual testing:
1. Open Chrome DevTools (`F12`), switch to the **Console** tab, and copy the red error message.
2. Switch to the **Network** tab, click on any red/failed request, and copy the Response payload.
3. Report the issue with the route path and steps to reproduce.

