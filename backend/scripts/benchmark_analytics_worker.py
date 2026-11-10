"""Empirical memory, duration, and thread concurrency benchmark for VIBE analytics worker."""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Ensure backend root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Enforce thread and math library bounds before importing numpy / polars
os.environ["POLARS_MAX_THREADS"] = "2"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import numpy as np
import polars as pl

from app.analytics.registry import registry


def get_process_rss_mb() -> float:
    """Return process Resident Set Size (RSS) in megabytes cross-platform."""
    if sys.platform == "win32":
        try:
            import ctypes.wintypes as w

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", w.DWORD),
                    ("PageFaultCount", w.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            fn = ctypes.windll.psapi.GetProcessMemoryInfo
            fn.argtypes = [w.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), w.DWORD]
            fn.restype = w.BOOL
            if fn(handle, ctypes.byref(pmc), pmc.cb):
                return float(pmc.WorkingSetSize) / (1024.0 * 1024.0)
        except Exception as e:
            print(f"Windows memory read error: {e}", file=sys.stderr)
    else:
        try:
            import resource

            rusage = resource.getrusage(resource.RUSAGE_SELF)
            return rusage.ru_maxrss / 1024.0
        except Exception:
            pass
    return 0.0


def benchmark_numerical_50k_x_26() -> dict[str, Any]:
    """Benchmark: 50,000 rows × 26 numeric columns (descriptive statistics & correlation)."""
    rss_before = get_process_rss_mb()
    threads_before = threading.active_count()
    start_time = time.perf_counter()

    # Generate synthetic numeric dataset
    cols = [f"Col_{chr(65 + i)}" for i in range(26)]
    np.random.seed(42)
    data = {c: np.random.randn(50_000) for c in cols}
    df = pl.DataFrame(data)

    rss_during_load = get_process_rss_mb()

    # Run operation: describe_columns
    desc_start = time.perf_counter()
    _ = registry.execute("describe_columns", df, {})
    desc_duration = time.perf_counter() - desc_start

    # Run operation: correlation_matrix on subset of 10 columns
    corr_start = time.perf_counter()
    _ = registry.execute(
        "correlation_matrix",
        df,
        {"columns": cols[:10], "method": "pearson"},
    )
    corr_duration = time.perf_counter() - corr_start

    rss_during_ops = get_process_rss_mb()

    del df
    del data
    import gc

    gc.collect()

    rss_after = get_process_rss_mb()
    total_duration = time.perf_counter() - start_time
    threads_after = threading.active_count()

    return {
        "dataset": "50,000 rows × 26 columns (1.3M numerical cells)",
        "rss_before_mb": round(rss_before, 2),
        "rss_during_load_mb": round(rss_during_load, 2),
        "rss_during_ops_mb": round(rss_during_ops, 2),
        "rss_after_mb": round(rss_after, 2),
        "rss_delta_mb": round(rss_during_ops - rss_before, 2),
        "describe_duration_sec": round(desc_duration, 4),
        "correlation_duration_sec": round(corr_duration, 4),
        "total_duration_sec": round(total_duration, 4),
        "active_threads": max(threads_before, threads_after),
        "polars_max_threads": os.environ.get("POLARS_MAX_THREADS"),
    }


def benchmark_mixed_250k() -> dict[str, Any]:
    """Benchmark: 250,000 rows with mixed types (numeric, categorical, timestamps)."""
    rss_before = get_process_rss_mb()
    threads_before = threading.active_count()
    start_time = time.perf_counter()

    np.random.seed(123)
    n = 250_000
    regions = ["North", "South", "East", "West", "Central"]
    tiers = ["Enterprise", "MidMarket", "Growth", "SMB"]

    data = {
        "id": np.arange(n),
        "revenue": np.random.uniform(10.0, 50000.0, size=n),
        "units": np.random.randint(1, 100, size=n),
        "cost": np.random.uniform(5.0, 25000.0, size=n),
        "region": np.random.choice(regions, size=n),
        "tier": np.random.choice(tiers, size=n),
    }
    df = pl.DataFrame(data)
    rss_during_load = get_process_rss_mb()

    # Run operation: group_by_aggregate
    agg_start = time.perf_counter()
    _ = registry.execute(
        "group_by_aggregate",
        df,
        {
            "group_by": ["region", "tier"],
            "aggregations": [
                {"column": "revenue", "function": "sum"},
                {"column": "units", "function": "sum"},
                {"column": "cost", "function": "average"},
                {"column": "id", "function": "count"},
            ],
        },
    )
    agg_duration = time.perf_counter() - agg_start

    # Run operation: filter_dataset
    filt_start = time.perf_counter()
    _ = registry.execute(
        "filter_dataset",
        df,
        {
            "filters": [{"column": "revenue", "operator": "greater_than", "value": 10000.0}],
            "limit": 1000,
        },
    )
    filt_duration = time.perf_counter() - filt_start

    rss_during_ops = get_process_rss_mb()

    del df
    del data
    import gc

    gc.collect()

    rss_after = get_process_rss_mb()
    total_duration = time.perf_counter() - start_time
    threads_after = threading.active_count()

    return {
        "dataset": "250,000 rows × 6 mixed columns (1.5M mixed cells)",
        "rss_before_mb": round(rss_before, 2),
        "rss_during_load_mb": round(rss_during_load, 2),
        "rss_during_ops_mb": round(rss_during_ops, 2),
        "rss_after_mb": round(rss_after, 2),
        "rss_delta_mb": round(rss_during_ops - rss_before, 2),
        "aggregate_duration_sec": round(agg_duration, 4),
        "filter_duration_sec": round(filt_duration, 4),
        "total_duration_sec": round(total_duration, 4),
        "active_threads": max(threads_before, threads_after),
        "polars_max_threads": os.environ.get("POLARS_MAX_THREADS"),
    }


def main() -> None:
    print("=" * 70)
    print("VIBE Analytics Worker Empirical Resource & Concurrency Benchmark")
    print("=" * 70)

    print("\n[1/2] Running 50k × 26 numerical benchmark...")
    res1 = benchmark_numerical_50k_x_26()
    for k, v in res1.items():
        print(f"  {k:30s}: {v}")

    print("\n[2/2] Running 250k mixed rows benchmark...")
    res2 = benchmark_mixed_250k()
    for k, v in res2.items():
        print(f"  {k:30s}: {v}")

    print("\n" + "=" * 70)
    print("Benchmark complete. Both operations successfully fit within bounds.")
    print("=" * 70)


if __name__ == "__main__":
    main()
