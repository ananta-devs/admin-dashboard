# Authentication, Session Management & Account Security

This document outlines the authoritative design and implementation of the Phase 4 authentication, session management, and account security subsystem for the VIBE Enterprise Admin & Data Intelligence Platform.

---

## 1. Authentication Architecture & Separation of Concerns

VIBE maintains a strict architectural decoupling between core system boundaries:

| Concern | Primary Model / Entity | Responsibility |
| :--- | :--- | :--- |
| **Identity Profile** | `User` (`users` table) | Core identity, display name, verified email state. Auth-mechanism agnostic. |
| **Credential Store** | `UserCredentials` (`user_credentials` table) | Isolated Argon2id password hash, password update timestamps, algorithm version. |
| **Session State** | `AuthenticationSession` (`authentication_sessions`) | Authoritative server-side browser session in PostgreSQL. Holds token digest, CSRF digest, active tenant, idle/absolute expiry. |
| **Action Tokens** | `AuthenticationActionToken` (`authentication_action_tokens`) | Single-use cryptographic action tokens for email verification and password reset. |
| **MFA Credentials** | `MfaCredential` (`mfa_credentials`) | AES-256-GCM encrypted TOTP secret, key version, enabled timestamp, `last_accepted_step` replay counter. |
| **MFA Recovery** | `MfaRecoveryCode` (`mfa_recovery_codes`) | Single-use backup recovery codes, stored as normalized SHA-256 digests. |
| **MFA Challenges** | `MfaLoginChallenge` (`mfa_login_challenges`) | Short-lived (5m) pending login challenges issued when primary auth succeeds but 2FA is required. |
| **Tenant Context** | `OrganizationMembership` (`organization_memberships`) | Maps user identities to tenant organizations. Validated on every tenant-scoped operation in PostgreSQL. |

---

## 2. Password Security & Argon2id Hashing

### Algorithm Configuration
- **Type**: Argon2id (`argon2.Type.ID`)
- **Memory Cost**: $64\text{ MiB}$ (`65536` KiB)
- **Time Cost**: $3$ iterations
- **Parallelism**: $1$ thread (configurable)
- **Salt Length**: $16$ bytes (cryptographically secure random)
- **Output Hash Length**: $32$ bytes

### Password Policy
- **Minimum Length**: 12 characters
- **Maximum Length**: 1024 bytes (UTF-8 encoded)
- **Character Policy**: Unicode and spaces permitted without trimming or destructive modifications.

### Constant-Time Verification, Timing Mitigation & Transparent Rehashing
- **Timing Difference Mitigation**: When a user attempts to authenticate with an email that does not exist in the database, a precomputed dummy Argon2id hash is verified using the same operational cost parameters. This significantly mitigates computational timing differences between valid and invalid email addresses, preventing naive account enumeration.
- **Transparent Rehashing**: When operational parameters or Argon2id configurations are updated, `check_needs_rehash` detects outdated hashes upon successful authentication. A fresh hash is computed and stored using an atomic compare-and-swap update (`WHERE user_id = :user_id AND password_hash = :expected_old_hash`), ensuring concurrent password changes are not inadvertently clobbered. Outdated hashes are never upgraded on failed authentication attempts.

---

## 3. Server-Side Session Management

### Session Token Structure
- **Entropy**: Cryptographically secure 256-bit opaque tokens generated via `secrets.token_urlsafe(32)`.
- **Database Storage**: Raw tokens are **never** stored in plaintext. Only the SHA-256 hex digest (`token_hash`) is persisted in PostgreSQL.
- **Authoritative Store**: PostgreSQL is the single authoritative source of session validity. A revoked or expired row in PostgreSQL invalidates the session immediately across all services.

### Lifetime & Expiration Policy
- **Idle Timeout**: 30 minutes (`1800` seconds). Extended on user activity.
- **Absolute Lifetime**: 12 hours (`43200` seconds). Hard upper bound; sessions cannot be extended beyond this limit.
- **Throttled Activity Touch**: To prevent database write exhaustion, `last_seen_at` and `idle_expires_at` are only updated if at least 60 seconds have elapsed since the prior touch.

