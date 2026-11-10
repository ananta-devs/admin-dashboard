# SECURITY PLAN & THREAT MITIGATION
## Enterprise Admin & Data Intelligence Platform

**Document Version:** 2.0.0  
**Security Standard:** OWASP ASVS 5.0 Level 2 / NIST SP 800-63B  
**Authentication Primitive:** Server-Managed Cryptographic Opaque Sessions

---

## 1. Authentication Lifecycle & Session Engine

```
[ User Action: Login ]
         |
         v
[ FastAPI /api/v1/auth/login ]
  - Verify credentials with Argon2id (memory: 64MB, t=3, p=4)
  - Generate raw token: `t_raw = secrets.token_urlsafe(32)`
  - Compute SHA-256 digest: `t_hash = sha256(t_raw)`
  - Insert record in `user_sessions` (PostgreSQL)
  - Cache active session in Redis DB 1:
      Key: `sess:<t_hash>`
      Value: `{"user_id": ..., "org_id": ..., "roles": [...]}`
      TTL: 86400 seconds (24 hours)
  - Issue Response Header:
      Set-Cookie: vibe_session=t_raw; HttpOnly; Secure; SameSite=Lax; Path=/
```

### 1.1 Invalidation & Session Termination
1. **Single Logout:**
   - Deletes Redis key `sess:<t_hash>`.
   - Sets `revoked_at = NOW()` on the matching `token_hash` in PostgreSQL.
   - Clears cookie in response (`Max-Age=0`).
2. **Global Logout (All Devices):**
   - Updates PostgreSQL: `UPDATE user_sessions SET revoked_at = NOW() WHERE user_id = :uid;`
   - Scans and deletes all Redis keys associated with the user ID (`sess:*`).
3. **Privilege-Change Revocation:**
   - When a user's role or membership is revoked, FastAPI invalidates all cached Redis sessions for that user ID immediately. The next request triggers a fresh database re-authorization check.

---

## 2. Hardened Content Security Policy (CSP)

Implemented in Next.js 16 root middleware:

```typescript
// middleware.ts
import { NextRequest, NextResponse } from 'next/server';

export function middleware(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString('base64');
  const isDev = process.env.NODE_ENV === 'development';

  const scriptSrc = isDev
    ? `'self' 'unsafe-eval' 'nonce-${nonce}'`
    : `'self' 'nonce-${nonce}' 'strict-dynamic'`;

  const cspHeader = `
    default-src 'self';
    script-src ${scriptSrc};
    style-src 'self' 'unsafe-inline';
    img-src 'self' data: blob: https:;
    font-src 'self' data:;
    connect-src 'self' http://localhost:8000;
    frame-ancestors 'none';
    object-src 'none';
    base-uri 'self';
    form-action 'self';
  `.replace(/\s{2,}/g, ' ').trim();

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set('x-nonce', nonce);
  requestHeaders.set('Content-Security-Policy', cspHeader);

  const response = NextResponse.next({
    request: { headers: requestHeaders },
  });
  response.headers.set('Content-Security-Policy', cspHeader);
  response.headers.set('X-Frame-Options', 'DENY');
  response.headers.set('X-Content-Type-Options', 'nosniff');
  response.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
  response.headers.set('Permissions-Policy', 'camera=(), microphone=(), geolocation=()');

  return response;
}
```

---

## 3. Threat Model (STRIDE) & Mitigations

| Threat Category | Target | Architectural Defense Mechanism |
| :--- | :--- | :--- |
| **Spoofing** | User Identity | Argon2id password hashing; 256-bit opaque tokens; TOTP MFA (RFC 6238); HttpOnly cookie storage. |
| **Tampering** | API Requests & DB | Strict Pydantic v2 schemas; parameterized SQLAlchemy queries; database-level trigger protecting `audit_events`. |
| **Repudiation** | Sensitive Admin Actions| Append-only immutable `audit_events` ledger recording actor ID, IP, user-agent, action type, and JSON payload diff. |
| **Information Disclosure** | Credentials & Data | AES-256-GCM encryption at rest for OAuth tokens; zero database error traces leaked in API responses; strict CORS. |
| **Denial of Service** | API & Auth Endpoints | Redis-backed token bucket rate limiter (5 failed logins / 15 mins -> progressive lockout); payload size limits (10MB max). |
| **Elevation of Privilege** | Cross-Tenant Access | Organization context validation on all repositories; PostgreSQL Row-Level Security (RLS) enforcement. |

