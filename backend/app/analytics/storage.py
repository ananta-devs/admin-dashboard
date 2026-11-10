"""Private organization-isolated analytics artifact and snapshot file storage."""

from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

from app.analytics.exceptions import (
    AnalyticsError,
    DatasetTooLargeError,
    StorageQuotaUnavailableError,
)
from app.core.config import settings


class AnalyticsStorageService:
    """Manages private file artifacts for analytical inputs and outputs with generation fencing."""

    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = Path(base_dir or settings.ANALYTICS_STORAGE_DIR).resolve()
        self.snapshots_dir = self.base_dir / "snapshots"
        self.results_dir = self.base_dir / "results"
        self.quarantine_dir = self.base_dir / "quarantine"
        self.last_reservation_id: uuid.UUID | None = None
        self._local_lock = threading.Lock()
        self._in_memory_reservations: dict[uuid.UUID, int] = defaultdict(int)
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_safe_path(
        self, subfolder: str, organization_id: uuid.UUID, job_id: uuid.UUID, filename: str
    ) -> Path:
        """Resolve path strictly preventing directory traversal and symlink escapes."""
        safe_filename = Path(filename).name
        target_dir = (self.base_dir / subfolder / str(organization_id) / str(job_id)).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        target_path = (target_dir / safe_filename).resolve()
        base_str = str(self.base_dir)

        # Strict containment check
        if not str(target_path).startswith(base_str):
            raise AnalyticsError(
                "Path traversal attempt detected in analytics storage.", code="SECURITY_ERROR"
            )

        # Reject external symlinks if file exists
        if target_path.is_symlink():
            resolved_link = target_path.resolve()
            if not str(resolved_link).startswith(base_str):
                raise AnalyticsError(
                    "Symlink escape attempt detected in analytics storage.", code="SECURITY_ERROR"
                )

        return target_path

    def get_snapshot_path(
        self,
        organization_id: uuid.UUID,
        job_id: uuid.UUID,
        claim_generation: int = 1,
        snapshot_id: uuid.UUID | str | None = None,
    ) -> Path:
        """Generate attempt-fenced immutable snapshot path."""
        s_id = str(snapshot_id or "snap")
        filename = f"gen_{claim_generation}_{s_id}.parquet"
        return self._resolve_safe_path("snapshots", organization_id, job_id, filename)

    def get_result_artifact_path(
        self,
        organization_id: uuid.UUID,
        job_id: uuid.UUID,
        result_id: uuid.UUID,
        claim_generation: int = 1,
        ext: str = "parquet",
    ) -> Path:
        """Generate attempt-fenced immutable result artifact path."""
        filename = f"gen_{claim_generation}_{result_id}.{ext}"
        return self._resolve_safe_path("results", organization_id, job_id, filename)

    _local_lock = threading.Lock()
    _in_memory_reservations: dict[uuid.UUID, int] = defaultdict(int)

    def atomic_promote(self, tmp_path: Path, final_path: Path) -> None:
        """Flush, synchronize, and atomically promote temporary artifact to immutable path."""
        if not tmp_path.is_file():
            raise AnalyticsError(
                f"Temporary artifact {tmp_path} does not exist for promotion.",
                code="STORAGE_ERROR",
            )

        try:
            with open(tmp_path, "a+b") as f:
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            pass

        final_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_path, final_path)

        # Parent-directory synchronization where applicable and supported (POSIX / Linux)
        try:
            parent_fd = os.open(str(final_path.parent), os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        except (OSError, AttributeError):
            # os.O_RDONLY on directory or directory fsync is unsupported on Windows NTFS
            pass

    def _reserve_quota_postgres(
        self,
        organization_id: uuid.UUID,
        reservation_bytes: int,
        limit: int,
        worker_id: str | None = None,
        job_id: uuid.UUID | None = None,
        ttl_seconds: int = 600,
    ) -> tuple[uuid.UUID, int]:
        """Reserve quota atomically in PostgreSQL serialized per organization."""
        res_id = uuid.uuid4()
        disk_used = self.get_organization_storage_bytes(organization_id)
        dsn = settings.DATABASE_URL.replace("+asyncpg", "")
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                conn.autocommit = False
                # Advisory lock serialized per organization UUID string
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(organization_id),))

                # Clean up expired reservations for this organization
                cur.execute(
                    """
                    UPDATE analytics_quota_reservations
                    SET status = 'expired'
                    WHERE organization_id = %s
                      AND status = 'active'
                      AND expires_at < NOW()
                    """,
                    (str(organization_id),),
                )

                # Compute total active unexpired reservations
                cur.execute(
                    """
                    SELECT COALESCE(SUM(reserved_bytes), 0)
                    FROM analytics_quota_reservations
                    WHERE organization_id = %s
                      AND status = 'active'
                      AND expires_at >= NOW()
                    """,
                    (str(organization_id),),
                )
                row = cur.fetchone()
                active_res = int(row[0]) if row and row[0] is not None else 0

                if (disk_used + active_res + reservation_bytes) > limit:
                    conn.rollback()
                    raise DatasetTooLargeError(
                        f"Organization storage quota ({limit} bytes) exceeded: disk usage {disk_used} bytes, active reservations {active_res} bytes, requested {reservation_bytes} bytes."
                    )

                expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
                cur.execute(
                    """
                    INSERT INTO analytics_quota_reservations (
                        id, organization_id, worker_id, job_id, reserved_bytes, status, expires_at, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, 'active', %s, NOW()
                    )
                    """,
                    (
                        str(res_id),
                        str(organization_id),
                        worker_id,
                        str(job_id) if job_id else None,
                        reservation_bytes,
                        expires_at,
                    ),
                )
                conn.commit()
        return res_id, active_res + reservation_bytes

    def _release_quota_postgres(self, reservation_id: uuid.UUID) -> bool:
        """Release PostgreSQL reservation idempotently without touching any other worker's quota."""
        dsn = settings.DATABASE_URL.replace("+asyncpg", "")
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE analytics_quota_reservations
                    SET status = 'released', released_at = NOW()
                    WHERE id = %s AND status = 'active'
                    """,
                    (str(reservation_id),),
                )
                released = cur.rowcount > 0
            conn.commit()
            return released

    def reconcile_expired_reservations(self, organization_id: uuid.UUID | None = None) -> int:
        """Mark expired active reservations as expired."""
        dsn = settings.DATABASE_URL.replace("+asyncpg", "")
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                if organization_id:
                    cur.execute(
                        """
                        UPDATE analytics_quota_reservations
                        SET status = 'expired'
                        WHERE status = 'active'
                          AND expires_at < NOW()
                          AND organization_id = %s
                        """,
                        (str(organization_id),),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE analytics_quota_reservations
                        SET status = 'expired'
                        WHERE status = 'active'
                          AND expires_at < NOW()
                        """
                    )
                count = cur.rowcount
            conn.commit()
            return count

    def renew_quota_reservation(
        self,
        reservation_id: uuid.UUID,
        extend_seconds: int = 300,
        max_lifetime_seconds: int = 3600,
    ) -> bool:
        """Safely and boundingly renew an active quota reservation during ongoing legitimate operations.

        Requirements:
        - Only active reservations can be extended.
        - Extension is strictly bounded: total elapsed lifetime cannot exceed max_lifetime_seconds.
        - Fails closed safely if max lifetime is reached or reservation was cancelled/expired.
        """
        dsn = settings.DATABASE_URL.replace("+asyncpg", "")
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                conn.autocommit = False
                cur.execute(
                    """
                    SELECT created_at, status 
                    FROM analytics_quota_reservations 
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (str(reservation_id),),
                )
                row = cur.fetchone()
                if not row or row[1] != "active":
                    conn.rollback()
                    return False

                created_at: datetime = row[0]
                now = datetime.now(UTC)
                total_duration = (now - created_at).total_seconds() + extend_seconds
                if total_duration > max_lifetime_seconds:
                    # Bounded limit exceeded: prevent indefinite renewal
                    conn.rollback()
                    return False

                new_expires_at = now + timedelta(seconds=extend_seconds)
                cur.execute(
                    """
                    UPDATE analytics_quota_reservations
                    SET expires_at = %s
                    WHERE id = %s AND status = 'active'
                    """,
                    (new_expires_at, str(reservation_id)),
                )
                conn.commit()
                return cur.rowcount > 0

    @contextmanager
    def reserve_quota(
        self,
        organization_id: uuid.UUID,
        reservation_bytes: int,
        worker_id: str | None = None,
        job_id: uuid.UUID | None = None,
        ttl_seconds: int = 600,
        force_fallback: bool = False,
        force_in_memory: bool = False,
    ) -> Generator[int, None, None]:
        """Atomically reserve quota before artifact generation using PostgreSQL as the single authoritative ledger."""
        if reservation_bytes <= 0:
            yield 0
            return

        limit = settings.ANALYTICS_MAX_ORG_STORAGE_BYTES

        # PostgreSQL is the sole distributed authority for every reservation.
        # Fail closed on database unavailability with a retryable error.
        try:
            res_id, total_res = self._reserve_quota_postgres(
                organization_id=organization_id,
                reservation_bytes=reservation_bytes,
                limit=limit,
                worker_id=worker_id,
                job_id=job_id,
                ttl_seconds=ttl_seconds,
            )
        except DatasetTooLargeError:
            raise
        except Exception as e:
            raise StorageQuotaUnavailableError(
                f"Authoritative PostgreSQL storage quota ledger unavailable: {e}"
            ) from e

        self.last_reservation_id = res_id
        try:
            yield total_res
        finally:
            try:
                self._release_quota_postgres(res_id)
            except Exception:
                pass

    @staticmethod
    def compute_sha256(file_path: Path) -> str:
        """Compute SHA-256 hash of a file on disk."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def delete_artifact(self, file_path: str | Path) -> bool:
        """Safely delete an artifact file if within base directory."""
        path = Path(file_path).resolve()
        if str(path).startswith(str(self.base_dir)) and path.is_file():
            try:
                os.remove(path)
                return True
            except OSError:
                return False
        return False

    def quarantine_artifact(self, file_path: str | Path) -> Path:
        """Move an unreferenced or failed artifact to the quarantine directory."""
        path = Path(file_path).resolve()
        if not path.is_file():
            return path
        try:
            rel = path.relative_to(self.base_dir)
            target = (self.quarantine_dir / rel).resolve()
        except ValueError:
            target = (self.quarantine_dir / path.name).resolve()

        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(path, target)
        except OSError:
            pass
        return target

    def get_organization_storage_bytes(self, organization_id: uuid.UUID) -> int:
        """Calculate total storage occupied by an organization's snapshots and result artifacts."""
        total = 0
        org_str = str(organization_id)
        for subfolder in [self.snapshots_dir, self.results_dir]:
            org_dir = subfolder / org_str
            if org_dir.is_dir():
                for p in org_dir.rglob("*"):
                    if p.is_file():
                        total += p.stat().st_size
        return total

    def check_storage_quota(self, organization_id: uuid.UUID, additional_bytes: int = 0) -> None:
        """Verify that organization does not exceed total storage quota."""
        from app.analytics.exceptions import DatasetTooLargeError

        current = self.get_organization_storage_bytes(organization_id)
        if (current + additional_bytes) > settings.ANALYTICS_MAX_ORG_STORAGE_BYTES:
            raise DatasetTooLargeError(
                f"Organization storage quota ({settings.ANALYTICS_MAX_ORG_STORAGE_BYTES} bytes) exceeded: current usage {current} bytes."
            )

    def prune_orphan_artifacts(
        self,
        active_job_ids: set[uuid.UUID] | None = None,
        referenced_paths: set[str] | None = None,
        grace_period_seconds: int = 3600,
    ) -> int:
        """Reclaim unreferenced and orphan artifact files while strictly protecting active jobs."""
        active_ids = {str(jid) for jid in (active_job_ids or set())}
        ref_paths = {str(Path(p).resolve()) for p in (referenced_paths or set()) if p}
        cutoff_time = time.time() - grace_period_seconds
        pruned_count = 0

        for subfolder in [self.snapshots_dir, self.results_dir]:
            if not subfolder.is_dir():
                continue
            for p in subfolder.rglob("*"):
                if not p.is_file():
                    continue

                # Temporary files older than grace period
                if p.name.endswith(".tmp"):
                    if p.stat().st_mtime < cutoff_time:
                        try:
                            p.unlink(missing_ok=True)
                            pruned_count += 1
                        except OSError:
                            pass
                    continue

                # Check if file belongs to an active job directory
                # Path layout: subfolder / org_id / job_id / filename
                job_id_in_path = None
                try:
                    # Parent directory is job_id
                    job_id_in_path = p.parent.name
                except Exception:
                    pass

                if job_id_in_path and job_id_in_path in active_ids:
                    continue  # Active job in progress -> preserve!

                # Check if referenced in database
                resolved_p = str(p.resolve())
                if resolved_p in ref_paths:
                    continue  # Committed result or snapshot referenced in DB -> preserve!

                # Unreferenced file older than grace period
                if p.stat().st_mtime < cutoff_time:
                    try:
                        p.unlink(missing_ok=True)
                        pruned_count += 1
                    except OSError:
                        pass

        return pruned_count

    def prune_expired_artifacts(self, retention_days: int = 30) -> int:
        """Prune artifacts older than global retention period."""
        cutoff_time = time.time() - (retention_days * 86400)
        pruned_count = 0
        for subfolder in [self.snapshots_dir, self.results_dir]:
            if subfolder.is_dir():
                for p in subfolder.rglob("*"):
                    if p.is_file() and p.stat().st_mtime < cutoff_time:
                        try:
                            p.unlink()
                            pruned_count += 1
                        except OSError:
                            pass
        return pruned_count
