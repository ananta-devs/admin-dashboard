# PROJECT OVERVIEW & SCOPE MANIFEST
## Enterprise Admin & Data Intelligence Platform

**Document Version:** 2.0.0  
**Project Objective:** Build a world-class, production-grade, enterprise administration and data intelligence platform combining the design quality of Linear/Notion, the spreadsheet capabilities of Google Sheets, the analytical horsepower of Power BI, and the operational reliability of modern enterprise SaaS.

---

## 1. System Vision & Deliverables

The platform is architected around four core pillars:
1. **Administrative Governance:** Multi-tenant organization isolation, granular RBAC/ABAC permissions, server-managed opaque session security, and tamper-resistant audit ledgers.
2. **Spreadsheet Workspace:** A high-performance, virtualized data grid (TanStack Table v9 + TanStack Virtual) capable of handling 50,000+ rows with typed columns, inline editing, matrix clipboard support, and a deterministic formula calculation engine (zero `eval()`).
3. **Data Intelligence & Analytics:** A dedicated Python analytics engine powered by Polars, Pandas, and SciPy for high-speed statistical summaries, trend detection, data cleaning, and Apache ECharts visualizations.
4. **Resilient Infrastructure:** Containerized PostgreSQL 16 and Redis 7 on Docker Compose, continuous WAL archiving for Point-in-Time Recovery (PITR), transactional outbox task dispatching, and automated disaster recovery verification.

---

## 2. Documentation Index & Cross-References

| Document Path | Description |
| :--- | :--- |
| `docs/architecture/ARCHITECTURE.md` | Comprehensive system topology, service boundaries, and hybrid storage design. |
| `docs/architecture/DATABASE_DESIGN.md` | Relational schemas, phased migrations (001-009), and Row-Level Security (RLS). |
| `docs/architecture/SPREADSHEET_ENGINE_DESIGN.md` | Formula grammar, AST parser, DAG recalculation engine, and concurrency control. |
| `docs/security/SECURITY_PLAN.md` | Opaque session engine, threat mitigation (STRIDE), and Next.js nonce-based CSP. |
| `docs/operations/BACKUP_AND_RECOVERY.md` | Continuous WAL archiving, disaster recovery runbooks, and restore sandboxing. |
| `docs/operations/DOCKER_SETUP.md` | Production-grade `docker-compose.yml`, `.env.example`, and memory budgets. |
| `docs/operations/TECH_STACK_AND_COMPATIBILITY.md` | Pinned dependency matrix across Node.js 26 and Python 3.14. |
| `docs/operations/TESTING_STRATEGY.md` | Measurable acceptance criteria, testing gates, and test datasets. |
| `docs/planning/IMPLEMENTATION_PLAN.md` | Sequential 13-phase roadmap with testing gates after every step. |
| `docs/operations/DECISIONS_AND_RISKS.md` | Trade-off matrices, risk registers, and outstanding technical decisions. |

