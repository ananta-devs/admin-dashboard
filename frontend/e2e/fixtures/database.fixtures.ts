import { test as base, expect } from "@playwright/test";

export interface DatabaseVerificationFixture {
  backendVerified: boolean;
  expectedDatabase: string;
}

export const databaseTest = base.extend<{
  dbVerification: DatabaseVerificationFixture;
}>({
  dbVerification: async ({ request }, use) => {
    // Probe backend readiness and verified database identity
    const res = await request.get("http://127.0.0.1:8000/api/v1/ready");
    if (!res.ok()) {
      throw new Error(
        `FATAL: Dedicated E2E backend on port 8000 is not ready! Status: ${res.status()}. ` +
        "Ensure dedicated test backend is launched targeting vibe_enterprise_test."
      );
    }

    const data = await res.json();
    if (!data.e2e_mode) {
      throw new Error(
        "FATAL REFUSAL: Dedicated backend is NOT in verified E2E test mode! " +
        `Response: ${JSON.stringify(data)}. Aborting tests to protect development database.`
      );
    }

    await use({
      backendVerified: true,
      expectedDatabase: "vibe_enterprise_test",
    });
  },
});

export { expect };

