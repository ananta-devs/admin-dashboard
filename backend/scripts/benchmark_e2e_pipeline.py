# ruff: noqa: E402
"""Comprehensive Benchmark Suite for VIBE Phase 7 Analytics Subsystem.

Covers:
Category A: Analytics Execution Benchmarks (Polars vs Pandas across 10k, 50k, 100k, 250k rows;
            all 6 operations; snapshot extraction; serialization overhead; atomic rename).
Category B: End-to-End Pipeline Latency (50 full lifecycle runs, percentiles, resource consumption).
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import platform
import sys
import time
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any

# Ensure backend directory is in sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

import numpy as np
import pandas as pd
import polars as pl

from app.analytics.registry import registry
from app.analytics.storage import AnalyticsStorageService
from app.db.session import async_session_maker
from app.models.analytics import AnalyticsJob, AnalyticsJobStatus, AnalyticsOutboxEvent
from app.models.spreadsheet import Sheet
from app.repositories.analytics import (
    AnalyticsJobRepository,
)
from app.tasks.analytics import _execute_analytics_job_async


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def get_current_rss_mb() -> float:
    if sys.platform == "win32":
        try:
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            handle = ctypes.windll.kernel32.OpenProcess(0x0410, False, os.getpid())
            if handle:
                if ctypes.windll.psapi.GetProcessMemoryInfo(
                    handle, ctypes.byref(counters), counters.cb
                ):
                    ctypes.windll.kernel32.CloseHandle(handle)
                    return float(counters.WorkingSetSize) / (1024 * 1024)
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass
    return 0.0


def generate_benchmark_dataframe(n_rows: int) -> tuple[pl.DataFrame, pd.DataFrame]:
    """Generates synthetic dataset of given row size with mixed numeric, categorical, and float columns."""
    rng = np.random.default_rng(42)
    categories = np.array(["Retail", "Wholesale", "Online", "Direct", "Enterprise", "Government"])
    regions = np.array(["North", "South", "East", "West", "Central"])

    cat_col = rng.choice(categories, size=n_rows)
    reg_col = rng.choice(regions, size=n_rows)
    amount_col = rng.uniform(10.0, 5000.0, size=n_rows).round(2)
    qty_col = rng.integers(1, 100, size=n_rows)
    margin_col = rng.normal(0.25, 0.08, size=n_rows).round(4)
    discount_col = rng.uniform(0.0, 0.35, size=n_rows).round(2)

    pl_df = pl.DataFrame(
        {
            "Category": cat_col,
            "Region": reg_col,
            "Amount": amount_col,
            "Quantity": qty_col,
            "Margin": margin_col,
            "Discount": discount_col,
        }
    )

    pd_df = pd.DataFrame(
        {
            "Category": cat_col,
            "Region": reg_col,
            "Amount": amount_col,
            "Quantity": qty_col,
            "Margin": margin_col,
            "Discount": discount_col,
        }
    )

    return pl_df, pd_df


def benchmark_polars_operation(op_name: str, pl_df: pl.DataFrame, n_rows: int) -> dict[str, Any]:
    """Runs a registered operation on Polars and measures wall clock & memory."""
    mem_before = get_current_rss_mb()
    t0 = time.perf_counter()

    if op_name == "describe_columns":
        registry.execute("describe_columns", pl_df, {"columns": ["Amount", "Quantity", "Margin"]})
    elif op_name == "profile_dataset":
        registry.execute("profile_dataset", pl_df, {})
    elif op_name == "correlation_matrix":
        registry.execute(
            "correlation_matrix", pl_df, {"columns": ["Amount", "Quantity", "Margin", "Discount"]}
        )
    elif op_name == "group_by_aggregate":
        registry.execute(
            "group_by_aggregate",
            pl_df,
            {
                "group_by": ["Region", "Category"],
                "aggregations": [
                    {"column": "Amount", "function": "sum"},
                    {"column": "Quantity", "function": "average"},
                    {"column": "Margin", "function": "average"},
                ],
            },
        )
    elif op_name == "filter_dataset":
        registry.execute(
            "filter_dataset",
            pl_df,
            {
                "filters": [
                    {"column": "Amount", "operator": "greater_than_or_equal", "value": 500.0},
                    {"column": "Region", "operator": "equals", "value": "North"},
                ]
            },
        )
    elif op_name == "data_quality_profile":
        registry.execute("data_quality_profile", pl_df, {})
    else:
        raise ValueError(f"Unknown op {op_name}")

    t1 = time.perf_counter()
    mem_after = get_current_rss_mb()

    return {
        "engine": "Polars",
        "operation": op_name,
        "rows": n_rows,
        "duration_sec": round(t1 - t0, 5),
        "peak_mem_mb": round(max(mem_after, mem_before), 2),
        "delta_mem_mb": round(max(0.0, mem_after - mem_before), 2),
    }


def benchmark_pandas_operation(op_name: str, pd_df: pd.DataFrame, n_rows: int) -> dict[str, Any]:
    """Equivalent operations using pure Pandas to evaluate baseline vs Polars."""
    mem_before = get_current_rss_mb()
    t0 = time.perf_counter()

    if op_name == "describe_columns":
        _ = pd_df[["Amount", "Quantity", "Margin"]].describe().to_dict()
    elif op_name == "profile_dataset":
        _ = {
            "n_rows": len(pd_df),
            "n_cols": len(pd_df.columns),
            "dtypes": {c: str(pd_df[c].dtype) for c in pd_df.columns},
            "null_counts": pd_df.isnull().sum().to_dict(),
        }
    elif op_name == "correlation_matrix":
        _ = pd_df[["Amount", "Quantity", "Margin", "Discount"]].corr().to_dict()
    elif op_name == "group_by_aggregate":
        _ = (
            pd_df.groupby(["Region", "Category"])
            .agg(
                {
                    "Amount": "sum",
                    "Quantity": "mean",
                    "Margin": "mean",
                }
            )
            .reset_index()
        )
    elif op_name == "filter_dataset":
        _ = pd_df[(pd_df["Amount"] >= 500.0) & (pd_df["Region"] == "North")]
    elif op_name == "data_quality_profile":
        _ = {
            "row_count": len(pd_df),
            "duplicate_count": int(pd_df.duplicated().sum()),
            "null_rates": (pd_df.isnull().sum() / len(pd_df)).to_dict(),
        }
    else:
        raise ValueError(f"Unknown op {op_name}")

    t1 = time.perf_counter()
    mem_after = get_current_rss_mb()

    return {
        "engine": "Pandas",
        "operation": op_name,
        "rows": n_rows,
        "duration_sec": round(t1 - t0, 5),
        "peak_mem_mb": round(max(mem_after, mem_before), 2),
        "delta_mem_mb": round(max(0.0, mem_after - mem_before), 2),
    }


def benchmark_serialization_and_atomic_io(pl_df: pl.DataFrame, org_id: uuid.UUID) -> dict[str, Any]:
    """Measures Parquet write vs JSON serialization and atomic rename latency."""
    storage = AnalyticsStorageService()
    job_id = uuid.uuid4()
    result_id = uuid.uuid4()

    # 1. Parquet write
    t0 = time.perf_counter()
    temp_parquet = storage.get_result_artifact_path(
        org_id, job_id, result_id, claim_generation=1, ext="tmp"
    )
    pl_df.write_parquet(temp_parquet, compression="zstd")
    t1 = time.perf_counter()
    parquet_write_sec = t1 - t0
    parquet_size_bytes = temp_parquet.stat().st_size

    # 2. JSON serialization
    t0 = time.perf_counter()
    json_bytes = json.dumps(pl_df.to_dicts()).encode("utf-8")
    t1 = time.perf_counter()
    json_serialize_sec = t1 - t0
    json_size_bytes = len(json_bytes)

    # 3. Atomic rename with fsync
    final_parquet = storage.get_result_artifact_path(
        org_id, job_id, result_id, claim_generation=1, ext="parquet"
    )
    t0 = time.perf_counter()
    storage.atomic_promote(temp_parquet, final_parquet)
    t1 = time.perf_counter()
    atomic_rename_sec = t1 - t0

    # Cleanup
    if final_parquet.is_file():
        storage.delete_artifact(final_parquet)

    return {
        "parquet_write_sec": round(parquet_write_sec, 5),
        "parquet_size_bytes": parquet_size_bytes,
        "parquet_size_mb": round(parquet_size_bytes / (1024 * 1024), 3),
        "json_serialize_sec": round(json_serialize_sec, 5),
        "json_size_bytes": json_size_bytes,
        "json_size_mb": round(json_size_bytes / (1024 * 1024), 3),
        "parquet_compression_ratio": round(json_size_bytes / max(1, parquet_size_bytes), 2),
        "atomic_rename_sec": round(atomic_rename_sec, 5),
        "atomic_rename_ms": round(atomic_rename_sec * 1000, 3),
    }


async def run_end_to_end_pipeline_benchmark(n_runs: int = 50) -> dict[str, Any]:
    """Runs n_runs complete end-to-end analytics job pipelines and collects timing metrics."""
    # Find existing test org/workspace/workbook/sheet
    async with async_session_maker() as session:
        from sqlalchemy import select

        res = await session.execute(select(Sheet).limit(1))
        sheet = res.scalar_one_or_none()
        if not sheet:
            raise RuntimeError("No sheets found in DB. Run integration tests first.")
        sheet_id = sheet.id
        wb_id = sheet.workbook_id

        from app.models.spreadsheet import Workbook

        wb = (await session.execute(select(Workbook).where(Workbook.id == wb_id))).scalar_one()
        org_id = wb.organization_id
        ws_id = wb.workspace_id
        user_id = wb.created_by_user_id

    timings = {
        "total_latency_ms": [],
        "job_creation_ms": [],
        "execution_async_ms": [],
        "db_commit_ms": [],
    }

    cpu_measurements = []
    mem_measurements = []

    print(f"Executing {n_runs} End-to-End Pipeline Iterations against Sheet {sheet_id}...")

    for _i in range(n_runs):
        t_start = time.perf_counter()

        # Step 1: API/Service Job Creation & Outbox Insertion
        t_create_start = time.perf_counter()
        async with async_session_maker() as session:
            job = AnalyticsJob(
                id=uuid.uuid4(),
                organization_id=org_id,
                workspace_id=ws_id,
                source_workbook_id=wb_id,
                source_sheet_id=sheet_id,
                requested_by_user_id=user_id,
                operation_id="describe_columns",
                parameters={"columns": ["A"]},
                status=AnalyticsJobStatus.QUEUED,
                attempt_count=0,
                max_attempts=3,
                source_sheet_version=1,
            )
            session.add(job)
            outbox = AnalyticsOutboxEvent(
                job_id=job.id,
                organization_id=org_id,
                event_type="analytics.job.created",
                payload={"job_id": str(job.id)},
            )
            session.add(outbox)
            await session.commit()
            job_id = job.id
        t_create_end = time.perf_counter()
        timings["job_creation_ms"].append((t_create_end - t_create_start) * 1000)

        # Step 2: Worker Execution (_execute_analytics_job_async)
        t_exec_start = time.perf_counter()
        task_id = f"bench-task-{uuid.uuid4().hex[:8]}"
        _res_status = await _execute_analytics_job_async(str(job_id), task_id)
        t_exec_end = time.perf_counter()
        timings["execution_async_ms"].append((t_exec_end - t_exec_start) * 1000)

        # Step 3: Verify Status & Result
        t_db_start = time.perf_counter()
        async with async_session_maker() as session:
            job_repo = AnalyticsJobRepository(session)
            done_job = await job_repo.get_by_id(job_id)
            assert done_job.status == AnalyticsJobStatus.SUCCEEDED
        t_db_end = time.perf_counter()
        timings["db_commit_ms"].append((t_db_end - t_db_start) * 1000)

        t_total = (time.perf_counter() - t_start) * 1000
        timings["total_latency_ms"].append(t_total)

        cpu_measurements.append(0.0)
        mem_measurements.append(get_current_rss_mb())

    def calc_stats(series: list[float]) -> dict[str, float]:
        arr = np.array(series)
        return {
            "p50": round(float(np.percentile(arr, 50)), 2),
            "p90": round(float(np.percentile(arr, 90)), 2),
            "p95": round(float(np.percentile(arr, 95)), 2),
            "p99": round(float(np.percentile(arr, 99)), 2),
            "min": round(float(np.min(arr)), 2),
            "max": round(float(np.max(arr)), 2),
            "mean": round(float(np.mean(arr)), 2),
            "std": round(float(np.std(arr)), 2),
        }

    return {
        "iterations": n_runs,
        "total_latency_stats_ms": calc_stats(timings["total_latency_ms"]),
        "job_creation_stats_ms": calc_stats(timings["job_creation_ms"]),
        "worker_exec_stats_ms": calc_stats(timings["execution_async_ms"]),
        "db_commit_query_stats_ms": calc_stats(timings["db_commit_ms"]),
        "worker_rss_mb": {
            "mean": round(float(np.mean(mem_measurements)), 1),
            "max": round(float(np.max(mem_measurements)), 1),
        },
    }


async def main() -> None:
    print("=" * 80)
    print("VIBE PHASE 7 COMPREHENSIVE BENCHMARK SUITE")
    print("=" * 80)

    system_info = {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "cpu": platform.processor(),
        "cores_logical": os.cpu_count() or 1,
        "python_version": platform.python_version(),
        "polars_version": pl.__version__,
        "pandas_version": pd.__version__,
        "numpy_version": np.__version__,
    }
    print("System Configuration:")
    for k, v in system_info.items():
        print(f"  {k}: {v}")

    # Category A: Analytics Execution Benchmarks
    print("\n" + "-" * 80)
    print("CATEGORY A: ANALYTICS EXECUTION BENCHMARKS (Polars vs Pandas across row scales)")
    print("-" * 80)

    row_scales = [10_000, 50_000, 100_000, 250_000]
    operations = [
        "describe_columns",
        "profile_dataset",
        "correlation_matrix",
        "group_by_aggregate",
        "filter_dataset",
        "data_quality_profile",
    ]

    exec_results = []
    serialization_results = []

    org_id = uuid.uuid4()

    for n_rows in row_scales:
        print(f"\n--- Generating synthetic dataset of {n_rows:,} rows ---")
        pl_df, pd_df = generate_benchmark_dataframe(n_rows)

        # Serialization & atomic I/O benchmark
        ser_metrics = benchmark_serialization_and_atomic_io(pl_df, org_id)
        ser_metrics["rows"] = n_rows
        serialization_results.append(ser_metrics)

        for op in operations:
            polars_res = benchmark_polars_operation(op, pl_df, n_rows)
            pandas_res = benchmark_pandas_operation(op, pd_df, n_rows)
            speedup = round(
                pandas_res["duration_sec"] / max(0.00001, polars_res["duration_sec"]), 2
            )

            combined = {
                "operation": op,
                "rows": n_rows,
                "polars_time_sec": polars_res["duration_sec"],
                "polars_mem_mb": polars_res["peak_mem_mb"],
                "pandas_time_sec": pandas_res["duration_sec"],
                "pandas_mem_mb": pandas_res["peak_mem_mb"],
                "speedup_factor": speedup,
            }
            exec_results.append(combined)
            print(
                f"  [{op:<22}] {n_rows:>7,} rows | Polars: {polars_res['duration_sec']:>7.4f}s | Pandas: {pandas_res['duration_sec']:>7.4f}s | Speedup: {speedup:>5.1f}x"
            )

    # Category B: End-to-End Pipeline Latency
    print("\n" + "-" * 80)
    print("CATEGORY B: END-TO-END PIPELINE LATENCY (50 iterations)")
    print("-" * 80)
    pipeline_results = await run_end_to_end_pipeline_benchmark(n_runs=50)

    # Save benchmark outputs
    output_data = {
        "system_info": system_info,
        "category_a_execution": exec_results,
        "category_a_serialization_io": serialization_results,
        "category_b_pipeline": pipeline_results,
    }

    out_json_path = backend_dir / "benchmark_e2e_results.json"
    with open(out_json_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)

    print(f"\nBenchmark suite completed successfully. Results saved to {out_json_path}")


if __name__ == "__main__":
    asyncio.run(main())
