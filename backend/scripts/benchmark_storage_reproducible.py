"""Reproducible benchmark script for VIBE Spreadsheet Storage Architecture (ADR 001).

Measures:
1. Viewport range retrieval (100 rows from 50,000-row sheet):
   - Cold cache latency (1st run)
   - Warm cache latencies across 30+ repetitions: min, median, p95, max
2. PostgreSQL EXPLAIN (ANALYZE, BUFFERS) execution plan.
3. Storage breakdown: Table heap size, Index size, TOAST table size, and Total relation size.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg

from app.core.config import settings


async def run_benchmark() -> None:
    # Extract connection parameters from settings.DATABASE_URL
    db_url = settings.DATABASE_URL
    # Format: postgresql+asyncpg://user:pass@host:port/dbname
    raw_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_url)

    print("=" * 70)
    print("VIBE SPREADSHEET STORAGE ARCHITECTURE BENCHMARK (ADR 001)")
    print("=" * 70)

    try:
        # 1. Setup tenant & sheet
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        ws_id = uuid.uuid4()
        wb_id = uuid.uuid4()
        sheet_id = uuid.uuid4()

        await conn.execute(
            """
            INSERT INTO organizations (id, name, slug, created_at, updated_at)
            VALUES ($1, 'Benchmark Org', $2, NOW(), NOW())
            """,
            org_id,
            f"bench-{org_id.hex[:8]}",
        )
        await conn.execute(
            """
            INSERT INTO users (id, email, normalized_email, display_name, created_at, updated_at)
            VALUES ($1, $2, $2, 'Bench User', NOW(), NOW())
            """,
            user_id,
            f"bench-{org_id.hex[:8]}@vibe.io",
        )
        await conn.execute(
            """
            INSERT INTO organization_memberships (id, organization_id, user_id, created_at)
            VALUES ($1, $2, $3, NOW())
            """,
            uuid.uuid4(),
            org_id,
            user_id,
        )
        await conn.execute(
            """
            INSERT INTO workspaces (id, organization_id, name, slug, created_at, updated_at)
            VALUES ($1, $2, 'Bench WS', $3, NOW(), NOW())
            """,
            ws_id,
            org_id,
            f"ws-{org_id.hex[:8]}",
        )
        await conn.execute(
            """
            INSERT INTO workbooks (id, organization_id, workspace_id, created_by_user_id, name, version, created_at, updated_at)
            VALUES ($1, $2, $3, $4, '50k Benchmark Workbook', 1, NOW(), NOW())
            """,
            wb_id,
            org_id,
            ws_id,
            user_id,
        )
        await conn.execute(
            """
            INSERT INTO sheets (id, workbook_id, organization_id, name, position, row_count, column_count, version, created_at, updated_at)
            VALUES ($1, $2, $3, 'Sheet 50k', 0, 50000, 20, 1, NOW(), NOW())
            """,
            sheet_id,
            wb_id,
            org_id,
        )

        print(f"Created Sheet {sheet_id} in Org {org_id}")

        # 2. Bulk populate 50,000 rows with 5 cells each
        print("Populating 50,000 rows (250,000 cell records in JSONB)...")
        t0_pop = time.perf_counter()

        rows_data = []
        for r in range(50000):
            row_id = uuid.uuid4()
            cells = {
                "0": {"v": r, "c": r, "f": None, "t": "number", "err": None, "ver": 1},
                "1": {"v": r * 2.5, "c": r * 2.5, "f": None, "t": "number", "err": None, "ver": 1},
                "2": {
                    "v": f"Label {r}",
                    "c": f"Label {r}",
                    "f": None,
                    "t": "string",
                    "err": None,
                    "ver": 1,
                },
                "3": {
                    "v": r % 2 == 0,
                    "c": r % 2 == 0,
                    "f": None,
                    "t": "boolean",
                    "err": None,
                    "ver": 1,
                },
                "4": {
                    "v": None,
                    "c": r * 3.5,
                    "f": f"=A{r + 1}+B{r + 1}",
                    "t": "number",
                    "err": None,
                    "ver": 1,
                },
            }
            rows_data.append((row_id, sheet_id, org_id, r, json.dumps(cells), 1))

        # Copy in batches
        await conn.copy_records_to_table(
            "sheet_rows",
            records=rows_data,
            columns=["id", "sheet_id", "organization_id", "row_index", "cells", "version"],
        )
        pop_time = time.perf_counter() - t0_pop
        print(f"Population completed in {pop_time:.3f} s ({50000 / pop_time:.0f} rows/s)")

        # Analyze table to update statistics
        await conn.execute("ANALYZE sheet_rows;")

        # 3. Storage Footprint Measurements
        print("\n--- PostgreSQL Storage Footprint ---")
        storage_query = """
            SELECT
                pg_size_pretty(pg_relation_size('sheet_rows')) AS table_size,
                pg_size_pretty(pg_indexes_size('sheet_rows')) AS indexes_size,
                pg_size_pretty(pg_total_relation_size('sheet_rows')) AS total_size,
                pg_size_pretty(pg_relation_size(c.reltoastrelid)) AS toast_size,
                pg_total_relation_size('sheet_rows') AS total_bytes
            FROM pg_class c
            WHERE c.relname = 'sheet_rows';
        """
        row_storage = await conn.fetchrow(storage_query)
        print(f"Table Heap Size : {row_storage['table_size']}")
        print(f"Indexes Size    : {row_storage['indexes_size']}")
        print(f"TOAST Size      : {row_storage['toast_size']}")
        print(
            f"Total Size      : {row_storage['total_size']} ({row_storage['total_bytes']:,} bytes)"
        )

        # 4. Viewport Query Benchmark: 100 contiguous rows (row_index BETWEEN 25000 AND 25099)
        query = """
            SELECT id, sheet_id, row_index, cells, version
            FROM sheet_rows
            WHERE sheet_id = $1 AND row_index >= 25000 AND row_index < 25100
            ORDER BY row_index ASC;
        """

        # Explain (Analyze, Buffers)
        explain_query = f"""
            EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
            SELECT id, sheet_id, row_index, cells, version
            FROM sheet_rows
            WHERE sheet_id = '{sheet_id}' AND row_index >= 25000 AND row_index < 25100
            ORDER BY row_index ASC;
        """
        explain_lines = await conn.fetch(explain_query)
        explain_output = "\n".join(r[0] for r in explain_lines)
        print("\n--- EXPLAIN (ANALYZE, BUFFERS) ---")
        print(explain_output)

        # Repetition benchmark: 35 repetitions (1 cold + 34 warm)
        print("\n--- Viewport Query Repetition Latencies (35 reps) ---")
        latencies: list[float] = []
        for _i in range(35):
            t0 = time.perf_counter()
            rows = await conn.fetch(query, sheet_id)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies.append(elapsed_ms)
            assert len(rows) == 100

        cold_latency = latencies[0]
        warm_latencies = latencies[1:]
        med_latency = statistics.median(warm_latencies)
        warm_latencies_sorted = sorted(warm_latencies)
        p95_idx = int(0.95 * len(warm_latencies_sorted))
        p95_latency = warm_latencies_sorted[p95_idx]
        min_latency = min(warm_latencies)
        max_latency = max(warm_latencies)

        print(f"Cold Cache Latency : {cold_latency:.2f} ms")
        print(f"Warm Cache Latencies ({len(warm_latencies)} reps):")
        print(f"  Min    : {min_latency:.2f} ms")
        print(f"  Median : {med_latency:.2f} ms")
        print(f"  p95    : {p95_latency:.2f} ms")
        print(f"  Max    : {max_latency:.2f} ms")

        # Clean up benchmark data
        print("\nCleaning up benchmark data...")
        await conn.execute("DELETE FROM sheets WHERE id = $1", sheet_id)
        await conn.execute("DELETE FROM workbooks WHERE id = $1", wb_id)
        await conn.execute("DELETE FROM workspaces WHERE id = $1", ws_id)
        await conn.execute(
            "DELETE FROM organization_memberships WHERE organization_id = $1", org_id
        )
        await conn.execute("DELETE FROM users WHERE id = $1", user_id)
        await conn.execute("DELETE FROM organizations WHERE id = $1", org_id)
        print("Cleanup complete.")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_benchmark())
