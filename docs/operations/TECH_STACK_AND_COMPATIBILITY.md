# TECHNOLOGY STACK & DEPENDENCY COMPATIBILITY MATRIX
## Enterprise Admin & Data Intelligence Platform

**Document Version:** 2.0.0  
**Environment Baseline:** Node.js 26.13.0 / npm 11.20.0 / Python 3.14.7 / Docker Desktop 29.9.2

---

## 1. Verified Dependency Compatibility Matrix

| Technology | Category | Pinned Version | Compatibility Notes |
| :--- | :--- | :--- | :--- |
| **Next.js** | Frontend Framework | `^16.4.3` | App Router, React Server Components, Server Actions |
| **React / React-DOM** | UI Foundation | `^19.3.1` | Concurrent features, React Compiler ready |
| **TypeScript** | Language / Types | `^7.0.2` | Strict mode enabled (`noImplicitAny`, `strictNullChecks`) |
| **Tailwind CSS** | Styling | `^4.3.3` | Modern engine, zero CSS runtime overhead |
| **shadcn/ui & Radix** | UI Component Primitives | Latest stable | Unstyled accessible base, styled via Tailwind |
| **TanStack Table** | Data Table Logic | `^9.3.1` | Headless data management, zero external DOM assumptions |
| **TanStack Virtual** | Virtualization Engine | `^3.14.24` | Row and column DOM windowing for 50k+ records |
| **TanStack Query** | Server State Cache | `^5.105.0` | Client-side query caching, deduplication & optimistic updates |
| **Apache ECharts** | Visualizations | `^6.1.0` | High-performance Canvas/SVG charting engine |
| **FastAPI** | Backend Web Framework | `~=0.141.1` | ASGI asynchronous Python API core (Python 3.14 verified) |
| **Uvicorn** | ASGI Web Server | `~=0.53.0` | High-throughput asynchronous server |
| **Pydantic** | Validation & Schemas | `~=2.13.5` | Strict type coercion and runtime serialization |
| **SQLAlchemy** | Relational ORM | `~=2.1.6` | Async engine support (`asyncpg`) |
| **Alembic** | Schema Migrations | `~=1.20.1` | Declarative migration scripts |
| **psycopg[binary]** | Sync Postgres Driver | `~=3.3.6` | Native client for migrations and sync scripts |
| **asyncpg** | Async Postgres Driver | `~=0.32.0` | Fast asynchronous database driver for FastAPI |
| **Redis (Python Client)**| Cache & State | `~=6.4.0` | Pinned to comply with Kombu 5.6 upper bound requirements |
| **Celery** | Task Queue Worker | `~=5.6.3` | Redis broker support with Kombu 5.6 |
| **Argon2-cffi** | Password Hashing | `~=25.1.0` | OWASP-recommended memory-hard hashing |
| **Cryptography** | Symmetric Encryption | `~=50.0.1` | AES-256-GCM primitives |
| **Polars** | Fast Analytical Engine | `~=2.1.0` | Rust-backed analytical DataFrame engine (Python 3.14 wheel verified) |
| **Pandas** | Tabular Transformations | `~=3.0.5` | Comprehensive analytical manipulation |
| **NumPy** | Numerical Computing | `~=2.5.3` | Matrix operations |
| **SciPy** | Statistical Inference | `~=1.18.1` | Descriptive statistics and correlations |
| **scikit-learn** | Machine Learning | `~=1.9.1` | Clustering and trend estimation |
| **openpyxl** | Excel Workbook I/O | `~=3.1.5` | Native `.xlsx` parsing and serialization |

---

## 2. Pinned Python Specification (`backend/requirements.txt`)

```text
fastapi>=0.141.0,<0.142.0
uvicorn[standard]>=0.53.0,<0.54.0
pydantic>=2.13.0,<2.14.0
pydantic-settings>=2.8.0,<2.9.0
sqlalchemy[asyncio]>=2.0.36,<2.2.0
greenlet>=3.1.0,<4.0.0
alembic>=1.14.0,<1.21.0
asyncpg>=0.30.0,<0.33.0
psycopg[binary]>=3.2.0,<3.4.0
redis>=5.2.0,<6.5.0
argon2-cffi>=23.1.0,<26.0.0
cryptography>=44.0.0,<51.0.0
pyotp>=2.9.0,<3.0.0
email-validator>=2.0.0,<3.0.0
httpx>=0.28.0,<0.29.0
pytest>=8.3.0,<10.0.0
pytest-asyncio>=0.25.0,<1.5.0
pytest-cov>=6.0.0,<8.0.0
jinja2==3.1.5
defusedxml==0.7.1
pypdf==5.3.0
playwright==1.50.0
```
scikit-learn>=1.6.0,<2.0.0
matplotlib>=3.10.0,<3.12.0
openpyxl>=3.1.2,<3.2.0
python-multipart>=0.0.20,<0.0.34
aiofiles>=24.1.0,<26.0.0
httpx>=0.28.0,<0.29.0
pytest>=8.3.0,<10.0.0
pytest-asyncio>=0.25.0,<1.5.0
pytest-cov>=6.0.0,<8.0.0
```

