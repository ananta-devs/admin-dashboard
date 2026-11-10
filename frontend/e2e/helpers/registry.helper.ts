import { execFileSync } from "child_process";
import path from "path";

export interface RunScopedRegistry {
  runId: string;
  orgIds: Set<string>;
  userIds: Set<string>;
  workbookIds: Set<string>;
  dashboardIds: Set<string>;
}

const activeRegistries = new Map<string, RunScopedRegistry>();

export function getOrCreateRegistry(runId: string): RunScopedRegistry {
  if (!activeRegistries.has(runId)) {
    activeRegistries.set(runId, {
      runId,
      orgIds: new Set<string>(),
      userIds: new Set<string>(),
      workbookIds: new Set<string>(),
      dashboardIds: new Set<string>(),
    });
  }
  return activeRegistries.get(runId)!;
}

export function registerEntity(
  runId: string,
  type: "org" | "user" | "workbook" | "dashboard",
  id: string
): void {
  if (!id) return;
  const reg = getOrCreateRegistry(runId);
  switch (type) {
    case "org":
      reg.orgIds.add(id);
      break;
    case "user":
      reg.userIds.add(id);
      break;
    case "workbook":
      reg.workbookIds.add(id);
      break;
    case "dashboard":
      reg.dashboardIds.add(id);
      break;
  }
}

export function executeTeardownForRun(runId: string): { success: boolean; details: string } {
  const reg = activeRegistries.get(runId);
  if (!reg) {
    return { success: true, details: `No active registry found for runId: ${runId}` };
  }

  const pythonBin = path.resolve(__dirname, "../../../backend/.venv/Scripts/python.exe");
  const cleanupScript = path.resolve(__dirname, "../../../backend/scripts/e2e_cleanup.py");

  const args = [
    cleanupScript,
    "--run-id",
    runId,
    "--expected-db",
    "vibe_enterprise_test",
  ];

  if (reg.orgIds.size > 0) {
    args.push("--org-ids", ...Array.from(reg.orgIds));
  }
  if (reg.userIds.size > 0) {
    args.push("--user-ids", ...Array.from(reg.userIds));
  }

  try {
    const stdout = execFileSync(pythonBin, args, {
      encoding: "utf-8",
      env: {
        ...process.env,
        DATABASE_SYNC_URL: "postgresql+psycopg://vibe_admin:dev_secret_postgres_pass_change_in_prod@127.0.0.1:5432/vibe_enterprise_test",
        E2E_EXPECTED_DATABASE: "vibe_enterprise_test",
      },
    });
    activeRegistries.delete(runId);
    return { success: true, details: stdout };
  } catch (err: unknown) {
    const errorMsg = err instanceof Error ? err.message : String(err);
    console.error(`[E2E CLEANUP ERROR] Run ${runId} cleanup failed:`, errorMsg);
    return { success: false, details: errorMsg };
  }
}

