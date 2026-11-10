# Vibe Enterprise Documentation

Welcome to the **Vibe Enterprise — Admin & Data Intelligence Platform** documentation hub.

---

## 🧭 Directory Map

```
docs/
├── README.md               # Master documentation index and guide (this file)
├── CHANGELOG.md            # Platform engineering changelog & release log
│
├── architecture/           # System design, models, and technical specifications
│   ├── adr/                # Architecture Decision Records (001 - 003)
│   ├── ARCHITECTURE.md     # Platform high-level architecture & subsystem map
│   ├── backend-architecture.md  # FastAPI modular monolithic architecture & service layers
│   ├── DATABASE_DESIGN.md  # PostgreSQL relational models, indexing & tenancy
│   ├── design-system.md    # UI component library, tokens, and Tailwind theme
│   ├── analytics-engine.md # DuckDB execution pipeline, sandboxing & parquet storage
│   ├── spreadsheet-engine.md # Spreadsheet calculation meshes & formula engine
│   └── SPREADSHEET_ENGINE_DESIGN.md # Dependency graphs & reactive cell calculations
│
├── api/                    # REST API specifications and contracts
│   └── API_DOCUMENTATION.md # OpenAPI schemas, endpoint contracts & error payloads
│
├── security/               # Security policies, authentication & access control
│   ├── authentication-security.md # Session cookies, key rotation & security headers
│   ├── rbac-authorization.md # RBAC roles, permission hierarchy & scope matrices
│   └── SECURITY_PLAN.md    # Platform security posture, CSP policy & compliance
│
├── operations/             # DevOps, infrastructure, testing & deployment
│   ├── DEPLOYMENT_GUIDE.md # Production deployment & cloud runtime setup
│   ├── DOCKER_SETUP.md     # Docker Compose orchestration (Postgres, Redis, Backend, Frontend)
│   ├── BACKUP_AND_RECOVERY.md # DR plan, host-loss recovery & snapshot replication
│   ├── TESTING_STRATEGY.md # Pytest, Vitest & Playwright test coverage strategy
│   ├── TECH_STACK_AND_COMPATIBILITY.md # Software dependencies & matrix
│   └── DECISIONS_AND_RISKS.md # Engineering decisions, trade-offs & mitigations
│
├── planning/               # Roadmaps, requirements & assessments
│   ├── PROJECT_OVERVIEW.md # Executive vision, goals & scope
│   ├── IMPLEMENTATION_PLAN.md # Implementation roadmap & milestones
│   ├── ASSESSMENT_AND_PLAN.md # Baseline audit & system capability plan
│   └── REVISED_ARCHITECTURE_AND_PLAN.md # Architectural revisions & scope adjustments
│
├── reports/                # Operational incident & maintenance reports
│   ├── DATABASE_WIPE_REPORT.md # Database wipe audit & schema validation report
│   ├── LAYOUT_ALIGNMENT_STANDARDIZATION_REPORT.md # UI alignment & viewport standardization
│   └── SESSION_REDIRECTION_RESOLUTION.md # Stale cookie loop resolution & fix
│
└── phases/                 # Historical engineering phases & verification gates
    ├── phase-1/            # Foundation, database schemas & base models (2 reports)
    ├── phase-2/            # Core authentication, session handling & shell UI (3 reports)
    ├── phase-3/            # Transaction isolation, locks & concurrency safety (5 reports)
    ├── phase-4/            # Security hardening, rate-limiting & CSRF mitigation (3 reports)
    ├── phase-5/            # User administration, RBAC & audit logging (4 reports)
    ├── phase-6/            # Telemetry diagnostics & system health (2 reports)
    ├── phase-7/            # File storage quotas & multipart uploads (5 reports)
    ├── phase-8/            # Enterprise calculation mesh & spreadsheet engine (19 reports)
    ├── phase-9/            # DuckDB analytics engine & dashboard builder (38 reports)
    ├── phase-10/           # Disaster recovery, host-loss & high availability (5 reports)
    ├── phase-11/           # Gates 1-4 security & performance optimization audits (27 reports)
    └── phase-12/           # Manual QA, Chrome CDP validation & Stages 12.0 - 12.3 (39 reports)
```

---

## 📚 Section Overview

