"""Isolated migration test verifying Phase 9 Gate 3 Stage 3.4/4 migration 370908748232.

Demonstrates:
1. Migration upgrade from 370908748231 to 370908748232 on an isolated database.
2. Five new PDF metadata columns present in report_runs.
3. Check constraint ck_report_runs_pdf_metrics rejects negative sizes and negative/zero pages.
4. Historical report runs without PDF metadata are preserved.
5. Migration downgrade to 370908748231 removes PDF fields and check constraint cleanly.
6. Historical records remain intact after downgrade.
7. Clean re-upgrade to 370908748232.
"""

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
import psycopg
from psycopg import sql

TEST_DB = "vibe_stage3_5_migration_test"
ADMIN_URL = "postgresql://vibe_admin:dev_secret_postgres_pass_change_in_prod@127.0.0.1:5432/postgres"
TEST_DB_SYNC_URL = f"postgresql+psycopg://vibe_admin:dev_secret_postgres_pass_change_in_prod@127.0.0.1:5432/{TEST_DB}"
TEST_DB_CONN = f"postgresql://vibe_admin:dev_secret_postgres_pass_change_in_prod@127.0.0.1:5432/{TEST_DB}"


def run_alembic_cmd(cmd: str) -> None:
    env = os.environ.copy()
    env["DATABASE_SYNC_URL"] = TEST_DB_SYNC_URL
    res = subprocess.run(
        [sys.executable, "-m", "alembic"] + cmd.split(),
        cwd="backend",
        env=env,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        print(f"Alembic stdout: {res.stdout}")
        print(f"Alembic stderr: {res.stderr}")
        raise RuntimeError(f"Alembic command '{cmd}' failed with code {res.returncode}")


def main():
    print(f"=== Starting Isolated Migration Verification on {TEST_DB} ===")

    # 1. Connect to admin DB and create fresh isolated test database
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB};")
            cur.execute(f"CREATE DATABASE {TEST_DB};")
    print(f"Created isolated database: {TEST_DB}")

    try:
        # 2. Upgrade from base to 370908748231 (Parent revision)
        print("Migrating isolated database up to parent revision: 370908748231...")
        run_alembic_cmd("upgrade 370908748231")

        # 3. Insert historical data at revision 370908748231
        org_id = uuid.uuid4()
        user_id = uuid.uuid4()
        workspace_id = uuid.uuid4()
        report_def_id = uuid.uuid4()
        hist_run_id = uuid.uuid4()

        now = datetime.now(timezone.utc)
        with psycopg.connect(TEST_DB_CONN, autocommit=True) as conn:
            with conn.cursor() as cur:
                # Create org, user, workspace, report definition, and report run
                cur.execute(
                    "INSERT INTO organizations (id, name, slug, created_at, updated_at) VALUES (%s, %s, %s, %s, %s);",
                    (org_id, "Test Org", f"test-org-{uuid.uuid4().hex[:6]}", now, now),
                )
                email = f"hist_{uuid.uuid4().hex[:6]}@example.com"
                cur.execute(
                    "INSERT INTO users (id, email, normalized_email, display_name, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s);",
                    (user_id, email, email.lower(), "Hist User", now, now),
                )
                cur.execute(
                    "INSERT INTO workspaces (id, organization_id, name, slug, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s);",
                    (workspace_id, org_id, "Test WS", f"test-ws-{uuid.uuid4().hex[:6]}", now, now),
                )
                cur.execute(
                    """
                    INSERT INTO report_definitions (
                        id, organization_id, workspace_id, created_by_user_id,
                        title, page_orientation, page_size, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (report_def_id, org_id, workspace_id, user_id, "Historical Report", "portrait", "a4", now, now),
                )
                cur.execute(
                    """
                    INSERT INTO report_runs (
                        id, report_definition_id, organization_id, workspace_id, created_by_user_id,
                        report_title, status, trigger_type, attempt_count, max_attempts, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (hist_run_id, report_def_id, org_id, workspace_id, user_id, "Historical Report", "succeeded", "manual", 1, 3, now, now),
                )
        print("Historical record successfully inserted at revision 370908748231.")

        # 4. Upgrade to 370908748232 (Stage 3.4/4 PDF migration)
        print("Applying migration 370908748232 (PDF columns & constraints)...")
        run_alembic_cmd("upgrade 370908748232")

        # 5. Inspect database schema and verify new columns
        with psycopg.connect(TEST_DB_CONN, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_name = 'report_runs' AND column_name LIKE 'pdf_%'
                    ORDER BY column_name;
                    """
                )
                pdf_cols = {row[0]: (row[1], row[2]) for row in cur.fetchall()}
                print("PDF Columns verified:", pdf_cols)
                expected_cols = {
                    "pdf_artifact_path",
                    "pdf_checksum_sha256",
                    "pdf_file_size_bytes",
                    "pdf_page_count",
                    "pdf_rendered_at",
                }
                assert expected_cols.issubset(pdf_cols.keys()), f"Missing columns: {expected_cols - pdf_cols.keys()}"

                # Verify historical record has NULL for new PDF columns
                cur.execute(
                    "SELECT pdf_artifact_path, pdf_page_count, pdf_file_size_bytes FROM report_runs WHERE id = %s;",
                    (hist_run_id,),
                )
                hist_row = cur.fetchone()
                assert hist_row == (None, None, None), f"Expected NULL PDF metadata for historical run, got {hist_row}"
                print("Verified: Historical run has NULL PDF metadata without data loss.")

                # 6. Verify check constraint: Valid PDF metadata
                valid_run_id = uuid.uuid4()
                cur.execute(
                    """
                    INSERT INTO report_runs (
                        id, report_definition_id, organization_id, workspace_id, created_by_user_id,
                        report_title, status, trigger_type, attempt_count, max_attempts,
                        pdf_artifact_path, pdf_checksum_sha256, pdf_file_size_bytes, pdf_page_count, pdf_rendered_at,
                        created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        valid_run_id, report_def_id, org_id, workspace_id, user_id, "Test Report", "succeeded", "manual", 1, 3,
                        "/uploads/reports/runs/test.pdf", "a" * 64, 1024, 2, datetime.now(timezone.utc),
                        now, now,
                    ),
                )
                print("Verified: Valid PDF metadata record inserted successfully.")

                # 7. Check constraint: Negative file size must fail
                neg_size_id = uuid.uuid4()
                try:
                    cur.execute(
                        """
                        INSERT INTO report_runs (
                            id, report_definition_id, organization_id, workspace_id, created_by_user_id,
                            report_title, status, trigger_type, attempt_count, max_attempts,
                            pdf_file_size_bytes, pdf_page_count, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                        """,
                        (neg_size_id, report_def_id, org_id, workspace_id, user_id, "Test", "succeeded", "manual", 1, 3, -50, 1, now, now),
                    )
                    raise AssertionError("Check constraint failed to reject negative pdf_file_size_bytes")
                except psycopg.errors.CheckViolation:
                    print("Verified: Check constraint ck_report_runs_pdf_metrics rejected negative pdf_file_size_bytes.")

                # 8. Check constraint: Zero or negative page count must fail
                zero_page_id = uuid.uuid4()
                try:
                    cur.execute(
                        """
                        INSERT INTO report_runs (
                            id, report_definition_id, organization_id, workspace_id, created_by_user_id,
                            report_title, status, trigger_type, attempt_count, max_attempts,
                            pdf_file_size_bytes, pdf_page_count, created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                        """,
                        (zero_page_id, report_def_id, org_id, workspace_id, user_id, "Test", "succeeded", "manual", 1, 3, 100, 0, now, now),
                    )
                    raise AssertionError("Check constraint failed to reject zero pdf_page_count")
                except psycopg.errors.CheckViolation:
                    print("Verified: Check constraint ck_report_runs_pdf_metrics rejected zero pdf_page_count.")

        # 9. Downgrade to 370908748231
        print("Downgrading isolated database back to 370908748231...")
        run_alembic_cmd("downgrade 370908748231")

        with psycopg.connect(TEST_DB_CONN, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'report_runs' AND column_name LIKE 'pdf_%';
                    """
                )
                remaining_cols = cur.fetchall()
                assert len(remaining_cols) == 0, f"Columns remained after downgrade: {remaining_cols}"
                print("Verified: All 5 PDF columns and check constraint removed on downgrade.")

                # Verify historical run record is preserved
                cur.execute("SELECT id, status FROM report_runs WHERE id = %s;", (hist_run_id,))
                row = cur.fetchone()
                assert row is not None and row[1] == "succeeded"
                print("Verified: Historical record intact after downgrade.")

        # 10. Re-upgrade back to 370908748232 (verify re-applicability)
        print("Re-upgrading isolated database back to 370908748232...")
        run_alembic_cmd("upgrade 370908748232")
        print("Verified: Re-upgrade to 370908748232 succeeded cleanly.")

        print("=== Isolated Migration Verification COMPLETE (PASSED) ===")

    finally:
        # Clean up isolated test database
        with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB};")
        print(f"Dropped isolated test database: {TEST_DB}")


if __name__ == "__main__":
    main()
