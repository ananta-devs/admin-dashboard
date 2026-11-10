import { NextRequest, NextResponse } from "next/server";

export function proxy(request: NextRequest) {
  // Generate a cryptographically secure 16-byte random nonce, base64 encoded (canonical 24-char format)
  const nonce = Buffer.from(crypto.getRandomValues(new Uint8Array(16))).toString("base64");

  const isDev = process.env.NODE_ENV === "development";

  // Construct strict Content-Security-Policy directives
  // In development mode, React / Next.js Fast Refresh and Turbopack require 'unsafe-eval' for callstack reconstruction and HMR
  const scriptSrc = isDev
    ? `script-src 'self' 'nonce-${nonce}' 'strict-dynamic' 'unsafe-eval'`
    : `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`;

  const cspHeader = [
    "default-src 'self'",
    scriptSrc,
    "style-src 'self' 'unsafe-inline'",
    "style-src-attr 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "connect-src 'self'",
  ].join("; ");

  // Check route public vs protected status
  const pathname = request.nextUrl.pathname;
  const isPublicRoute =
    pathname.startsWith("/login") ||
    pathname.startsWith("/register") ||
    pathname.startsWith("/integrations/callback") ||
    pathname.startsWith("/api") ||
    pathname.startsWith("/_next") ||
    pathname.includes(".");

  const sessionCookie = request.cookies.get("vibe_session")?.value;

  // If accessing a protected route without session cookie, redirect to /login
  if (!isPublicRoute && !sessionCookie) {
    const loginUrl = new URL("/login", request.url);
    const redirectRes = NextResponse.redirect(loginUrl);
    redirectRes.headers.set("Content-Security-Policy", cspHeader);
    redirectRes.headers.set("X-Content-Type-Options", "nosniff");
    redirectRes.headers.set("X-Frame-Options", "DENY");
    redirectRes.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
    redirectRes.headers.set(
      "Permissions-Policy",
      "camera=(), microphone=(), geolocation=(), browsing-topics=()"
    );
    return redirectRes;
  }


  // Forward nonce and CSP in request headers so Next.js rendering pipeline attaches nonce to scripts
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", cspHeader);

  // Create response with modified request headers
  const response = NextResponse.next({
    request: {
      headers: requestHeaders,
    },
  });

  // Set standard security headers on HTTP response
  response.headers.set("Content-Security-Policy", cspHeader);
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("X-Frame-Options", "DENY");
  response.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  response.headers.set(
    "Permissions-Policy",
    "camera=(), microphone=(), geolocation=(), browsing-topics=()"
  );

  return response;
}

// Named export 'proxy' (Next.js 16) and default export
export default proxy;

export const config = {
  matcher: [
    /*
     * Match all request paths except for the ones starting with:
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico (favicon file)
     */
    {
      source: "/((?!_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};

