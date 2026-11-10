import { describe, it, expect } from "vitest";
import { NextRequest } from "next/server";
import { proxy } from "../proxy";

describe("Phase 11 Gate 3: Frontend CSP & Security Headers", () => {
  it("generates a unique, non-empty cryptographic nonce for each request", () => {
    const req1 = new NextRequest("http://localhost:3000/");
    const req2 = new NextRequest("http://localhost:3000/");

    const res1 = proxy(req1);
    const res2 = proxy(req2);

    const csp1 = res1.headers.get("content-security-policy");
    const csp2 = res2.headers.get("content-security-policy");

    expect(csp1).toBeDefined();
    expect(csp2).toBeDefined();
    expect(csp1).not.toBeNull();
    expect(csp2).not.toBeNull();

    // Extract nonces
    const nonceMatch1 = csp1?.match(/'nonce-([A-Za-z0-9+/=_-]+)'/);
    const nonceMatch2 = csp2?.match(/'nonce-([A-Za-z0-9+/=_-]+)'/);

    expect(nonceMatch1).not.toBeNull();
    expect(nonceMatch2).not.toBeNull();
    expect(nonceMatch1![1]).toBeDefined();
    expect(nonceMatch2![1]).toBeDefined();

    // Must be unique between distinct requests
    expect(nonceMatch1![1]).not.toEqual(nonceMatch2![1]);

    // Must be canonical 16-byte base64 encoding (24 chars ending in == or =)
    expect(nonceMatch1![1].length).toBe(24);
  });

  it("enforces strict, bounded CSP directives without unsafe-eval or broad wildcards", () => {
    const req = new NextRequest("http://localhost:3000/dashboards");
    const res = proxy(req);
    const csp = res.headers.get("content-security-policy") || "";

    // Required security directives
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("'strict-dynamic'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("base-uri 'self'");
    expect(csp).toContain("form-action 'self'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("connect-src 'self'");

    // Prohibited unsafe patterns and loopback origin exposure
    expect(csp).not.toContain("'unsafe-eval'");
    expect(csp).not.toContain("*"); // No wildcard origins
    expect(csp).not.toContain("localhost:8000"); // No loopback origin in connect-src
  });

  it("sets all enterprise HTTP security headers", () => {
    const req = new NextRequest("http://localhost:3000/");
    const res = proxy(req);

    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(res.headers.get("x-frame-options")).toBe("DENY");
    expect(res.headers.get("referrer-policy")).toBe("strict-origin-when-cross-origin");
    expect(res.headers.get("permissions-policy")).toContain("camera=()");
    expect(res.headers.get("permissions-policy")).toContain("microphone=()");
    expect(res.headers.get("permissions-policy")).toContain("geolocation=()");
  });
});
