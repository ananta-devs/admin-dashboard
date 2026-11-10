# DOCKER INFRASTRUCTURE & LOCAL DEVELOPMENT SETUP
## Enterprise Admin & Data Intelligence Platform

**Document Version:** 2.0.0  
**Resource Budget:** 16 GB Total RAM Host (~7.6 GB Docker Ceiling)  
**Total Allocated Memory:** 2.0 GB (PostgreSQL 1.5GB + Redis 512MB)

---

## 1. Clean `docker-compose.yml` Specification

The configuration eliminates the obsolete `version` tag, binds exclusively to localhost, specifies explicit resource limits, and sets strict health checks:

```yaml
services:
  postgres:
    image: postgres:16-alpine
    container_name: vibe_postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB:?POSTGRES_DB required}
      POSTGRES_USER: ${POSTGRES_USER:?POSTGRES_USER required}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD required}
    ports:
      - "127.0.0.1:5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./scripts/init-db.sql:/docker-entrypoint-initdb.d/init.sql:ro
      - ./backups/wal_archive:/wal_archive
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 5s
      timeout: 5s
      retries: 5
    deploy:
      resources:
        limits:
          memory: 1536M
        reservations:
          memory: 512M

  redis:
    image: redis:7-alpine
    container_name: vibe_redis
    restart: unless-stopped
    command: >
      redis-server
      --requirepass ${REDIS_PASSWORD:?REDIS_PASSWORD required}
      --appendonly yes
      --maxmemory 384mb
      --maxmemory-policy volatile-lru
    ports:
      - "127.0.0.1:6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "$$REDIS_PASSWORD", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5
    deploy:
      resources:
        limits:
          memory: 512M
        reservations:
          memory: 128M

volumes:
  postgres_data:
    name: vibe_postgres_data
  redis_data:
    name: vibe_redis_data
```

---

## 2. Environment Configuration Template (`.env.example`)

```bash
# ==============================================================================
# ENTERPRISE ADMIN PLATFORM - LOCAL ENVIRONMENT TEMPLATE
# ==============================================================================

# Database Settings
POSTGRES_DB=vibe_enterprise
POSTGRES_USER=vibe_admin
POSTGRES_PASSWORD=replace_with_strong_password_min_32_chars
DATABASE_URL=postgresql+asyncpg://vibe_admin:replace_with_strong_password_min_32_chars@127.0.0.1:5432/vibe_enterprise

# Redis Broker & Cache Settings
REDIS_PASSWORD=replace_with_strong_redis_password
REDIS_URL=redis://:replace_with_strong_redis_password@127.0.0.1:6379/0
REDIS_CACHE_URL=redis://:replace_with_strong_redis_password@127.0.0.1:6379/1

# Security & Master Encryption Keys (Generate via: openssl rand -hex 32)
SESSION_SECRET_KEY=replace_with_64_character_hex_session_secret
ENCRYPTION_MASTER_KEY=replace_with_64_character_hex_encryption_key

# Service URLs
NEXT_PUBLIC_APP_URL=http://localhost:3000
BACKEND_INTERNAL_URL=http://127.0.0.1:8000
NEXT_PUBLIC_API_URL=http://localhost:3000/api/v1

# Storage
FILE_STORAGE_PATH=./uploads
```

---

## 3. Operations Runbook: Backup & Restore Commands

### 3.1 Trigger Manual Logical Backup (Development)
```bash
docker exec -t vibe_postgres pg_dump -U vibe_admin -Fc vibe_enterprise > backups/manual_backup_$(date +%Y%m%d_%H%M%S).dump
```

### 3.2 Restore from Backup File
```bash
# 1. Terminate existing connections
docker exec -i vibe_postgres psql -U vibe_admin -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'vibe_enterprise' AND pid <> pg_backend_pid();"

# 2. Re-create and restore database
docker exec -i vibe_postgres psql -U vibe_admin -d postgres -c "DROP DATABASE IF EXISTS vibe_enterprise; CREATE DATABASE vibe_enterprise;"
docker exec -i vibe_postgres pg_restore -U vibe_admin -d vibe_enterprise --clean --if-exists < backups/target_backup.dump
```

