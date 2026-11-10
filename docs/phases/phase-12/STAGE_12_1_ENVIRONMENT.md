# Phase 12 Stage 12.1 — E2E Test Environment & Server Configuration
**Document:** `docs/phase-12/STAGE_12_1_ENVIRONMENT.md`  
**Phase:** 12 — Production Readiness, End-to-End Testing & Deployment Verification  
**Stage:** 12.1 — E2E Testing Infrastructure  
**Governing Roadmap:** `docs/IMPLEMENTATION_PLAN.md` v2.0.0  

---

## 1. Test Architecture Overview

The Phase 12 E2E testing environment is designed for multi-tier integration without using production secrets or altering existing developmental data.

```mermaid
flowchart LR
    subgraph Host / Test Runner
        Playwright["Playwright Test Runner (1.64.1)"]
    end

    subgraph Frontend Tier
        Playwright --> NextApp["Next.js Server (Port 3000)"]
    end

    subgraph Backend & DB Tier (Docker)
        NextApp -- "Reverse Proxy (/api/v1/*)" --> FastAPIServer["FastAPI Backend (Port 8000)"]
        FastAPIServer --> Postgres["PostgreSQL 16 (Port 5432)"]
        FastAPIServer --> Redis["Redis 7 (Port 6379)"]
    end
```

---

## 2. Server Management & Configuration

### 2.1 Next.js WebServer Management
Configured in `frontend/playwright.config.ts`:
- **Base URL:** `http://localhost:3000` (configurable via `PLAYWRIGHT_TEST_BASE_URL`).
- **WebServer Auto-Start:** Configured with `npm run start` and `reuseExistingServer: true`.
- **Environment Flags:**
  - `BACKEND_INTERNAL_URL="http://127.0.0.1:8000"` (enables Next.js API proxy to communicate with backend).
  - `PLAYWRIGHT_SKIP_WEBSERVER="1"` (allows running tests against an already running Next.js daemon without port collision).

### 2.2 Backend API Service
- Runs via container `vibe_backend_api` or uvicorn mapped to `127.0.0.1:8000`.
- Connects to `vibe_backend_net` Docker network with PostgreSQL and Redis.
- Zero development database resetting or data wiping is performed.

---

## 3. Dedicated Test Fixtures & Security

### 3.1 Synthetic User Generation
Implemented in `frontend/e2e/fixtures/auth.fixtures.ts`:
- Generates unpredictable, synthetic usernames and passwords (`e2e_<role>_<uid>@vibe.internal` and `P@ssword_<uid>_Secure123!`).
- Passwords satisfy the enterprise Argon2id password policy (uppercase, lowercase, numbers, special characters, min 12 characters).

### 3.2 Multi-Tenant Contexts
Provides pre-configured fixture objects:
- `orgAlpha`: `{ admin, member }`
- `orgBeta`: `{ admin, member }`
- Allows Stage 12.2 tests to verify that `orgAlpha.admin` cannot view or edit resources belonging to `orgBeta`.

### 3.3 Security Invariants
- **No Test Bypass Endpoints:** Authentication operates through standard production endpoints (`/api/v1/auth/register`, `/api/v1/auth/login`).
- **Real Session Cookies:** Sets authoritative `vibe_session` cookie via Playwright's `context.addCookies()`.
- **No Insecure Headers:** Relies on standard Next.js proxy rewrite headers.

