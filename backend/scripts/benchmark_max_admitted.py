"""Benchmark maximum-admitted workload of 500,000 cells (50,000 rows x 10 cols)."""

import os
import time
from pathlib import Path

import polars as pl

from app.analytics.registry import registry
from app.core.config import settings

print("=== MAXIMUM ADMITTED WORKLOAD BENCHMARK (500,000 CELLS) ===")
print(f"ANALYTICS_MAX_NON_EMPTY_CELLS: {settings.ANALYTICS_MAX_NON_EMPTY_CELLS:,}")
print(f"ANALYTICS_MAX_SOURCE_ROWS:     {settings.ANALYTICS_MAX_SOURCE_ROWS:,}")
print(f"ANALYTICS_MAX_SOURCE_COLS:     {settings.ANALYTICS_MAX_SOURCE_COLS:,}")

# Generate 50,000 rows x 10 cols = 500,000 cells
t0 = time.perf_counter()
data = {f"col_{i}": [float(r * 10 + i) for r in range(50_000)] for i in range(10)}
df = pl.DataFrame(data)
t_gen = time.perf_counter() - t0

# Write temporary Parquet snapshot
snap_path = Path("/app/uploads/analytics/bench_500k.parquet") if os.path.exists("/app/uploads/analytics") else Path("uploads/analytics/bench_500k.parquet")
snap_path.parent.mkdir(parents=True, exist_ok=True)

t1 = time.perf_counter()
df.write_parquet(snap_path)
t_snap = time.perf_counter() - t1
file_size = snap_path.stat().st_size

# Execute analytics operations
t2 = time.perf_counter()
read_df = pl.read_parquet(snap_path)
res_describe = registry.execute("describe_columns", read_df, {"columns": ["col_0", "col_1", "col_2"]})
t_describe = time.perf_counter() - t2

t3 = time.perf_counter()
res_profile = registry.execute("profile_dataset", read_df, {})
t_profile = time.perf_counter() - t3

# Clean up benchmark file
if snap_path.is_file():
    snap_path.unlink()

# Inspect cgroup memory if running in container
cgroup_mem_current = None
cgroup_mem_peak = None
if os.path.exists("/sys/fs/cgroup/memory.current"):
    with open("/sys/fs/cgroup/memory.current") as f:
        cgroup_mem_current = int(f.read().strip())
if os.path.exists("/sys/fs/cgroup/memory.peak"):
    with open("/sys/fs/cgroup/memory.peak") as f:
        cgroup_mem_peak = int(f.read().strip())

print(f"Dataset generation time:        {t_gen:.3f} s")
print(f"Parquet snapshot write time:    {t_snap:.3f} s")
print(f"Parquet snapshot size on disk:  {file_size:,} bytes ({file_size / 1024 / 1024:.2f} MB)")
print(f"Describe columns exec time:     {t_describe:.3f} s")
print(f"Profile dataset exec time:      {t_profile:.3f} s")
if cgroup_mem_current is not None:
    print(f"cgroup memory.current:          {cgroup_mem_current:,} bytes ({cgroup_mem_current / 1024 / 1024:.2f} MB)")
if cgroup_mem_peak is not None:
    print(f"cgroup memory.peak:             {cgroup_mem_peak:,} bytes ({cgroup_mem_peak / 1024 / 1024:.2f} MB)")
print("=== BENCHMARK COMPLETE ===")

