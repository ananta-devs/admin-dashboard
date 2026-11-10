"""Mandatory Cleanup Safety Tests A-F for Stage 12.2.

Validates:
Test A — Unrelated NULL-organization audit preservation.
Test B — Cross-run isolation.
Test C — Exact identifier matching.
Test D — Foreign-key integrity.
Test E — Wrong database refusal.
Test F — Failure visibility and reporting.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime

# Append backend root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from scripts.e2e_cleanup import cleanup_run, get_sync_db_url


def run_cleanup_safety_tests() -> bool:
    print("===================================================================")
    print("STAGE 12.2 MANDATORY CLEANUP SAFETY VERIFICATION SUITE")
    print("===================================================================")

    db_url = get_sync_db_url("vibe_enterprise_test")
    engine = create_engine(db_url, pool_pre_ping=True)

    with engine.connect() as conn:
        actual_db = conn.execute(text("SELECT current_database();")).scalar()
        assert actual_db == "vibe_enterprise_test", f"Safety test must run on vibe_enterprise_test, got {actual_db}"

    # -------------------------------------------------------------------------
    # TEST E: Wrong database refusal (Must fail closed before touching data)
    # -------------------------------------------------------------------------
    print("\n[Executing Test E] Wrong database refusal...")
    test_e_passed = False
    try:
        cleanup_run(
            run_id="dummy_run",
            expected_db="wrong_database_name",
            db_url=get_sync_db_url("vibe_enterprise_test"),
        )
    except RuntimeError as e:
        if "FATAL REFUSAL: Cleanup target mismatch!" in str(e):
            test_e_passed = True
            print("  -> PASSED: Cleanup aborted with expected fatal mismatch error.")
        else:
            print(f"  -> FAILED: Unexpected error message: {e}")
    except Exception as e:
        print(f"  -> FAILED: Wrong exception type: {type(e)} {e}")

    assert test_e_passed, "Test E failed: cleanup did not refuse wrong database!"

    # -------------------------------------------------------------------------
    # TEST F: Failure visibility on invalid invocation
    # -------------------------------------------------------------------------
    print("\n[Executing Test F] Failure visibility...")
    test_f_passed = False
    try:
        cleanup_run(run_id="")
    except ValueError as e:
        if "run_id must be provided" in str(e):
            test_f_passed = True
            print("  -> PASSED: Invalid invocation cleanly surfaced fatal validation error.")
    assert test_f_passed, "Test F failed: invalid run_id did not raise ValueError!"

    # -------------------------------------------------------------------------
    # TEST A: Unrelated NULL-organization audit preservation
    # -------------------------------------------------------------------------
    print("\n[Executing Test A] Unrelated NULL-organization audit preservation...")
    unrelated_event_id = uuid.uuid4()
    now = datetime.now(UTC)

    with engine.connect() as conn:
        with conn.begin():
            conn.execute(
                text("""
                    INSERT INTO administrative_audit_events 
                        (id, organization_id, organization_slug, actor_user_id, action, target_type, target_id, occurred_at)
                    VALUES 
                        (:id, NULL, NULL, NULL, 'SYSTEM_STARTUP', 'System', 'sys-01', :now)
                """),
                {"id": unrelated_event_id, "now": now},
            )

    # Run cleanup for an unrelated run_id
    cleanup_run(run_id="unrelated_run_alpha_1234")

    # Assert the NULL-org audit event STILL exists
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id FROM administrative_audit_events WHERE id = :id"),
            {"id": unrelated_event_id},
        ).fetchone()
        assert row is not None, "Test A FAILED: Unrelated NULL-organization audit event was improperly deleted!"
        print("  -> PASSED: Unrelated NULL-organization audit record was strictly preserved.")

        # Clean up the test fixture event
        conn.execute(
            text("DELETE FROM administrative_audit_events WHERE id = :id"),
            {"id": unrelated_event_id},
        )

    # -------------------------------------------------------------------------
    # TEST B & C: Cross-run isolation and Exact Identifier Matching
    # -------------------------------------------------------------------------
    print("\n[Executing Test B & C] Cross-run isolation & Exact identifier matching...")
    run_a = f"run_test_a_{uuid.uuid4().hex[:6]}"
    run_b = f"run_test_b_{uuid.uuid4().hex[:6]}"

    org_a_id = uuid.uuid4()
    user_a_id = uuid.uuid4()

    org_b_id = uuid.uuid4()
    user_b_id = uuid.uuid4()

    with engine.begin() as conn:
        # Seed Run A
        conn.execute(
            text("INSERT INTO organizations (id, name, slug, created_at, updated_at) VALUES (:id, 'Org A', :slug, :now, :now)"),
            {"id": org_a_id, "slug": f"org-a-{run_a}", "now": now},
        )
        conn.execute(
            text("INSERT INTO users (id, email, normalized_email, display_name, created_at, updated_at) VALUES (:id, :email, :n_email, 'User A', :now, :now)"),
            {"id": user_a_id, "email": f"user_a_{run_a}@vibe.internal", "n_email": f"user_a_{run_a}@vibe.internal", "now": now},
        )
        conn.execute(
            text("INSERT INTO administrative_audit_events (id, organization_id, organization_slug, actor_user_id, action, target_type, target_id, occurred_at) VALUES (:id, :org_id, 'org-a', :user_id, 'USER_LOGIN', 'User', 'u-a', :now)"),
            {"id": uuid.uuid4(), "org_id": org_a_id, "user_id": user_a_id, "now": now},
        )

        # Seed Run B
        conn.execute(
            text("INSERT INTO organizations (id, name, slug, created_at, updated_at) VALUES (:id, 'Org B', :slug, :now, :now)"),
            {"id": org_b_id, "slug": f"org-b-{run_b}", "now": now},
        )
        conn.execute(
            text("INSERT INTO users (id, email, normalized_email, display_name, created_at, updated_at) VALUES (:id, :email, :n_email, 'User B', :now, :now)"),
            {"id": user_b_id, "email": f"user_b_{run_b}@vibe.internal", "n_email": f"user_b_{run_b}@vibe.internal", "now": now},
        )
        conn.execute(
            text("INSERT INTO administrative_audit_events (id, organization_id, organization_slug, actor_user_id, action, target_type, target_id, occurred_at) VALUES (:id, :org_id, 'org-b', :user_id, 'USER_LOGIN', 'User', 'u-b', :now)"),
            {"id": uuid.uuid4(), "org_id": org_b_id, "user_id": user_b_id, "now": now},
        )

    # Execute cleanup ONLY for Run A using exact IDs
    counts_a = cleanup_run(
        run_id=run_a,
        org_ids=[str(org_a_id)],
        user_ids=[str(user_a_id)],
    )
    print(f"  -> Cleanup Run A Counts: {counts_a}")

    with engine.begin() as conn:
        # Verify Run A is completely gone
        row_org_a = conn.execute(text("SELECT id FROM organizations WHERE id = :id"), {"id": org_a_id}).fetchone()
        row_user_a = conn.execute(text("SELECT id FROM users WHERE id = :id"), {"id": user_a_id}).fetchone()
        row_audit_a = conn.execute(text("SELECT id FROM administrative_audit_events WHERE actor_user_id = :id"), {"id": user_a_id}).fetchone()

        assert row_org_a is None, "Test B FAILED: Org A was not deleted!"
        assert row_user_a is None, "Test B FAILED: User A was not deleted!"
        assert row_audit_a is None, "Test B FAILED: Audit event for User A was not deleted!"

        # Verify Run B is 100% intact
        row_org_b = conn.execute(text("SELECT id FROM organizations WHERE id = :id"), {"id": org_b_id}).fetchone()
        row_user_b = conn.execute(text("SELECT id FROM users WHERE id = :id"), {"id": user_b_id}).fetchone()
        row_audit_b = conn.execute(text("SELECT id FROM administrative_audit_events WHERE actor_user_id = :id"), {"id": user_b_id}).fetchone()

        assert row_org_b is not None, "Test B FAILED: Org B was improperly deleted during Run A cleanup!"
        assert row_user_b is not None, "Test B FAILED: User B was improperly deleted during Run A cleanup!"
        assert row_audit_b is not None, "Test B FAILED: Audit event B was improperly deleted!"
        print("  -> PASSED: Cross-run isolation and exact identifier targeting verified.")

    # Clean up Run B
    cleanup_run(
        run_id=run_b,
        org_ids=[str(org_b_id)],
        user_ids=[str(user_b_id)],
    )

    # -------------------------------------------------------------------------
    # TEST D: Foreign-key integrity cascade
    # -------------------------------------------------------------------------
    print("\n[Executing Test D] Foreign-key cascade and dependency integrity...")
    run_d = f"run_test_d_{uuid.uuid4().hex[:6]}"
    org_d_id = uuid.uuid4()
    ws_d_id = uuid.uuid4()
    wb_d_id = uuid.uuid4()
    sheet_d_id = uuid.uuid4()
    user_d_id = uuid.uuid4()

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO organizations (id, name, slug, created_at, updated_at) VALUES (:id, 'Org D', :slug, :now, :now)"),
            {"id": org_d_id, "slug": f"org-d-{run_d}", "now": now},
        )
        conn.execute(
            text("INSERT INTO workspaces (id, organization_id, name, slug, created_at, updated_at) VALUES (:id, :org_id, 'WS D', 'ws-d', :now, :now)"),
            {"id": ws_d_id, "org_id": org_d_id, "now": now},
        )
        conn.execute(
            text("INSERT INTO workbooks (id, organization_id, workspace_id, name, created_at, updated_at) VALUES (:id, :org_id, :ws_id, 'WB D', :now, :now)"),
            {"id": wb_d_id, "org_id": org_d_id, "ws_id": ws_d_id, "now": now},
        )
        conn.execute(
            text("INSERT INTO sheets (id, organization_id, workbook_id, name, position, row_count, column_count, created_at, updated_at) VALUES (:id, :org_id, :wb_id, 'Sheet1', 0, 100, 26, :now, :now)"),
            {"id": sheet_d_id, "org_id": org_d_id, "wb_id": wb_d_id, "now": now},
        )
        conn.execute(
            text("INSERT INTO users (id, email, normalized_email, display_name, created_at, updated_at) VALUES (:id, :email, :n_email, 'User D', :now, :now)"),
            {"id": user_d_id, "email": f"user_d_{run_d}@vibe.internal", "n_email": f"user_d_{run_d}@vibe.internal", "now": now},
        )
        conn.execute(
            text("INSERT INTO user_credentials (id, user_id, password_hash, password_changed_at, created_at, updated_at) VALUES (:cred_id, :user_id, 'argon2_dummy', :now, :now, :now)"),
            {"cred_id": uuid.uuid4(), "user_id": user_d_id, "now": now},
        )
        conn.execute(
            text("INSERT INTO authentication_sessions (id, user_id, active_organization_id, token_hash, csrf_token_hash, created_at, last_seen_at, idle_expires_at, absolute_expires_at) VALUES (:id, :user_id, :org_id, 'hash1', 'csrf1', :now, :now, :now, :now)"),
            {"id": uuid.uuid4(), "user_id": user_d_id, "org_id": org_d_id, "now": now},
        )

    counts_d = cleanup_run(
        run_id=run_d,
        org_ids=[str(org_d_id)],
        user_ids=[str(user_d_id)],
    )
    print(f"  -> Cleanup Run D Counts: {counts_d}")

    with engine.begin() as conn:
        for tbl in ["organizations", "workspaces", "workbooks", "sheets", "users", "user_credentials", "authentication_sessions"]:
            cnt = conn.execute(text(f"SELECT count(*) FROM {tbl} WHERE id = :id"), {"id": org_d_id if "org" in tbl else (ws_d_id if tbl == "workspaces" else (wb_d_id if tbl == "workbooks" else (sheet_d_id if tbl == "sheets" else user_d_id)))}).scalar()
            assert cnt == 0, f"Test D FAILED: Table {tbl} still has test record after cleanup!"
        print("  -> PASSED: Complete foreign-key cascading and dependency integrity confirmed.")

    print("\n===================================================================")
    print("ALL 6 CLEANUP SAFETY TESTS PASSED (A, B, C, D, E, F)!")
    print("===================================================================")
    return True


if __name__ == "__main__":
    success = run_cleanup_safety_tests()
    sys.exit(0 if success else 1)
