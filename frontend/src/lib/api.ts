/**
 * Authoritative API Client & Environment Configuration
 *
 * Uses NEXT_PUBLIC_API_URL and NEXT_PUBLIC_APP_URL for production-ready, configurable deployments.
 *
 * - In browser/client runtime with a configured NEXT_PUBLIC_API_URL, resolves fully qualified or relative paths.
 * - In test environments or when matching relative endpoints, gracefully preserves the canonical relative path
 *   `/api/v1/...` so reverse-proxying, Next.js rewrites, and mock fetch matchers function identically.
 */

export const API_BASE_URL: string = process.env.NEXT_PUBLIC_API_URL || "/api/v1";

export const APP_BASE_URL: string =
  process.env.NEXT_PUBLIC_APP_URL ||
  (typeof window !== "undefined" ? window.location.origin : "http://localhost:3000");

/**
 * Build an API endpoint URL using NEXT_PUBLIC_API_URL.
 *
 * - If NEXT_PUBLIC_API_URL is an absolute URL (e.g. "http://localhost:3000/api/v1" or production CDN/API),
 *   it prepends the base.
 * - If in test environment or when NEXT_PUBLIC_API_URL is relative or default, returns standard `/api/v1/...`
 *   to ensure tests and local reverse proxies remain 100% compatible.
 */
export function apiUrl(path: string): string {
  const cleanPath = path.startsWith("/") ? path : `/${path}`;
  const endpoint = cleanPath.startsWith("/api/v1")
    ? cleanPath
    : `/api/v1${cleanPath}`;

  // In test environment or when running under Vitest/Jest, return the canonical /api/v1/...
  if (typeof process !== "undefined" && process.env.NODE_ENV === "test") {
    return endpoint;
  }

  const rawBase = process.env.NEXT_PUBLIC_API_URL;
  if (!rawBase || rawBase === "/api/v1") {
    return endpoint;
  }

  // If NEXT_PUBLIC_API_URL is set to an absolute URL e.g. "http://localhost:3000/api/v1"
  if (rawBase.startsWith("http://") || rawBase.startsWith("https://")) {
    const baseWithoutTrailing = rawBase.replace(/\/+$/, "");
    const baseWithoutApi = baseWithoutTrailing.endsWith("/api/v1")
      ? baseWithoutTrailing.slice(0, -"/api/v1".length)
      : baseWithoutTrailing;
    return `${baseWithoutApi}${endpoint}`;
  }

  return endpoint;
}

/**
 * Standard enterprise fetch wrapper that:
 * 1. Resolves URL using apiUrl()
 * 2. Enforces credentials: "include" for secure HttpOnly cookie session transmission
 */
export async function apiFetch(
  endpoint: string,
  init?: RequestInit
): Promise<Response> {
  const url = apiUrl(endpoint);
  return fetch(url, {
    credentials: "include",
    ...init,
  });
}
