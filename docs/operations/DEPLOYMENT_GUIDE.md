# DEPLOYMENT & PRODUCTION RUNBOOK
## Enterprise Admin & Data Intelligence Platform

**Document Version:** 2.0.0  
**Target Environments:** Production Linux Server (Ubuntu 24.04 LTS / Debian 12 / Docker Compose)

---

## 1. Production Architecture Topology

In a production environment:
- **Next.js** runs inside a production Node.js container behind a reverse proxy (e.g., Caddy or Cloudflare) with automated SSL/TLS termination.
- **FastAPI** runs via Uvicorn with multiple worker processes (`uvicorn app.main:app --workers 4 --host 0.0.0.0 --port 8000`).
- **PostgreSQL 16** is tuned with connection pooling (`pgbouncer`), WAL archiving enabled for continuous Point-in-Time Recovery, and automated daily base backups uploaded to an encrypted S3 bucket.
- **Redis 7** is deployed with two isolated container instances:
  1. `redis-broker`: configured with `noeviction` and persistent append-only files (AOF) for Celery tasks.
  2. `redis-cache`: configured with `volatile-lru` and a strict memory ceiling for sessions and caching.
- **Celery Worker & Beat**: run as dedicated containerized daemon processes for asynchronous background jobs.

---

## 2. Production Environment Checklist

Before deploying:
1. Generate cryptographically random secrets for `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `SESSION_SECRET_KEY`, and `ENCRYPTION_MASTER_KEY` (`openssl rand -hex 32`).
2. Verify that database and Redis ports (`5432`, `6379`) are strictly bound to `127.0.0.1` or internal Docker networks and never exposed to the public internet.
3. Configure `wal_level = replica` and test the WAL archive script to verify automated transmission to private object storage.
4. Verify that Next.js CSP headers are set to production mode (removing `'unsafe-eval'`).
5. Ensure persistent storage volumes (`postgres_data`, `redis_data`, `uploads`) are attached to high-durability block storage with automated snapshots.