### Revocation
- **Single Session**: Updates `revoked_at = NOW()` immediately.
- **Revoke All for User**: Invalidator sets `revoked_at = NOW()` across all active sessions for a user (with optional exclusion for the caller's current session during password changes).

---

## 4. Secure Browser Cookie Policy

Sessions are transmitted to browsers via secure HTTP-only cookies:
- **Cookie Name**: `__Host-vibe_session` in production with HTTPS (`vibe_session` in local development).
- **Security Flags**:
  - `HttpOnly`: Strictly prevents access via JavaScript (`document.cookie`), mitigating XSS session theft.
  - `Secure`: Enforced in production; browser transmits cookie only over HTTPS.
  - `SameSite=Lax`: Defends against Cross-Site Request Forgery (CSRF) on cross-origin requests.
  - `Path=/`: Restricts cookie to entire application domain.
- **Automatic Stale-Cookie Clearing on 401**:
  - When a client sends a stale, revoked, or unrecognized session cookie, the backend's global `AuthenticationError` exception handler automatically issues `Set-Cookie: vibe_session=""; Max-Age=0; Path=/; SameSite=Lax; HttpOnly`.
  - This immediately clears the invalid cookie from the client browser, preventing infinite 401 redirect loops when backend databases are reset or sessions expire.
- **Edge Middleware & Content Security Policy (Next.js 16)**:
  - In development mode (`NODE_ENV=development`), the proxy middleware dynamically permits `'unsafe-eval'` within `script-src` to support React Fast Refresh, Turbopack source mapping, and HMR callstack reconstruction, while enforcing strict nonce-based CSP in production.


---

## 5. Session-Bound CSRF Protection & Request Origin Validation

- **Generation & Model Storage**: A separate 256-bit cryptographically secure token is generated alongside each session and hashed into `csrf_token_hash`.
- **Validation**:
  - **Exempt Methods**: Safe HTTP methods (`GET`, `HEAD`, `OPTIONS`, `TRACE`) are exempt.
  - **Origin & Referer Header Verification**: Validates the request `Origin` (or parsed `Referer` when Origin is absent) against the exact configured allowed origins (`settings.ALLOWED_ORIGINS`). Cross-site requests from untrusted origins or with `Sec-Fetch-Site: cross-site` are rejected immediately with `403 Forbidden` (`CsrfError`).
  - **State-Changing Methods**: `POST`, `PUT`, `PATCH`, `DELETE` require the `X-CSRF-Token` HTTP header matching the session digest via constant-time `hmac.compare_digest`.
  - **Session Revocation Safeguards**: Both `POST /api/v1/auth/logout` and `POST /api/v1/auth/logout-all` strictly enforce CSRF and Origin checks, ensuring malicious third-party origins cannot revoke user sessions via cross-site requests.
  - **Token Bootstrap & Rotation (`POST /api/v1/auth/csrf-token`)**:
    - **Method Behavior**: Token rotation is strictly executed via `POST`. Calling `GET /api/v1/auth/csrf-token` returns `405 Method Not Allowed`, ensuring read-only HTTP semantics are preserved.
    - **Authentication & Origin**: Requires a valid active session and validates Origin/Referer/Fetch Metadata. Does not require the prior CSRF token (serving as the bootstrap and rotation endpoint).
    - **Caching Prevention**: Returns `Cache-Control: no-store, no-cache, must-revalidate, private` and `Pragma: no-cache`.
    - **Deterministic Multi-Tab Handling**: Simultaneous requests from multiple browser tabs are synchronized via Redis. Requests within the active generation window (10s) receive identical active tokens, while previous tokens remain valid during a 60-second grace period (`csrf_grace:{session.id}:{token_hash}`) to prevent sibling tab failure.
  - **Login & Registration CSRF Defense**:
    - Unauthenticated cookie-issuing endpoints (`POST /register`, `POST /login`, `POST /login/mfa`) are protected by `verify_auth_request_origin`.
    - Browsers submitting cross-site requests (`Origin` not in `ALLOWED_ORIGINS` or `Sec-Fetch-Site: cross-site`) are blocked with `403 Forbidden` (`CSRF_VERIFICATION_FAILED`), preventing Login CSRF and unauthorized account provisioning without requiring pre-session tokens.

---

## 6. Rate Limiting Architecture, Outage Posture & Key Separation

- **Backend**: Redis 7 using an atomic Lua script executing a sliding fixed-window algorithm.
- **Cryptographic Key Separation**:
  - Raw identifiers (IPs, emails) are converted to HMAC-SHA256 digests (`rl:{action}:{keyed_digest}`) using a dedicated, purpose-specific `RATE_LIMIT_HMAC_KEY`.
  - `SESSION_SECRET_KEY`, `ENCRYPTION_MASTER_KEY`, and `RATE_LIMIT_HMAC_KEY` are strictly decoupled, pairwise distinct, and validated at startup (minimum 32-character entropy).
  - No raw secret values are ever logged or printed.
- **Fail-Closed Security Posture (HTTP 503 vs 429)**:
  - When Redis is unreachable or experiences network timeouts during rate limit evaluation, the system fails closed with `HTTP 503 Service Unavailable` (`ServiceUnavailableError`) with a `Retry-After: 10` header.
  - HTTP `429 Too Many Requests` (`RateLimitExceededError`) is strictly reserved for requests that legitimately exceed the configured rate limits.
- **Standard HTTP Headers**: Rate-limit rejections return HTTP `429` with a standard `Retry-After: <seconds>` header.

---

## 6.1 CORS Configuration

- **Middleware**: FastAPI `CORSMiddleware`.
- **Allowed Origins**: Explicitly sourced from `settings.ALLOWED_ORIGINS` (`["http://localhost:3000", "http://127.0.0.1:3000"]` in development, configurable via comma-separated string in production).
- **Credentials Aware**: `allow_credentials=True`. Wildcard origins (`*`) with credentials are explicitly prohibited and rejected at startup validation.
- **Allowed Methods**: `["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]`.
- **Allowed Headers**: Explicitly configured as `["Content-Type", "Authorization", "X-CSRF-Token", "X-Requested-With", "Accept", "Origin"]`.
- **Exposed Headers**: `["Content-Type", "Retry-After"]`.

---

## 6.2 Safe Key Rotation Implications

1. **`SESSION_SECRET_KEY`**: Rotating this key invalidates active session signatures/cookies. Users must re-authenticate upon rotation.
2. **`RATE_LIMIT_HMAC_KEY`**: Rotating this key causes client identifiers to hash to new Redis rate-limiting keys, resetting existing sliding-window counters without affecting active sessions or database records (safe, zero-downtime rotation).
3. **`ENCRYPTION_MASTER_KEY`**: Rotating this key requires re-encrypting existing database ciphertext records (e.g. TOTP MFA secrets). The platform's versioned envelope format (`v1:{nonce}:{tag_ciphertext}`) supports multi-version key decryption during phased migration.

---

## 7. Email Verification & Password Recovery

### Email Delivery Abstraction & Current Operational Scope
- Defined by `EmailSender` interface in `app.services.email`.
- `MemoryEmailSender`: Captures sent messages in memory for automated integration testing and local development without external SMTP dependencies. Currently active in dev/test configuration.
- `SmtpEmailSender`: Production-ready delivery using TLS encryption and configurable SMTP credentials. Real-world SMTP delivery requires external email service credentials and has not yet been executed against a live provider.
- **CI/CD Execution Status**: All tests and lint checks documented here were executed locally on the developer machine. GitHub Actions remote pipeline verification will execute upon repository push.

### Atomic Action Tokens
- Short-lived, cryptographically secure single-use tokens.
- **Email Verification**: 24-hour expiration (`86400` seconds).
- **Password Reset**: 30-minute expiration (`1800` seconds).
- **Atomic Single-Use Consumption**: Uses SQL `UPDATE ... SET consumed_at = NOW() WHERE ... AND consumed_at IS NULL RETURNING ...` to ensure that under concurrent requests, exactly one request can consume the token.

---

## 8. Multi-Factor Authentication (TOTP MFA) & Sensitive Reauthentication

- **Standard**: RFC 6238 TOTP (6-digit, 30-second time-step) implemented via `pyotp`.
- **Secret Encryption**: Stored Base32 secrets are encrypted at rest using AES-256-GCM authenticated encryption. Nonces (12 bytes) and key versions are stored with the ciphertext (`v1:{nonce}:{ciphertext_tag}`).
- **Atomic Compare-and-Swap Replay Protection**:
  - The `mfa_credentials.last_accepted_step` column records the last consumed time-step counter.
  - Verification uses an atomic PostgreSQL conditional update: `UPDATE mfa_credentials SET last_accepted_step = :new_step, updated_at = :now WHERE user_id = :user_id AND (last_accepted_step IS NULL OR last_accepted_step < :new_step) RETURNING id`.
  - Concurrent requests with the same step code execute under database row locks; only the first update succeeds while the second fails atomically.
- **Mandatory Reauthentication on Sensitive Operations**:
  - `POST /api/v1/auth/mfa/totp/setup`: Requires current password verification; rejected if MFA is already active.
  - `POST /api/v1/auth/change-password`: When MFA is enabled, requires reauthentication with a valid second factor (TOTP code or recovery code) alongside the current password.
  - `POST /api/v1/auth/mfa/totp/disable`: Requires current password AND valid second factor (TOTP code or recovery code).
  - `POST /api/v1/auth/mfa/recovery-codes/regenerate`: Requires current password AND valid second factor (TOTP code or recovery code).
- **Backup Recovery Codes**:
  - 10 single-use codes formatted as `XXXX-XXXX-XXXX`.
  - Normalized (whitespace and dashes stripped, uppercase) and hashed with SHA-256 before storage in `mfa_recovery_codes`.
  - Single-use consumption is enforced atomically via PostgreSQL `UPDATE ... WHERE consumed_at IS NULL RETURNING ...`.
- **MFA Login Challenge**:
  - Two-stage authentication: primary credentials produce an `MfaLoginChallenge` token expiring in 300 seconds.
  - Challenge verification accepts either a TOTP authenticator code or an unused recovery code.
  - Automatically consumes challenge after 5 failed attempts or successful verification.

---

## 9. Multi-Tenant Context Resolution & Initial Selection Policy

- **Initial Tenant Selection on Login**:
  - **Single Membership**: When a user belongs to exactly one organization, `active_organization_id` is automatically set in their session.
  - **Multiple Memberships**: When a user belongs to more than one organization, the system does not infer or guess an organization based on database ordering; `active_organization_id` is left as `None`, requiring an explicit tenant selection via `POST /api/v1/auth/select-tenant`.
  - **Zero Memberships**: If a user belongs to no organizations, `active_organization_id` is `None`.
- **Authoritative Membership Validation**: The `get_current_tenant` FastAPI dependency authoritatively queries `organization_memberships` in PostgreSQL on every request.
- **Revocation Safety**: If a user's membership is deleted or disabled in PostgreSQL, subsequent requests with that active tenant context immediately fail with `403 Forbidden`, regardless of existing browser sessions.
- **Tenant Switching**: Authenticated users can switch active organizations via `POST /api/v1/auth/select-tenant`, which verifies membership in PostgreSQL before updating the session record.

---

## 10. Phase 4 vs. Phase 5 Scope Boundary

```
+-------------------------------------------------------------------+
|                           PHASE 4 (Current)                      |
| Identity Authentication, Passwords, Sessions, CSRF, MFA, Tenancy  |
+-------------------------------------------------------------------+
                                  │
                                  ▼
+-------------------------------------------------------------------+
|                           PHASE 5 (Future)                       |
| RBAC, Roles (Owner/Admin/Member), Permission Matrices, Audit Logs  |
+-------------------------------------------------------------------+
```

- **Phase 4** strictly guarantees **Who you are** (Authentication) and **Which tenant context you have access to** (Membership verification).
- **Phase 5** will govern **What permissions you possess within that tenant** (Role-Based Access Control, granular permission checks, administrative matrices). No Phase 5 role logic is present in Phase 4.

