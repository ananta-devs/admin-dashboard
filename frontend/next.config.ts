import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  poweredByHeader: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    const isProd = process.env.NODE_ENV === "production";
    const rawBackendUrl = process.env.BACKEND_INTERNAL_URL;

    // Distinguish development and production configuration:
    // Production must NEVER silently fallback to an insecure or unverified loopback URL.
    if (isProd && !rawBackendUrl) {
      console.warn(
        "[SECURITY] BACKEND_INTERNAL_URL is not set in production. " +
        "API reverse proxy will not default to loopback. Set BACKEND_INTERNAL_URL explicitly."
      );
    }

    const backendUrl = rawBackendUrl || (isProd ? "" : "http://127.0.0.1:8000");
    if (!backendUrl) {
      return [];
    }

    // Validate that the backend URL specifies a valid HTTP/HTTPS origin
    try {
      const parsed = new URL(backendUrl);
      if (!["http:", "https:"].includes(parsed.protocol)) {
        console.error(`[SECURITY] Invalid BACKEND_INTERNAL_URL protocol: "${parsed.protocol}"`);
        return [];
      }
    } catch (err) {
      console.error(`[SECURITY] Malformed BACKEND_INTERNAL_URL: "${backendUrl}"`, err);
      return [];
    }

    return [
      {
        source: "/api/v1/:path*",
        destination: `${backendUrl}/api/v1/:path*`,
      },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          {
            key: "X-Content-Type-Options",
            value: "nosniff",
          },
          {
            key: "X-Frame-Options",
            value: "DENY",
          },
          {
            key: "Referrer-Policy",
            value: "strict-origin-when-cross-origin",
          },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=(), browsing-topics=()",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
