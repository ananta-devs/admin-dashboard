# BACKUP & DISASTER RECOVERY ARCHITECTURE
## Enterprise Admin & Data Intelligence Platform

**Document Version:** 2.0.0  
**Target Systems:** PostgreSQL 16 & Attached Storage Volumes  
**Empirical Targets:** Local Dev (RPO 24h, RTO 5m) | Production (RPO ≤ 15m, RTO ≤ 45m)

---

## 1. Local Development vs. Production Architectures

| Feature | Local Development (Docker) | Production Environment |
| :--- | :--- | :--- |
| **Primary Method** | Logical dumps via `pg_dump -Fc` | Physical base backups (`pg_basebackup`) + Continuous WAL Streaming |
| **Point-in-Time Recovery** | Not enabled | **Enabled (to the second)** |
| **Backup Trigger** | Manual Admin API / Container Cron (Daily) | Automated Daily Base Backup + 15-Minute WAL File Archiving |
| **Encryption** | AES-256-GCM via Python Cryptography | AES-256-GCM with separate offline Master Key |
| **Integrity Check** | SHA-256 hash verified after creation | SHA-256 + Automated Weekly Restoration Test Container |
| **Storage Target** | Mounted volume `/var/backups` | Encrypted Remote S3 Bucket (Object Lock / WORM enabled) |

---

## 2. Production Continuous WAL Archiving & PITR

### 2.1 PostgreSQL Engine Configuration (`postgresql.conf`)
```ini
wal_level = replica
archive_mode = on
archive_command = 'test ! -f /wal_archive/%f && cp %p /wal_archive/%f && gzip /wal_archive/%f'
archive_timeout = 900 # Forces WAL switch every 15 minutes to guarantee 15-min RPO
```

### 2.2 Disaster Recovery Procedure (Runbook)
In the event of total server loss:
1. **Provision Infrastructure:** Start fresh PostgreSQL 16 container with the target base volume.
2. **Restore Base Image:** Unpack the most recent physical base backup into `/var/lib/postgresql/data`.
3. **Configure Recovery Target:**
   Create `/var/lib/postgresql/data/recovery.signal` and define in `postgresql.conf`:
   ```ini
   restore_command = 'gunzip -c /wal_archive/%f.gz > %p'
   recovery_target_time = '2026-11-01 08:30:00 UTC'
   recovery_target_action = 'promote'
   ```
4. **Replay & Promotion:** Start PostgreSQL; it replays WAL records up to the exact target second, validates data consistency, and promotes the database to read-write mode.

---

## 3. Automated Integrity Verification Worker

A backup file is never assumed valid without verification:
1. **Checksum Generation:** When `pg_dump` finishes, Celery generates a SHA-256 hash and records it in `backup_records`.
2. **Restoration Testing Sandbox:**
   - On a weekly automated schedule, the worker spins up a temporary isolated PostgreSQL container.
   - It decrypts the backup, verifies the SHA-256 checksum, and executes `pg_restore`.
   - It performs automated sanity checks (`SELECT count(*) FROM users`, `SELECT count(*) FROM organizations`).
   - If restoration fails, an alert is dispatched immediately to administrators and logged to `audit_events`.

