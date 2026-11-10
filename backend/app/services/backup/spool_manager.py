"""Spool Capacity Management and Endurance Monitoring for Phase 10 Disaster Recovery.

Tracks spool disk metrics, calculates time-to-exhaustion across workloads,
and provides multi-tier watermark alarms (High: 80%, Critical: 90%, Emergency: 95%).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Spool Limits (Binary units)
DEFAULT_SPOOL_CAPACITY_BYTES = 50 * 1024 * 1024 * 1024  # 50 GiB = 53,687,091,200 bytes
HIGH_WATERMARK_RATIO = 0.80  # 40 GiB
CRITICAL_WATERMARK_RATIO = 0.90  # 45 GiB
EMERGENCY_WATERMARK_RATIO = 0.95  # 47.5 GiB

# Known Workload WAL Generation Rates (MiB / hour)
WAL_RATES_MIB_HR = {
    "low": 16.0,  # 1 segment / hr
    "normal": 160.0,  # 10 segments / hr
    "high": 512.0,  # 32 segments / hr
    "burst": 1600.0,  # 100 segments / hr
}


class SpoolManagerService:
    """Monitors and manages WAL spool storage capacity and backpressure."""

    def __init__(
        self, spool_dir: str | Path, capacity_bytes: int = DEFAULT_SPOOL_CAPACITY_BYTES
    ) -> None:
        self.spool_dir = Path(spool_dir).resolve()
        self.capacity_bytes = capacity_bytes

    def get_spool_metrics(self) -> dict[str, Any]:
        """Compute current spool utilization, segment counts, and watermark status."""
        total_used_bytes = 0
        segment_count = 0
        history_count = 0

        if self.spool_dir.exists():
            for p in self.spool_dir.iterdir():
                if p.is_file() and not p.name.startswith("."):
                    total_used_bytes += p.stat().st_size
                    if p.name.endswith(".history"):
                        history_count += 1
                    else:
                        segment_count += 1

        usage_ratio = total_used_bytes / self.capacity_bytes if self.capacity_bytes > 0 else 0.0

        if usage_ratio >= EMERGENCY_WATERMARK_RATIO:
            watermark_level = "EMERGENCY"
            action_required = "CRITICAL: Engine approach to pg_wal exhaustion. Pause analytical writes immediately."
        elif usage_ratio >= CRITICAL_WATERMARK_RATIO:
            watermark_level = "CRITICAL"
            action_required = "Pause asynchronous ETL ingestion; accelerate remote upload workers."
        elif usage_ratio >= HIGH_WATERMARK_RATIO:
            watermark_level = "HIGH"
            action_required = "Elevate upload worker concurrency."
        else:
            watermark_level = "NORMAL"
            action_required = "Normal operational buffering."

        remaining_usable_bytes = max(
            0, int(self.capacity_bytes * HIGH_WATERMARK_RATIO) - total_used_bytes
        )
        remaining_gross_bytes = max(0, self.capacity_bytes - total_used_bytes)

        # Calculate time to exhaustion across workloads (in hours)
        exhaustion_hours = {}
        for scenario, rate_mib in WAL_RATES_MIB_HR.items():
            rate_bytes_hr = rate_mib * 1024 * 1024
            usable_hrs = remaining_usable_bytes / rate_bytes_hr if rate_bytes_hr > 0 else 0.0
            gross_hrs = remaining_gross_bytes / rate_bytes_hr if rate_bytes_hr > 0 else 0.0
            exhaustion_hours[scenario] = {
                "hours_to_high_watermark": round(usable_hrs, 2),
                "hours_to_full": round(gross_hrs, 2),
                "days_to_full": round(gross_hrs / 24.0, 2),
            }

        return {
            "spool_dir": str(self.spool_dir),
            "capacity_bytes": self.capacity_bytes,
            "used_bytes": total_used_bytes,
            "usage_ratio": round(usage_ratio, 4),
            "usage_percentage": round(usage_ratio * 100, 2),
            "watermark_level": watermark_level,
            "action_required": action_required,
            "segment_count": segment_count,
            "history_count": history_count,
            "exhaustion_estimates": exhaustion_hours,
        }