### 1. [Architecture](file:///D:/vibe/docs/architecture)
Detailed architectural specifications covering backend design, database schema, data intelligence engines, and design systems:
- [High-Level Architecture](file:///D:/vibe/docs/architecture/ARCHITECTURE.md)
- [Backend Architecture](file:///D:/vibe/docs/architecture/backend-architecture.md)
- [Database Design](file:///D:/vibe/docs/architecture/DATABASE_DESIGN.md)
- [Design System](file:///D:/vibe/docs/architecture/design-system.md)
- [Analytics Engine](file:///D:/vibe/docs/architecture/analytics-engine.md)
- [Spreadsheet Engine](file:///D:/vibe/docs/architecture/spreadsheet-engine.md)
- [Spreadsheet Engine Design](file:///D:/vibe/docs/architecture/SPREADSHEET_ENGINE_DESIGN.md)
- [Architecture Decision Records (ADRs)](file:///D:/vibe/docs/architecture/adr)

### 2. [API](file:///D:/vibe/docs/api)
Complete REST API reference including request/response models and authentication:
- [API Documentation](file:///D:/vibe/docs/api/API_DOCUMENTATION.md)

### 3. [Security](file:///D:/vibe/docs/security)
Security configurations, key management, authorization policies, and access controls:
- [Authentication & Security](file:///D:/vibe/docs/security/authentication-security.md)
- [RBAC Authorization](file:///D:/vibe/docs/security/rbac-authorization.md)
- [Security Plan](file:///D:/vibe/docs/security/SECURITY_PLAN.md)

### 4. [Operations](file:///D:/vibe/docs/operations)
Guides for container orchestration, deployment, backup/recovery, and quality assurance:
- [Deployment Guide](file:///D:/vibe/docs/operations/DEPLOYMENT_GUIDE.md)
- [Docker Setup](file:///D:/vibe/docs/operations/DOCKER_SETUP.md)
- [Backup & Recovery](file:///D:/vibe/docs/operations/BACKUP_AND_RECOVERY.md)
- [Testing Strategy](file:///D:/vibe/docs/operations/TESTING_STRATEGY.md)
- [Tech Stack & Compatibility](file:///D:/vibe/docs/operations/TECH_STACK_AND_COMPATIBILITY.md)
- [Decisions & Risks](file:///D:/vibe/docs/operations/DECISIONS_AND_RISKS.md)

### 5. [Planning](file:///D:/vibe/docs/planning)
Project overview, scope definitions, roadmap blueprints, and initial assessments:
- [Project Overview](file:///D:/vibe/docs/planning/PROJECT_OVERVIEW.md)
- [Implementation Plan](file:///D:/vibe/docs/planning/IMPLEMENTATION_PLAN.md)
- [Assessment & Plan](file:///D:/vibe/docs/planning/ASSESSMENT_AND_PLAN.md)
- [Revised Architecture & Plan](file:///D:/vibe/docs/planning/REVISED_ARCHITECTURE_AND_PLAN.md)

### 6. [Reports](file:///D:/vibe/docs/reports)
Operational maintenance, database management, and runtime resolution logs:
- [Database Wipe & Schema Audit](file:///D:/vibe/docs/reports/DATABASE_WIPE_REPORT.md)
- [Layout Alignment Standardization](file:///D:/vibe/docs/reports/LAYOUT_ALIGNMENT_STANDARDIZATION_REPORT.md)
- [Session Redirection Resolution](file:///D:/vibe/docs/reports/SESSION_REDIRECTION_RESOLUTION.md)

### 7. [Phases](file:///D:/vibe/docs/phases)
Historical progression through the 12 platform development phases:
- [Phase 1: Foundation & Data Layer](file:///D:/vibe/docs/phases/phase-1)
- [Phase 2: Authentication & Frontend Shell](file:///D:/vibe/docs/phases/phase-2)
- [Phase 3: Transactions & Concurrency](file:///D:/vibe/docs/phases/phase-3)
- [Phase 4: Security Hardening & Rate Limiting](file:///D:/vibe/docs/phases/phase-4)
- [Phase 5: User Administration & RBAC](file:///D:/vibe/docs/phases/phase-5)
- [Phase 6: System Telemetry & Health](file:///D:/vibe/docs/phases/phase-6)
- [Phase 7: File Storage & Quotas](file:///D:/vibe/docs/phases/phase-7)
- [Phase 8: Spreadsheet Engine](file:///D:/vibe/docs/phases/phase-8)
- [Phase 9: Analytics & Dashboards](file:///D:/vibe/docs/phases/phase-9)
- [Phase 10: Disaster Recovery & High Availability](file:///D:/vibe/docs/phases/phase-10)
- [Phase 11: Security & Performance Audits](file:///D:/vibe/docs/phases/phase-11)
- [Phase 12: Manual QA & Chrome CDP Verification](file:///D:/vibe/docs/phases/phase-12)
